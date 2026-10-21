"""Six shared website shortcuts, persisted with the NAS state store."""
import threading
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator

router = APIRouter(prefix="/api/home", tags=["home"])
_lock = threading.Lock()
_http_url = TypeAdapter(HttpUrl)


class Website(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请填写网站名称")
        return value

    @field_validator("url")
    @classmethod
    def clean_url(cls, value):
        value = value.strip()
        if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError("网址不能包含空白或反斜杠")
        if "://" not in value:
            value = "https://" + value
        try:
            parts = urlsplit(value)
            _ = parts.port
            if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
                raise ValueError()
            _http_url.validate_python(value)
        except ValueError:
            raise ValueError("请填写有效的 HTTP 或 HTTPS 网址，不含登录凭据")
        return value


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    sites: list[Website | None] = Field(min_length=6, max_length=6)


def load_state():
    from .main import DATA_DIR, load_persisted_state
    return load_persisted_state("home_shortcuts", DATA_DIR / "home.yaml", {"revision": 0, "sites": [None] * 6})


def save_state(state):
    from .main import DATA_DIR, save_persisted_state
    save_persisted_state("home_shortcuts", DATA_DIR / "home.yaml", state)


@router.get("/sites")
def get_sites():
    with _lock:
        return JSONResponse(load_state(), headers={"Cache-Control": "no-store"})


@router.put("/sites")
def update_sites(body: Update):
    with _lock:
        state = load_state()
        if body.expected_revision != state["revision"]:
            raise HTTPException(409, "网站配置已被其他页面修改，请重新加载首页后再试")
        updated = {"revision": state["revision"] + 1, "sites": [site.model_dump() if site else None for site in body.sites]}
        save_state(updated)
        return JSONResponse(updated, headers={"Cache-Control": "no-store"})
