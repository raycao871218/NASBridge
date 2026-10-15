"""Bounded mobile read APIs. Google tests always use the NAS's existing Mihomo proxy."""
from __future__ import annotations

import asyncio
import time
import os
import re
import hashlib
import subprocess
import tempfile
from pathlib import Path
from fastapi.responses import FileResponse
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
_google_lock = asyncio.Lock()
GOOGLE_TARGET = "https://www.google.com/generate_204"


def proxy_address(api_base: str, config: dict) -> httpx.Proxy:
    host = urlparse(api_base).hostname
    port = config.get("mixed-port") or config.get("port")
    if not host or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Mihomo HTTP/Mixed 代理端口未启用")
    auth = None
    for item in config.get("authentication") or []:
        if isinstance(item, str) and ":" in item:
            auth = tuple(item.split(":", 1))
            break
    return httpx.Proxy(httpx.URL(scheme="http", host=host, port=port), auth=auth)


async def run_google_check(mihomo_config: dict) -> dict:
    from .main import mihomo_get, mihomo_base_url
    config = await mihomo_get("/configs", mihomo_config)
    proxy = proxy_address(mihomo_base_url(mihomo_config), config)
    start = time.monotonic()
    async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(7, connect=4), trust_env=False, follow_redirects=False) as client:
        async with client.stream("GET", GOOGLE_TARGET) as response:
            code = response.status_code
    return {
        "status": "ok" if code == 204 else "failed",
        "success": code == 204,
        "http_status": code,
        "delay_ms": round((time.monotonic() - start) * 1000),
        "message": "Google 连接成功" if code == 204 else f"Google 返回 HTTP {code}",
    }


@router.get("/api/mihomo/google-check")
async def google_check() -> dict:
    from .main import load_config
    if _google_lock.locked():
        raise HTTPException(status_code=429, detail="已有 Google 检测正在进行，请稍后重试")
    async with _google_lock:
        try:
            result = await asyncio.wait_for(run_google_check(load_config().get("mihomo", {})), timeout=12)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            result = {"status": "failed", "success": False, "message": "Google 检测超时，请检查 Mihomo 和代理节点"}
        except ValueError:
            result = {"status": "failed", "success": False, "message": "Mihomo HTTP/Mixed 代理端口未启用"}
        except Exception:
            # Do not return proxy URLs, credentials or internal exception strings.
            result = {"status": "failed", "success": False, "message": "无法通过 Mihomo 连接 Google，请检查代理服务、节点及证书"}
        return {**result, "target": GOOGLE_TARGET, "via": "NAS Mihomo", "checked_at": datetime.now(timezone.utc).isoformat()}


def read_article(summary_id: str) -> dict:
    from .main import postgres_connection, load_daily_task_state, daily_task_state_lock
    if not summary_id or len(summary_id) > 500:
        raise HTTPException(status_code=400, detail="无效的文章标识")
    connection = postgres_connection("nasbridge-mobile-article")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT summary_filename, video_id, source_url, updated_at, summary_markdown
                FROM public.rhino_video_summaries WHERE summary_filename = %s LIMIT 1""", (summary_id,))
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="文章不存在")
            record = dict(zip(("summary_filename", "video_id", "source_url", "updated_at", "summary_markdown"), row))
        if len(record.get("summary_markdown") or "") > 512000:
            raise HTTPException(status_code=413, detail="文章过大，无法在手机中完整显示")
        stamp = record.get("updated_at")
        record["updated_at"] = stamp.isoformat() if stamp else None
        with daily_task_state_lock:
            record["is_read"] = summary_id in load_daily_task_state()["read_summaries"]
        return record
    finally:
        connection.close()


@router.get("/api/daily-tasks/article")
async def article(id: str) -> dict:
    try:
        return await asyncio.to_thread(read_article, id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="文章读取失败，请检查任务数据库连接") from exc


# Only indexed MP4s under this read-only mount may be served. Never accept a path from clients.
VIDEO_ROOT = Path(os.environ.get("NASBRIDGE_VIDEO_ROOT", "/app/media/rhino/视频"))
VIDEO_INDEX = Path(os.environ.get("NASBRIDGE_VIDEO_INDEX", "/app/media/rhino/.rhino-finance-video-index.tsv"))


def resolve_video(video_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    try:
        root = VIDEO_ROOT.resolve(strict=True)
        for line in VIDEO_INDEX.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2 or parts[0] != video_id:
                continue
            name = parts[1].replace("\\", "/").rsplit("/", 1)[-1]
            path = (root / name).resolve(strict=True)
            if path.parent == root and path.suffix.lower() == ".mp4" and path.is_file():
                return path
    except (OSError, UnicodeError):
        pass
    raise HTTPException(status_code=404, detail="视频尚未下载或文件不可用")


@router.get("/api/daily-tasks/video")
async def video_info(id: str) -> dict:
    record = await article(id)
    path = await asyncio.to_thread(resolve_video, record.get("video_id") or "")
    return {"video_id": record["video_id"], "title": path.stem,
            "size": path.stat().st_size, "stream_path": "api/videos/" + record["video_id"]}


@router.api_route("/api/videos/{video_id}", methods=["GET", "HEAD"])
async def video_stream(video_id: str):
    path = await asyncio.to_thread(resolve_video, video_id)
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-cache"})


@router.get("/api/videos")
async def video_list() -> dict:
    def collect():
        try:
            ids = [line.split("\t", 1)[0] for line in VIDEO_INDEX.read_text(encoding="utf-8").splitlines()]
        except (OSError, UnicodeError):
            raise HTTPException(status_code=503, detail="视频目录暂不可用")
        items = []
        seen = set()
        for video_id in reversed(ids):
            if video_id in seen:
                continue
            seen.add(video_id)
            try:
                items.append(describe_video(video_id))
            except HTTPException:
                continue
            if len(items) >= 200:
                break
        return {"items": items}
    return await asyncio.to_thread(collect)


def describe_video(video_id: str) -> dict:
    path = resolve_video(video_id)
    published_date = None
    if re.match(r'^\d{8}[-_ ]', path.stem):
        try:
            published_date = datetime.strptime(path.stem[:8], '%Y%m%d').date().isoformat()
        except ValueError:
            pass
    return {"video_id": video_id, "title": path.stem, "size": path.stat().st_size,
            "published_date": published_date,
            "stream_path": "api/videos/" + video_id, "thumbnail_version": video_revision(path)}


@router.get("/api/videos/{video_id}/info")
async def video_metadata(video_id: str) -> dict:
    return await asyncio.to_thread(describe_video, video_id)


THUMBNAIL_ROOT = Path(os.environ.get("NASBRIDGE_THUMBNAIL_ROOT", "/app/data/video-thumbnails"))
_thumbnail_slots = asyncio.Semaphore(2)


def video_revision(path: Path) -> str:
    stamp = path.stat()
    return hashlib.sha256(f"{path.name}:{stamp.st_size}:{stamp.st_mtime_ns}".encode()).hexdigest()[:16]


def make_thumbnail(video_id: str) -> Path:
    path = resolve_video(video_id)
    revision = video_revision(path)
    THUMBNAIL_ROOT.mkdir(parents=True, exist_ok=True)
    target = THUMBNAIL_ROOT / f"{video_id}-{revision}.jpg"
    if target.is_file():
        return target
    fd, temporary = tempfile.mkstemp(suffix=".jpg", dir=THUMBNAIL_ROOT)
    os.close(fd)
    try:
        for second in (90, 0):
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2",
                       "-ss", str(second), "-i", str(path), "-frames:v", "1", "-filter_threads", "1",
                       "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
                       "-q:v", "5", "-y", temporary]
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
            if result.returncode == 0 and Path(temporary).stat().st_size > 0:
                os.replace(temporary, target)
                return target
        raise HTTPException(status_code=503, detail="视频缩略图暂不可用")
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail="视频缩略图生成失败，请稍后重试") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


@router.get("/api/videos/{video_id}/thumbnail")
async def video_thumbnail(video_id: str):
    async with _thumbnail_slots:
        path = await asyncio.to_thread(make_thumbnail, video_id)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})



def read_articles(offset: int, limit: int) -> dict:
    from .main import postgres_connection, load_daily_task_state, daily_task_state_lock
    connection=postgres_connection("nasbridge-article-list")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT summary_filename, video_id, updated_at, LEFT(summary_markdown, 600)
                FROM public.rhino_video_summaries ORDER BY updated_at DESC, summary_filename DESC
                LIMIT %s OFFSET %s""", (limit+1,offset))
            rows=cursor.fetchall()
        with daily_task_state_lock:
            read=load_daily_task_state()["read_summaries"]
        return {"items": [{"summary_filename":row[0],"video_id":row[1],
                "updated_at":row[2].isoformat() if row[2] else None,
                "preview":row[3] or "", "is_read":row[0] in read} for row in rows[:limit]],
                "has_more":len(rows)>limit}
    finally:
        connection.close()


@router.get("/api/articles")
async def articles(offset: int=Query(0,ge=0,le=1000000),limit: int=Query(40,ge=1,le=100)) -> dict:
    try:
        return await asyncio.to_thread(read_articles,offset,limit)
    except Exception as exc:
        raise HTTPException(status_code=502,detail="文章列表读取失败，请稍后重试") from exc
