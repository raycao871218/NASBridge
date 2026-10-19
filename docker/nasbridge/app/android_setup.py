"""Encrypted setup profiles and local QR generation; configured URLs are never fetched."""
from __future__ import annotations

import io
import base64
import json
import re
import os
import tempfile
import threading
import zlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, ValidationError, field_validator
from qrcode.exceptions import DataOverflowError

router = APIRouter(prefix="/api/android-setup", tags=["android-setup"])
_profile_lock = threading.RLock()


def service_url(value: str, https_only=False, default_scheme="http"):
    value = value.strip()
    if "://" not in value:
        value = default_scheme + ":" + (value if value.startswith("//") else "//" + value)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in (("https",) if https_only else ("http", "https"))
                or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or "?" in value or "#" in value or parsed.port == 0
                or re.search(r"[\s\x00-\x1f\x7f]", value)):
            raise ValueError()
    except ValueError:
        raise ValueError("地址格式无效") from None
    return value.rstrip("/") + "/"


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ssid: str = Field(default="", max_length=128)
    local_url: str = Field(min_length=1, max_length=512)
    public_url: str = Field(min_length=1, max_length=512)

    @field_validator("ssid")
    @classmethod
    def wifi(cls, value):
        if len(value.encode("utf-8")) > 32 or value == "<unknown ssid>" or any(ord(c) < 32 for c in value):
            raise ValueError("Wi-Fi 名称无效")
        return value

    @field_validator("local_url", "public_url")
    @classmethod
    def address(cls, value, info):
        return service_url(value, default_scheme="http" if info.field_name == "local_url" else "https")


class Presence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=512)
    entity: str = Field(pattern=r"^input_boolean\.[a-z0-9_]+$", max_length=160)
    token: SecretStr = Field(min_length=1, max_length=1024)
    test_mode: StrictBool = False

    @field_validator("url")
    @classmethod
    def address(cls, value):
        return service_url(value, https_only=True, default_scheme="https")

    @field_validator("token")
    @classmethod
    def secret(cls, value):
        if re.search(r"[\s\x00-\x1f\x7f]", value.get_secret_value()):
            raise ValueError("令牌无效")
        return value


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    network: Network
    presence: Presence | None = None
    include_geofence: StrictBool = True


class SavedSetup(Setup):
    expected_revision: int = Field(strict=True, ge=0)


def profile_directory():
    from .main import DATA_DIR
    return DATA_DIR


def _plain(data):
    result = data.model_dump(exclude={"presence", "expected_revision"})
    result["presence"] = None if data.presence is None else {
        **data.presence.model_dump(exclude={"token"}), "token": data.presence.token.get_secret_value()}
    return result


def _cipher(create=False):
    root = profile_directory()
    path = root / "android-setup.key"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key()); handle.flush(); os.fsync(handle.fileno())
    return Fernet(path.read_bytes())


def _load_profile():
    path = profile_directory() / "android-setup.enc"
    if not path.exists():
        return {"revision": 0, "updated_at": None, "settings": None}
    try:
        state = json.loads(_cipher().decrypt(path.read_bytes()))
        if type(state["revision"]) is not int or state["revision"] < 1:
            raise ValueError()
        Setup.model_validate(state["settings"])
        return state
    except Exception:
        raise HTTPException(503, "已保存配置读取失败，请检查数据目录和密钥，原配置未被覆盖") from None


def _public_profile(state):
    settings = state["settings"]
    token_saved = bool(settings and settings.get("presence"))
    if settings:
        settings = {**settings, "presence": None if not settings.get("presence") else {
            k: v for k, v in settings["presence"].items() if k != "token"}}
    return {**state, "settings": settings, "has_saved_token": token_saved}


def _save_profile(data):
    root = profile_directory()
    try:
        cipher = _cipher(create=True)
    except Exception:
        raise HTTPException(503, "配置密钥读取或创建失败，未保存配置") from None
    current = _load_profile()
    if current["revision"] != data.expected_revision:
        raise HTTPException(409, "配置已被其他页面修改，请先读取后台配置")
    state = {"revision": current["revision"] + 1, "updated_at": datetime.now(timezone.utc).isoformat(), "settings": _plain(data)}
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".android-setup-", delete=False) as handle:
            pending = handle.name
            os.chmod(pending, 0o600)
            handle.write(cipher.encrypt(json.dumps(state, ensure_ascii=False).encode("utf-8")))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(pending, root / "android-setup.enc")
    except Exception:
        raise HTTPException(503, "配置保存失败，原配置未被覆盖") from None
    finally:
        if pending and os.path.exists(pending):
            os.unlink(pending)
    return state


async def _read_request(request, model=Setup):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
            raise HTTPException(403, "不接受跨站配置操作")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(415, "请使用 JSON 提交配置")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, "配置过长")
    try:
        value = json.loads(body)
        if isinstance(value, dict) and isinstance(value.get("presence"), dict) and value["presence"].get("token", "") == "":
            saved = _load_profile()["settings"]
            if saved and saved.get("presence"):
                value["presence"]["token"] = saved["presence"]["token"]
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, "请检查 Wi-Fi、服务地址、HA 实体及令牌格式；首次保存需要填写令牌") from None


@router.get("/config")
def get_profile():
    with _profile_lock:
        return JSONResponse(_public_profile(_load_profile()), headers={"Cache-Control": "no-store"})


@router.put("/config")
async def save_profile(request: Request):
    # A stale revision cannot overwrite a profile changed while request bytes were arriving.
    data = await _read_request(request, SavedSetup)
    with _profile_lock:
        return JSONResponse(_public_profile(_save_profile(data)), headers={"Cache-Control": "no-store"})


def configuration(data: Setup):
    result = {"type": "nasbridge.android.config", "version": 1, "network": data.network.model_dump()}
    if data.presence:
        result["presence"] = {**data.presence.model_dump(exclude={"token"}),
                              "token": data.presence.token.get_secret_value()}
    if data.include_geofence:
        from .geofence import document, load_state
        try:
            fence = document(load_state())
        except Exception:
            raise HTTPException(503, "围栏读取失败，请重试或取消包含围栏") from None
        keys = ("enabled", "name", "fence_type", "center_latitude", "center_longitude", "radius_meters",
                "max_location_accuracy_meters", "max_location_age_seconds")
        result["geofence"] = {"schema_version": 2, "revision": fence["revision"],
                              "settings": {key: fence["settings"][key] for key in keys}}
    return result


@router.post("/qr.svg", response_class=Response)
async def generate_qr(request: Request):
    data = await _read_request(request)
    value = json.dumps(configuration(data), ensure_ascii=False, separators=(",", ":"))
    if len(value.encode("utf-8")) > 2000:
        raise HTTPException(422, "配置过长，请缩短地址或取消包含围栏")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=6)
    # Compact ASCII avoids very dense codes and Unicode guessing by camera decoders.
    encoded = "NASBRIDGE1:" + base64.urlsafe_b64encode(zlib.compress(value.encode("utf-8"))).decode("ascii").rstrip("=")
    qr.add_data(encoded)
    try:
        qr.make(fit=True)
    except DataOverflowError:
        raise HTTPException(422, "配置过长，无法生成二维码") from None
    output = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage).save(output)
    return Response(output.getvalue(), media_type="image/svg+xml", headers={
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
