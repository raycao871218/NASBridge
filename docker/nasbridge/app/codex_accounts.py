"""Expose account and power controls over the restricted Ubuntu SSH connection."""
import json
import re
import socket

import paramiko
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/api/codex-accounts")
EMAIL = re.compile(r"[a-zA-Z0-9.!_+%=-]+@[a-zA-Z0-9.-]+", re.ASCII)
ERRORS = {
    "busy": (409, "正在切换账号，请稍后刷新"),
    "stale": (409, "当前账号已变化，请刷新后重新选择"),
    "unknown_account": (409, "该账号已不可用，请刷新列表"),
    "reauth": (409, "目标账号的登录已失效，请在 Ubuntu 重新授权"),
    "refresh_failed": (502, "无法刷新目标账号，请检查 Ubuntu 的代理和登录状态"),
    "identity_mismatch": (502, "授权返回的账号不匹配，未切换"),
    "unsupported_login": (409, "Ubuntu 当前未使用受支持的 ChatGPT 文件登录"),
}


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and EMAIL.fullmatch(value)


def authorize(request):
    from .main import load_config

    settings = load_config().get("codex_accounts") or {}
    lan_hosts = settings.get("lan_hosts") or []
    if not lan_hosts or not settings.get("ssh_host") or not settings.get("ssh_key_path"):
        raise HTTPException(503, "鸟巢尚未配置 Codex 账号管理")
    host = request.headers.get("host", "").lower()
    if host not in lan_hosts or any(header in request.headers for header in (
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
    )):
        raise HTTPException(403, "请通过鸟巢的内网地址管理 Codex 账号")
    origin = request.headers.get("origin")
    if (request.headers.get("sec-fetch-site") == "cross-site" or
            (origin and origin.lower() != f"{request.url.scheme}://{host}")):
        raise HTTPException(403, "不接受跨站请求")
    return settings


def ssh_action(settings, action, timeout):
    with paramiko.SSHClient() as client:
        try:
            client.load_host_keys(settings["ssh_known_hosts"])
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            key = paramiko.Ed25519Key.from_private_key_file(settings["ssh_key_path"])
            client.connect(settings["ssh_host"], port=int(settings.get("ssh_port", 22)),
                           username=settings["ssh_user"], pkey=key, look_for_keys=False,
                           allow_agent=False, timeout=4, banner_timeout=4, auth_timeout=4)
            stdin, stdout, stderr = client.exec_command(action, timeout=timeout)
            with stdin, stdout, stderr:
                raw = stdout.read(65537)
                if len(raw) > 65536 or stdout.channel.recv_exit_status():
                    raise ValueError("Invalid controller response")
            return json.loads(raw)
        except Exception:
            raise HTTPException(503, "无法确认 Ubuntu 的响应") from None


def control(settings, target=None, expected=None):
    if target is not None and not (valid_email(target) and valid_email(expected)):
        raise ValueError("Invalid account")
    action = "status-quota" if target is None else f"switch {target} {expected}"
    data = ssh_action(settings, action, 32 if target is None else 19)
    if data.get("error"):
        raise HTTPException(*ERRORS.get(data["error"], (503, "Ubuntu 的账号服务暂不可用")))
    try:
        current, accounts = data["current"], data["accounts"]
        if not valid_email(current) or not isinstance(accounts, list) or not all(map(valid_email, accounts)):
            raise ValueError("Invalid controller response")
        if target is not None and (current != target or type(data.get("changed")) is not bool):
            raise ValueError("Unconfirmed switch")
        result = {"current": current, "accounts": accounts}
        if target is not None:
            result["changed"] = data["changed"]
        else:
            quota = data.get("quota") or {}
            result["quota"] = {
                email: [
                    {"remaining_percent": window["remaining_percent"],
                     "window_seconds": window["window_seconds"],
                     "reset_at": window["reset_at"]}
                    for window in quota.get(email, [])[:2]
                    if type(window.get("remaining_percent")) in (int, float)
                    and 0 <= window["remaining_percent"] <= 100
                    and type(window.get("window_seconds")) is int and window["window_seconds"] > 0
                    and type(window.get("reset_at")) is int and 0 < window["reset_at"] < 4102444800
                ]
                for email in {current, *accounts}
            }
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(503, "无法确认 Ubuntu 的账号状态，请刷新后再操作") from None


def respond(data, status=200):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def failure(error):
    return respond({"detail": error.detail}, error.status_code)


POWER_MESSAGES = {
    "ready": "开发机在线，可以关机",
    "work_protected": "每日任务正在运行或等待重试，暂不能关机",
    "power_inhibited": "系统有正在保护的工作，暂不能关机",
    "power_pending": "已请求关机并断电，等待开发机下线",
    "power_still_online": "上次请求后开发机仍在线，未能确认关机",
}


def power_request(request, submit=False):
    try:
        settings = authorize(request)
        if settings.get("manual_poweroff") is not True:
            raise HTTPException(503, "尚未启用手动关机")
        if submit and request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "请使用 application/json")
        try:
            data = ssh_action(settings, "poweroff" if submit else "power-status", 16)
        except HTTPException:
            if submit:
                raise HTTPException(503, "关机结果未知，请查看开发机状态") from None
            try:
                with socket.create_connection((settings["ssh_host"], int(settings.get("ssh_port", 22))), timeout=2):
                    return respond({"state": "online", "message": "开发机在线，暂无法读取关机状态"})
            except OSError:
                return respond({"state": "unreachable", "message": "开发机离线或网络不可达"})
        state = data.get("power") if isinstance(data, dict) else None
        if not submit and state not in POWER_MESSAGES:
            return respond({"state": "online", "message": "开发机在线，暂无法读取关机状态"})
        if state not in POWER_MESSAGES or (submit and state == "ready"):
            raise HTTPException(503, "无法确认开发机的关机状态")
        payload = {"state": state, "message": POWER_MESSAGES[state]}
        if submit and state != "power_pending":
            return respond({**payload, "detail": payload["message"]}, 409)
        return respond(payload, 202 if submit else 200)
    except HTTPException as error:
        return failure(error)


@router.get("/power")
def power_status(request: Request):
    return power_request(request)


@router.post("/power")
def poweroff(request: Request):
    return power_request(request, submit=True)


@router.get("")
def accounts(request: Request):
    try:
        return respond(control(authorize(request)))
    except HTTPException as error:
        return failure(error)


@router.post("/switch")
async def switch(request: Request):
    from starlette.concurrency import run_in_threadpool

    try:
        settings = authorize(request)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "请使用 application/json")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 2048:
                raise HTTPException(413, "请求过大")
            body.extend(chunk)
        try:
            data = json.loads(body)
            if (not isinstance(data, dict) or set(data) != {"target", "expected_current"} or
                    not all(map(valid_email, data.values()))):
                raise ValueError("Invalid request")
        except ValueError:
            raise HTTPException(422, "请选择有效的账号并刷新当前状态") from None
        return respond(await run_in_threadpool(control, settings, data["target"], data["expected_current"]))
    except HTTPException as error:
        return failure(error)
