"""Same-origin gateway to the separately deployed wechat-article-reader."""
import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .home import check_origin

router = APIRouter(prefix="/api/wechat", tags=["wechat"])
_read_lock = asyncio.Lock()
_schema_lock = threading.Lock()
_schema_ready = False


def history_connection():
    from .main import postgres_connection
    global _schema_ready
    connection = postgres_connection("nasbridge-wechat")
    try:
        with _schema_lock:
            if not _schema_ready:
                with connection, connection.cursor() as cursor:
                    cursor.execute("""CREATE TABLE IF NOT EXISTS public.wechat_articles (
                        id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                        account TEXT NOT NULL, author TEXT NOT NULL, publish_time TEXT NOT NULL,
                        fetched_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL)""")
                    cursor.execute("""CREATE INDEX IF NOT EXISTS wechat_articles_fetched_at_idx
                                      ON public.wechat_articles (fetched_at DESC, id)""")
                _schema_ready = True
    except Exception:
        connection.close()
        raise
    return connection


def article_id(url):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if parts.path == "/s" and all(query.get(key) for key in ("__biz", "mid", "idx")):
        identity = json.dumps([query[key][0] for key in ("__biz", "mid", "idx")])
    else:
        identity = urlunsplit(("https", "mp.weixin.qq.com", parts.path, "" if parts.path.startswith("/s/") else parts.query, ""))
    return hashlib.sha256(identity.encode()).hexdigest()


def save_article(payload, source_url):
    key = article_id(source_url)
    fetched_at = datetime.now(timezone.utc).isoformat()
    # Keep the validated requested URL for reopening an archived article safely.
    saved = {**payload, "url": source_url}
    with closing(history_connection()) as connection, connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO public.wechat_articles
            (id, url, title, account, author, publish_time, fetched_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT(id) DO UPDATE SET url=excluded.url, title=excluded.title,
            account=excluded.account, author=excluded.author, publish_time=excluded.publish_time,
            fetched_at=excluded.fetched_at, payload=excluded.payload""",
            (key, source_url, payload["title"], payload.get("account") or "", payload.get("author") or "",
             payload.get("publish_time") or "", fetched_at, json.dumps(saved, ensure_ascii=False)))
    return {**saved, "history_id": key, "fetched_at": fetched_at}


@router.get("/articles")
def list_articles(q: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1),
                  limit: int = Query(default=20, ge=1, le=100)):
    search = q.strip().lower()
    where = "WHERE strpos(lower(title || ' ' || account || ' ' || author || ' ' || url), %s) > 0"
    try:
        with closing(history_connection()) as connection, connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT count(*) AS total FROM public.wechat_articles " + where, (search,))
            total = cursor.fetchone()["total"]
            cursor.execute("SELECT id, url, title, account, author, publish_time, fetched_at FROM public.wechat_articles "
                           + where + " ORDER BY fetched_at DESC, id LIMIT %s OFFSET %s",
                           (search, limit, (page - 1) * limit))
            rows = [{**dict(row), "fetched_at": row["fetched_at"].isoformat()} for row in cursor.fetchall()]
    except psycopg2.Error:
        raise HTTPException(503, "读取抓取历史失败，请稍后重试")
    return JSONResponse({"items": [dict(row) for row in rows], "total": total, "page": page, "limit": limit},
                        headers={"Cache-Control": "no-store"})


@router.get("/articles/{key}")
def get_article(key: str):
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise HTTPException(404, "历史文章不存在")
    try:
        with closing(history_connection()) as connection, connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT payload, fetched_at FROM public.wechat_articles WHERE id = %s", (key,))
            row = cursor.fetchone()
    except psycopg2.Error:
        raise HTTPException(503, "读取历史文章失败，请稍后重试")
    if row is None:
        raise HTTPException(404, "历史文章不存在")
    return JSONResponse({**row["payload"], "history_id": key, "fetched_at": row["fetched_at"].isoformat()},
                        headers={"Cache-Control": "no-store"})


class ExportArticles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    q: str = Field(default="", max_length=200)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value):
        if value is not None and any(not re.fullmatch(r"[0-9a-f]{64}", key) for key in value):
            raise ValueError("文章 ID 无效")
        return value


def article_markdown(payload):
    metadata = [("公众号", payload.get("account")), ("作者", payload.get("author")),
                ("发布时间", payload.get("publish_time")), ("原文", payload.get("url"))]
    return "# " + (payload.get("title") or "未命名文章") + "\n\n" + "\n\n".join(
        f"{label}：{value}" for label, value in metadata if value) + "\n\n" + payload.get("markdown", "")


@router.post("/export")
def export_articles(body: ExportArticles, request: Request):
    check_origin(request)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不接受跨站请求")
    archive = tempfile.TemporaryFile()
    count = 0
    try:
        with closing(history_connection()) as connection, connection, connection.cursor(
                name="wechat_export", cursor_factory=RealDictCursor) as cursor:
            if body.ids is not None:
                cursor.execute("SELECT id, payload, fetched_at FROM public.wechat_articles WHERE id = ANY(%s) ORDER BY fetched_at DESC, id",
                               (body.ids,))
            else:
                cursor.execute("""SELECT id, payload, fetched_at FROM public.wechat_articles
                    WHERE strpos(lower(title || ' ' || account || ' ' || author || ' ' || url), %s) > 0
                    ORDER BY fetched_at DESC, id""", (body.q.strip().lower(),))
            cursor.itersize = 100
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for row in cursor:
                    payload = row["payload"]
                    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', payload.get("title") or "未命名文章").strip(" .")[:80] or "文章"
                    stem = title + "-" + row["id"]
                    bundle.writestr(stem + ".md", article_markdown(payload))
                    bundle.writestr(stem + ".json", json.dumps({**payload, "history_id": row["id"],
                        "fetched_at": row["fetched_at"].isoformat()}, ensure_ascii=False, indent=2))
                    count += 1
        if not count:
            archive.close()
            raise HTTPException(404, "没有可打包的历史文章")
        size = archive.tell()
        archive.seek(0)
    except (psycopg2.Error, OSError):
        archive.close()
        raise HTTPException(503, "历史文章打包失败，请稍后重试")
    except Exception:
        archive.close()
        raise

    def chunks():
        try:
            while chunk := archive.read(65536):
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(archive.close),
        headers={"Cache-Control": "no-store", "Content-Length": str(size),
                 "Content-Disposition": 'attachment; filename="wechat-articles.zip"'})


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
            if payload["title"].strip() and payload["markdown"].strip():
                try:
                    payload = await run_in_threadpool(save_article, payload, body.url)
                except (psycopg2.Error, OSError):
                    payload = {**payload, "history_error": "抓取成功，但历史保存失败，请立即下载备份后重试。"}
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    return payload
