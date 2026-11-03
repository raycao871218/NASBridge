"""Same-origin gateway to the separately deployed wechat-article-reader."""
import asyncio
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .home import check_origin

router = APIRouter(prefix="/api/wechat", tags=["wechat"])
_read_lock = asyncio.Lock()


class ReadArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=8192)
    images: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        value = value.strip()
        try:
            parts = urlsplit(value)
            # Both /s?__biz=... and /s/ARTICLE_ID are supported.
            valid = (parts.scheme in {"http", "https"} and parts.hostname == "mp.weixin.qq.com"
                     and parts.username is None and parts.password is None
                     and parts.port in {None, 80, 443}
                     and (parts.path == "/s" or parts.path.startswith("/s/")))
        except ValueError:
            valid = False
        if not valid or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("请填写 mp.weixin.qq.com 的公众号文章链接")
        return value


def reader_url():
    from .main import load_config
    config = load_config().get("wechat_reader") or {}
    value = (os.getenv("NASBRIDGE_WECHAT_READER_URL") or config.get("base_url") or "").strip().rstrip("/")
    if not value:
        raise HTTPException(503, "请先配置 wechat_reader.base_url 或 NASBRIDGE_WECHAT_READER_URL")
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in {"http", "https"} and parts.hostname and not parts.username
                 and not parts.password and not parts.query and not parts.fragment)
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "公众号读取服务地址配置无效")
    return value


async def upstream(path, params=None):
    url = reader_url() + path
    try:
        async with asyncio.timeout(60):
            async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
                response = await client.get(url, params=params)
        payload = response.json()
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(504, "公众号读取服务超时，请稍后手动重试")
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "公众号读取服务连接失败或未返回有效 JSON")
    if not isinstance(payload, dict):
        raise HTTPException(502, "公众号读取服务返回格式无效")
    if response.status_code != 200 or payload.get("error"):
        return JSONResponse({"error": payload.get("error", "upstream_error"),
                             "message": payload.get("message") or payload.get("usage") or "公众号读取服务请求失败"},
                            status_code=400 if response.status_code == 400 else 502,
                            headers={"Cache-Control": "no-store"})
    return payload


@router.get("/health")
async def health():
    payload = await upstream("/health")
    if isinstance(payload, dict):
        if payload.get("ok") is not True:
            raise HTTPException(502, "公众号读取服务健康检查失败")
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    return payload


@router.post("/read")
async def read(body: ReadArticle, request: Request):
    check_origin(request)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不接受跨站请求")
    if _read_lock.locked():
        raise HTTPException(409, "正在抓取文章，请完成后再试")
    async with _read_lock:
        payload = await upstream("/read", {"url": body.url, "format": "json",
                                          "images": "1" if body.images else "0", "browser": "never"})
    if isinstance(payload, dict):
        if not isinstance(payload.get("title"), str) or not isinstance(payload.get("markdown"), str):
            raise HTTPException(502, "公众号读取服务返回的文章格式无效")
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    return payload
