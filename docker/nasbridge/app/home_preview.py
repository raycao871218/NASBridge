"""Cached first-screen captures for saved shortcuts. Each capture uses a fresh browser."""
import asyncio
import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
import httpx

_capture_lock = asyncio.Lock()
CACHE_SECONDS = 6 * 60 * 60


def cache_directory():
    from .main import DATA_DIR
    return DATA_DIR / "website-previews"


def fresh(path):
    return path.is_file() and time.time() - path.stat().st_mtime < CACHE_SECONDS


def playwright_proxy(proxy):
    settings = {"server": str(proxy.url), "bypass": "<local>,*.local,*.lan,127.0.0.1,[::1],10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"}
    if proxy.auth:
        settings.update(username=proxy.auth[0], password=proxy.auth[1])
    return settings


async def browser_proxy():
    try:
        if os.getenv("NASBRIDGE_PREVIEW_PROXY"):
            return playwright_proxy(httpx.Proxy(os.environ["NASBRIDGE_PREVIEW_PROXY"]))
        from .main import load_config, mihomo_base_url, mihomo_get
        from .mobile_api import proxy_address
        config = load_config().get("mihomo", {})
        if config:
            ports = await mihomo_get("/configs", config)
            return playwright_proxy(proxy_address(mihomo_base_url(config), ports))
    except Exception:
        pass
    return None


async def capture(url, output):
    from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=os.getenv("NASBRIDGE_CHROMIUM_PATH", "/usr/bin/chromium"),
                                                   headless=True, proxy=await browser_proxy(), args=["--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 800},
                                                device_scale_factor=1, locale="zh-CN",
                                                accept_downloads=False, service_workers="block")
            async def route_request(route):
                # A preview can read a page but must never submit forms or write through its scripts.
                request = route.request
                if request.method not in ("GET", "HEAD") or urlsplit(request.url).scheme not in ("http", "https"):
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", route_request)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if response and response.status >= 400:
                raise ValueError("website returned an error")
            try:
                await page.wait_for_load_state("networkidle", timeout=3500)
            except BrowserTimeout:
                pass
            await page.screenshot(path=str(output), type="jpeg", quality=78, full_page=False,
                                  animations="disabled", caret="hide", timeout=8000)
        finally:
            await browser.close()


async def screenshot(url):
    from .home import Website
    url = Website.clean_url(url)
    directory = cache_directory()
    path = directory / (hashlib.sha256(url.encode()).hexdigest() + ".jpg")
    if fresh(path):
        return path
    try:
        # One browser at a time, including requests from several open dashboard tabs.
        async with asyncio.timeout(100):
            async with _capture_lock:
                if fresh(path):
                    return path
                directory.mkdir(parents=True, exist_ok=True)
                pending = path.with_suffix(".pending.jpg")
                try:
                    async with asyncio.timeout(35):
                        await capture(url, pending)
                    pending.replace(path)
                finally:
                    pending.unlink(missing_ok=True)
        return path
    except Exception:
        # A stale successful screenshot is more useful than a broken image during a site outage.
        if path.is_file():
            return path
        raise HTTPException(503, "暂时无法生成网页预览，请稍后重新加载") from None
