"""Bounded HTML metadata reads. No scripts, login or image downloads."""
import asyncio
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key and attrs.get("content") and key not in self.meta:
                self.meta[key] = attrs["content"]

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def extract_metadata(html, url):
    from .home import Website
    parser = MetadataParser()
    parser.feed(html)
    name = parser.meta.get("og:title") or parser.meta.get("twitter:title") or "".join(parser.title) or urlsplit(url).hostname
    image = parser.meta.get("og:image:secure_url") or parser.meta.get("og:image") or parser.meta.get("twitter:image")
    try:
        image = Website.clean_url(urljoin(url, image)) if image else None
    except ValueError:
        image = None
    return {"name": " ".join(name.split())[:40], "image_url": image}


async def read_metadata(url):
    from .home import Website
    try:
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(timeout=6, follow_redirects=False, trust_env=False,
                                         headers={"User-Agent": "NASBridge/1.0 WebsitePreview", "Accept": "text/html,application/xhtml+xml"}) as client:
                for _ in range(4):
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = Website.clean_url(urljoin(url, response.headers.get("location", "")))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
                            raise ValueError("not HTML")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            data.extend(chunk[:524288 - len(data)])
                            if len(data) >= 524288:
                                break
                        try:
                            html = data.decode(response.encoding or "utf-8", errors="replace")
                        except LookupError:
                            html = data.decode("utf-8", errors="replace")
                        return extract_metadata(html, str(response.url))
                raise ValueError("too many redirects")
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise HTTPException(502, "暂时无法读取网站信息，可直接保存，或手动填写标题") from None
