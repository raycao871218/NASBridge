#!/usr/bin/env python3
"""Sync TMDB metadata/images into local media library and write NFO files.

Features:
- Scan movie/TV folders under a library root
- Skip folders when required resources already exist
- Resolve metadata by TMDB/IMDb explicit IDs, or by title/year search
- Download poster/fanart images (if missing)
- Write Kodi/Emby style NFO files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"

DEFAULT_REQUIRED = {
    "movie": ["movie.nfo", "poster.jpg", "fanart.jpg"],
    "tv": ["tvshow.nfo", "poster.jpg", "fanart.jpg"],
}


@dataclass
class OverrideConfig:
    imdb_id: str | None = None
    tmdb_id: int | None = None
    media_type: str | None = None  # movie|tv


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        language: str,
        timeout: int = 30,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
    ):
        self.api_key = api_key
        self.language = language
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.session = requests.Session()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        url = f"{TMDB_API_BASE}{path}"
        query = {"api_key": self.api_key, "language": self.language}
        query.update(params)
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.get(url, params=query, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status is not None and status < 500 and status != 429:
                    raise
                if attempt >= self.max_retries:
                    raise
                sleep_s = self.retry_backoff * (2**attempt)
                print(f"[RETRY] tmdb http {status}: {url} (sleep {sleep_s:.1f}s)")
                time.sleep(sleep_s)
            except (requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError):
                if attempt >= self.max_retries:
                    raise
                sleep_s = self.retry_backoff * (2**attempt)
                print(f"[RETRY] tmdb timeout/connection: {url} (sleep {sleep_s:.1f}s)")
                time.sleep(sleep_s)

    def get_movie(self, tmdb_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{tmdb_id}")

    def get_tv(self, tmdb_id: int) -> dict[str, Any]:
        return self._get(f"/tv/{tmdb_id}")

    def find_by_imdb(self, imdb_id: str) -> dict[str, Any]:
        return self._get(f"/find/{imdb_id}", external_source="imdb_id")

    def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        data = self._get("/search/movie", query=query, year=year or "")
        return data.get("results", [])

    def search_tv(self, query: str, first_air_date_year: int | None = None) -> list[dict[str, Any]]:
        data = self._get("/search/tv", query=query, first_air_date_year=first_air_date_year or "")
        return data.get("results", [])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync TMDB metadata/images and generate NFO in media library")
    p.add_argument("--library-root", required=True, help="Media library root directory")
    p.add_argument("--api-key", default=os.getenv("TMDB_API_KEY"), help="TMDB API key (or TMDB_API_KEY env)")
    p.add_argument("--language", default="zh-CN", help="TMDB language (default: zh-CN)")
    p.add_argument("--dry-run", action="store_true", help="Only print actions")
    p.add_argument("--recursive", action="store_true", help="Recursively discover media folders")
    p.add_argument("--media-type", choices=["auto", "movie", "tv"], default="auto")
    p.add_argument("--item-path", help="Only process one specific media folder")

    # explicit override for single folder
    p.add_argument("--imdb-id", help="Force IMDb id for single target")
    p.add_argument("--tmdb-id", type=int, help="Force TMDB id for single target")

    # mapping file for batch override
    p.add_argument(
        "--override-file",
        help=(
            "JSON file mapping folder name/path to ids. "
            "Example: {\"Inception (2010)\": {\"tmdb_id\": 27205, \"media_type\": \"movie\"}}"
        ),
    )

    p.add_argument("--overwrite-nfo", action="store_true", help="Rewrite nfo even if exists")
    p.add_argument("--overwrite-images", action="store_true", help="Redownload images even if exists")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--max-retries", type=int, default=2, help="Retry times for network requests")
    p.add_argument("--retry-backoff", type=float, default=2.0, help="Retry backoff base seconds")
    return p.parse_args()


def load_override_map(path: str | None) -> dict[str, OverrideConfig]:
    if not path:
        return {}
    fp = Path(path)
    if not fp.exists():
        raise FileNotFoundError(f"override file not found: {fp}")
    data = json.loads(fp.read_text(encoding="utf-8"))
    result: dict[str, OverrideConfig] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        result[k] = OverrideConfig(
            imdb_id=v.get("imdb_id"),
            tmdb_id=v.get("tmdb_id"),
            media_type=v.get("media_type"),
        )
    return result


def find_media_folders(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        dirs: list[Path] = []
        for p in root.rglob("*"):
            if not p.is_dir():
                continue
            if has_video_file(p):
                dirs.append(p)
        return sorted(set(dirs))

    return sorted([p for p in root.iterdir() if p.is_dir()])


def has_video_file(folder: Path) -> bool:
    exts = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv"}
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in exts:
            return True
    return False


def infer_media_type(folder: Path) -> str:
    season_dir = any(re.search(r"^season\s*\d+$", d.name, re.I) for d in folder.iterdir() if d.is_dir())
    episodic_file = any(re.search(r"s\d{1,2}e\d{1,2}", f.name, re.I) for f in folder.iterdir() if f.is_file())
    return "tv" if season_dir or episodic_file else "movie"


def parse_title_year(folder_name: str) -> tuple[str, int | None]:
    name = folder_name
    year_match = re.search(r"(19|20)\d{2}", name)
    year = int(year_match.group()) if year_match else None

    clean = re.sub(r"[._]+", " ", name)
    clean = re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", clean)
    clean = re.sub(r"(19|20)\d{2}", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or folder_name, year


def is_complete(folder: Path, media_type: str) -> bool:
    required = DEFAULT_REQUIRED[media_type]
    return all((folder / f).exists() for f in required)


def pick_best_match(results: list[dict[str, Any]], title: str, year: int | None, media_type: str) -> dict[str, Any] | None:
    if not results:
        return None

    title_key = "title" if media_type == "movie" else "name"
    date_key = "release_date" if media_type == "movie" else "first_air_date"

    def score(item: dict[str, Any]) -> tuple[int, float, float]:
        item_title = (item.get(title_key) or "").strip().lower()
        query_title = title.strip().lower()
        exact = 1 if item_title == query_title else 0

        year_score = 0
        if year:
            date_str = item.get(date_key) or ""
            item_year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
            year_score = 1 if item_year == year else 0

        pop = float(item.get("popularity", 0))
        vote = float(item.get("vote_average", 0))
        return (exact + year_score, pop, vote)

    return sorted(results, key=score, reverse=True)[0]


def resolve_tmdb_item(
    client: TMDBClient,
    folder: Path,
    media_type: str,
    override: OverrideConfig | None,
) -> tuple[str, dict[str, Any]] | None:
    forced_media_type = override.media_type if override and override.media_type in {"movie", "tv"} else media_type

    if override and override.tmdb_id:
        try:
            if forced_media_type == "movie":
                return "movie", client.get_movie(override.tmdb_id)
            return "tv", client.get_tv(override.tmdb_id)
        except requests.HTTPError:
            # fallback to auto-detect if media type was wrong
            try:
                return "tv", client.get_tv(override.tmdb_id)
            except requests.HTTPError:
                return "movie", client.get_movie(override.tmdb_id)

    if override and override.imdb_id:
        found = client.find_by_imdb(override.imdb_id)
        movie_results = found.get("movie_results", [])
        tv_results = found.get("tv_results", [])

        if forced_media_type == "movie" and movie_results:
            return "movie", client.get_movie(int(movie_results[0]["id"]))
        if forced_media_type == "tv" and tv_results:
            return "tv", client.get_tv(int(tv_results[0]["id"]))

        if movie_results:
            return "movie", client.get_movie(int(movie_results[0]["id"]))
        if tv_results:
            return "tv", client.get_tv(int(tv_results[0]["id"]))
        return None

    title, year = parse_title_year(folder.name)

    if forced_media_type == "movie":
        best = pick_best_match(client.search_movie(title, year), title, year, "movie")
        return ("movie", client.get_movie(int(best["id"]))) if best else None

    if forced_media_type == "tv":
        best = pick_best_match(client.search_tv(title, year), title, year, "tv")
        return ("tv", client.get_tv(int(best["id"]))) if best else None

    best_movie = pick_best_match(client.search_movie(title, year), title, year, "movie")
    best_tv = pick_best_match(client.search_tv(title, year), title, year, "tv")

    if best_movie and not best_tv:
        return "movie", client.get_movie(int(best_movie["id"]))
    if best_tv and not best_movie:
        return "tv", client.get_tv(int(best_tv["id"]))
    if not best_tv and not best_movie:
        return None

    movie_pop = float(best_movie.get("popularity", 0)) if best_movie else -1
    tv_pop = float(best_tv.get("popularity", 0)) if best_tv else -1
    if movie_pop >= tv_pop:
        return "movie", client.get_movie(int(best_movie["id"]))
    return "tv", client.get_tv(int(best_tv["id"]))


def text(elem: ET.Element, tag: str, value: Any) -> None:
    child = ET.SubElement(elem, tag)
    child.text = "" if value is None else str(value)


def build_movie_nfo(data: dict[str, Any]) -> ET.Element:
    root = ET.Element("movie")
    text(root, "title", data.get("title"))
    text(root, "originaltitle", data.get("original_title"))
    text(root, "year", (data.get("release_date") or "")[:4])
    text(root, "rating", data.get("vote_average"))
    text(root, "plot", data.get("overview"))
    text(root, "premiered", data.get("release_date"))
    text(root, "runtime", data.get("runtime"))
    text(root, "tmdbid", data.get("id"))
    text(root, "imdbid", data.get("imdb_id"))

    for g in data.get("genres", []):
        text(root, "genre", g.get("name"))

    return root


def build_tv_nfo(data: dict[str, Any]) -> ET.Element:
    root = ET.Element("tvshow")
    text(root, "title", data.get("name"))
    text(root, "originaltitle", data.get("original_name"))
    text(root, "year", (data.get("first_air_date") or "")[:4])
    text(root, "rating", data.get("vote_average"))
    text(root, "plot", data.get("overview"))
    text(root, "premiered", data.get("first_air_date"))
    text(root, "tmdbid", data.get("id"))

    ext = data.get("external_ids") or {}
    imdb_id = ext.get("imdb_id")
    if imdb_id:
        text(root, "imdbid", imdb_id)

    for g in data.get("genres", []):
        text(root, "genre", g.get("name"))

    return root


def indent_xml(elem: ET.Element, level: int = 0) -> None:
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent_xml(e, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def write_nfo(folder: Path, media_type: str, data: dict[str, Any], overwrite: bool, dry_run: bool) -> None:
    nfo_name = "movie.nfo" if media_type == "movie" else "tvshow.nfo"
    nfo_path = folder / nfo_name

    if nfo_path.exists() and not overwrite:
        return

    if media_type == "movie":
        root = build_movie_nfo(data)
    else:
        root = build_tv_nfo(data)

    indent_xml(root)
    xml_data = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    if dry_run:
        print(f"[DRY-RUN] write nfo: {nfo_path}")
        return

    nfo_path.write_bytes(xml_data)
    print(f"[OK] nfo written: {nfo_path}")


def download_file(
    url: str,
    path: Path,
    overwrite: bool,
    dry_run: bool,
    timeout: int,
    max_retries: int,
    retry_backoff: float,
) -> None:
    if path.exists() and not overwrite:
        return

    if dry_run:
        print(f"[DRY-RUN] download: {url} -> {path}")
        return

    for attempt in range(max_retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            path.write_bytes(r.content)
            print(f"[OK] image saved: {path}")
            return
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is not None and status < 500 and status != 429:
                raise
            if attempt >= max_retries:
                raise
            sleep_s = retry_backoff * (2**attempt)
            print(f"[RETRY] image http {status}: {url} (sleep {sleep_s:.1f}s)")
            time.sleep(sleep_s)
        except (requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError):
            if attempt >= max_retries:
                raise
            sleep_s = retry_backoff * (2**attempt)
            print(f"[RETRY] image timeout/connection: {url} (sleep {sleep_s:.1f}s)")
            time.sleep(sleep_s)


def write_images(
    folder: Path,
    data: dict[str, Any],
    overwrite: bool,
    dry_run: bool,
    timeout: int,
    max_retries: int,
    retry_backoff: float,
) -> None:
    poster = data.get("poster_path")
    backdrop = data.get("backdrop_path")

    if poster:
        download_file(
            f"{TMDB_IMAGE_BASE}{poster}",
            folder / "poster.jpg",
            overwrite,
            dry_run,
            timeout,
            max_retries,
            retry_backoff,
        )

    if backdrop:
        download_file(
            f"{TMDB_IMAGE_BASE}{backdrop}",
            folder / "fanart.jpg",
            overwrite,
            dry_run,
            timeout,
            max_retries,
            retry_backoff,
        )


def get_override_for_folder(
    folder: Path,
    override_map: dict[str, OverrideConfig],
    single_override: OverrideConfig | None,
) -> OverrideConfig | None:
    if single_override and (single_override.imdb_id or single_override.tmdb_id):
        return single_override

    by_abs = override_map.get(str(folder))
    if by_abs:
        return by_abs

    by_name = override_map.get(folder.name)
    if by_name:
        return by_name

    return None


def process_folder(
    folder: Path,
    client: TMDBClient,
    args: argparse.Namespace,
    override_map: dict[str, OverrideConfig],
    single_override: OverrideConfig | None,
) -> None:
    media_type = args.media_type if args.media_type != "auto" else infer_media_type(folder)

    if media_type not in {"movie", "tv"}:
        print(f"[SKIP] unsupported media type: {folder}")
        return

    if is_complete(folder, media_type):
        print(f"[SKIP] complete resources exists: {folder}")
        return

    override = get_override_for_folder(folder, override_map, single_override)

    try:
        resolved = resolve_tmdb_item(client, folder, media_type, override)
    except requests.RequestException as e:
        print(f"[ERR] tmdb request failed: {folder} -> {e}")
        return

    if not resolved:
        print(f"[MISS] no tmdb match: {folder}")
        return

    final_type, data = resolved

    if final_type == "tv":
        try:
            ext = client._get(f"/tv/{data.get('id')}/external_ids")
            data["external_ids"] = ext
        except requests.RequestException:
            pass

    write_nfo(folder, final_type, data, args.overwrite_nfo, args.dry_run)
    try:
        write_images(
            folder,
            data,
            args.overwrite_images,
            args.dry_run,
            args.timeout,
            args.max_retries,
            args.retry_backoff,
        )
    except requests.RequestException as e:
        print(f"[WARN] image download failed: {folder} -> {e}")


def main() -> int:
    args = parse_args()

    if not args.api_key:
        print("[ERR] TMDB API key is required. Use --api-key or set TMDB_API_KEY")
        return 2

    root = Path(args.library_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERR] invalid library root: {root}")
        return 2

    override_map = load_override_map(args.override_file)
    single_override = OverrideConfig(imdb_id=args.imdb_id, tmdb_id=args.tmdb_id, media_type=args.media_type)

    client = TMDBClient(
        args.api_key,
        args.language,
        timeout=args.timeout,
        max_retries=args.max_retries,
        retry_backoff=args.retry_backoff,
    )

    if args.item_path:
        folders = [Path(args.item_path).expanduser().resolve()]
    else:
        folders = find_media_folders(root, args.recursive)

    if not folders:
        print("[WARN] no media folder found")
        return 0

    for folder in folders:
        if not folder.exists() or not folder.is_dir():
            print(f"[SKIP] not a directory: {folder}")
            continue
        process_folder(folder, client, args, override_map, single_override)

    return 0


if __name__ == "__main__":
    sys.exit(main())
