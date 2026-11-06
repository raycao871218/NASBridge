"""Download new public Rhino Finance videos and transcribe them on ubuntu-server."""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from rhino_backfill import FIRST_DATE, for_target, parse_target
from rhino_events import event


ROOT = Path("/mnt/azkaban-home/犀牛哥")
HERE = Path(__file__).resolve().parent
PROXY = "http://127.0.0.1:7890"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UCFQsi7WaF5X41tcuOryDk8w"
CHANNEL_URL = "https://www.youtube.com/@RhinoFinance/videos"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


def feed_entries(known):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY}))
    feed_error = None
    tree = None
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(FEED, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
                tree = ET.fromstring(response.read())
            break
        except Exception as error:
            feed_error = error
            if attempt < 2:
                time.sleep(2 * (attempt + 1))

    entries = []
    if tree is not None:
        for item in tree.findall(f"{ATOM}entry"):
            published = dt.datetime.fromisoformat(item.findtext(f"{ATOM}published").replace("Z", "+00:00"))
            date = published.astimezone(dt.timezone(dt.timedelta(hours=8))).date()
            if date >= FIRST_DATE:
                entries.append((published, item.findtext(f"{YT}videoId")))
    else:
        print(f"RSS_UNAVAILABLE {type(feed_error).__name__}: {feed_error}; falling back to yt-dlp channel listing", flush=True)
        listing_command = [
            "/usr/local/bin/yt-dlp", "--proxy", PROXY, "--js-runtimes", "node",
            "--remote-components", "ejs:github", "--cookies", str(HERE / "youtube-cookies.txt"),
            "--ignore-errors", "--skip-download", "--flat-playlist", "--playlist-end", "50",
            "--print", "%(id)s\t%(availability)s", CHANNEL_URL,
        ]
        listing = subprocess.run(listing_command, check=True, text=True, capture_output=True, timeout=180)
        candidates = []
        known_hit = False
        for line in listing.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            video_id, availability = parts
            if video_id in known:
                known_hit = True
                break
            if availability != "subscriber_only":
                candidates.append(video_id)

        for video_id in candidates:
            metadata_command = [
                "/usr/local/bin/yt-dlp", "--proxy", PROXY, "--js-runtimes", "node",
                "--remote-components", "ejs:github", "--cookies", str(HERE / "youtube-cookies.txt"),
                "--ignore-errors", "--skip-download", "--print", "%(upload_date)s\t%(availability)s",
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            metadata = subprocess.run(metadata_command, check=False, text=True, capture_output=True, timeout=120)
            for line in metadata.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) != 2 or len(parts[0]) != 8 or not parts[0].isdigit():
                    continue
                if parts[1] == "subscriber_only":
                    break
                published_date = dt.datetime.strptime(parts[0], "%Y%m%d").date()
                if published_date >= FIRST_DATE:
                    published = dt.datetime.combine(published_date, dt.time(), tzinfo=dt.timezone.utc)
                    entries.append((published, video_id))
                break
        if not entries and known_hit:
            return []
    if not entries:
        raise RuntimeError("The public channel feed has no eligible videos")
    return [video_id for _, video_id in sorted(entries)]


def channel_entries(base):
    page_size = 50
    first = 1
    while True:
        listing = subprocess.run([*base, "--flat-playlist", "--lazy-playlist",
                                  "--playlist-items", f"{first}:{first + page_size - 1}",
                                  "--print", "%(id)s\t%(availability)s", CHANNEL_URL],
                                 check=True, text=True, capture_output=True, timeout=90)
        rows = listing.stdout.splitlines()
        yield from rows
        if len(rows) < page_size:
            return
        first += page_size


def historical_entries(target, indexed):
    base = [
        "/usr/local/bin/yt-dlp", "--ignore-config", "--proxy", PROXY, "--js-runtimes", "node",
        "--remote-components", "ejs:github", "--cookies", str(HERE / "youtube-cookies.txt"),
        "--no-ignore-errors", "--skip-download",
    ]
    matches = []
    seen = set()
    for line in channel_entries(base):
        video_id, availability = line.split("\t")
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise RuntimeError("Channel listing has an invalid video ID")
        if video_id in seen or availability in {"subscriber_only", "private", "premium_only"}:
            continue
        seen.add(video_id)
        if video_id in indexed:
            upload_day, _ = indexed[video_id]
        else:
            metadata = subprocess.run([*base, "--no-playlist", "--print", "%(upload_date)s\t%(availability)s",
                                       f"https://www.youtube.com/watch?v={video_id}"],
                                      check=True, text=True, capture_output=True, timeout=90)
            upload_date, availability = metadata.stdout.strip().split("\t")
            if availability in {"subscriber_only", "private", "premium_only"}:
                continue
            if not re.fullmatch(r"[0-9]{8}", upload_date):
                raise RuntimeError("Video upload date is unavailable")
            upload_day = dt.datetime.strptime(upload_date, "%Y%m%d").date()
        # The channel's Videos tab is newest first. Known dates avoid re-querying archived uploads.
        if upload_day < target:
            break
        if upload_day == target:
            matches.append(video_id)
    if not seen:
        raise RuntimeError("Channel listing has no readable public videos")
    return matches


def indexed_media(videos):
    try:
        rows = (ROOT / ".rhino-finance-video-index.tsv").read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    indexed = {}
    for row in rows:
        video_id, separator, path = row.partition("\t")
        file = videos / Path(path.replace("\\", "/")).name
        if not separator or not re.match(r"[0-9]{8}[-_ ]", file.name) or file.suffix.lower() != ".mp4":
            continue
        try:
            upload_day = dt.datetime.strptime(file.name[:8], "%Y%m%d").date()
        except ValueError:
            continue
        indexed[video_id] = (upload_day, file)
    return indexed


def download(target=None):
    event('discover', message='正在检查频道更新')
    videos = ROOT / "视频"
    archive = videos / ".rhino-finance-archive.txt"
    known = set(re.findall(r"^youtube\s+(\S+)", archive.read_text(encoding="utf-8"), re.M)) if archive.exists() else set()
    if target:
        indexed = indexed_media(videos)
        matches = historical_entries(target, indexed)
        archived = {video_id for video_id, (day, file) in indexed.items() if day == target and file.is_file()}
        pending = [video_id for video_id in matches if video_id not in archived]
        event('discover', 'success', f'{target} 找到 {len(matches)} 部公开视频，{len(pending)} 部待下载',
              pending_count=len(pending), matched_count=len(matches))
    else:
        pending = [video_id for video_id in feed_entries(known) if video_id not in known]
        event('discover', 'success', f'发现 {len(pending)} 部待下载视频', pending_count=len(pending))
    event('download', message=f'待下载 {len(pending)} 部视频')
    for video_id in pending:
        event('download', message='正在下载视频与音频', item=video_id)
        archive_options = ["--no-download-archive"] if target and video_id in known else ["--download-archive", str(archive)]
        command = [
            "/usr/local/bin/yt-dlp", "--proxy", PROXY,
            "--js-runtimes", "node", "--remote-components", "ejs:github",
            "--cookies", str(HERE / "youtube-cookies.txt"),
            *archive_options, "--no-playlist", "--progress", "--newline", "--progress-delta", "5",
            "--windows-filenames", "--merge-output-format", "mp4",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "-P", str(videos), "-o", "%(upload_date)s-%(title).150B.%(ext)s",
            "--print-to-file", "after_move:%(id)s\t%(filepath)s", str(ROOT / ".rhino-finance-video-index.tsv"),
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        subprocess.run(command, check=True)
        print(f"DOWNLOADED {video_id}", flush=True)
        event('download', message='视频下载与合并完成', item=video_id)
    if not pending:
        print("NO_NEW_VIDEOS", flush=True)
    event('download', 'success' if pending else 'skipped', '下载阶段完成' if pending else '没有新视频，跳过下载')


def stamp(seconds):
    total = round(seconds)
    return f"{total // 3600:02d}:{(total // 60) % 60:02d}:{total % 60:02d}"


def transcribe(target=None):
    event('transcribe', message='正在检查待转写视频')
    from faster_whisper import WhisperModel

    video_dir, text_dir = ROOT / "视频", ROOT / "原文"
    text_dir.mkdir(exist_ok=True)
    index = ROOT / ".rhino-finance-video-index.tsv"
    sources = {}
    if index.exists():
        for line in index.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sources[Path(parts[1].replace("\\", "/")).stem] = f"https://www.youtube.com/watch?v={parts[0]}"
    pending = [v for v in sorted(video_dir.glob("*.mp4"))
               if for_target(v, target) and not (text_dir / f"{v.stem}.txt").exists()]
    if not pending:
        print("NO_NEW_TRANSCRIPTS", flush=True)
        event('transcribe', 'skipped', '没有待转写的视频')
        return
    model = WhisperModel(str(HERE / "whisper-base"), device="cpu", compute_type="int8")
    for video in pending:
        event('transcribe', message='正在语音转写', item=video.name)
        target = text_dir / f"{video.stem}.txt"
        temporary = target.with_suffix(".txt.tmp")
        segments, info = model.transcribe(str(video), language="zh", beam_size=3,
                                          vad_filter=True, condition_on_previous_text=False)
        count = 0
        last_report = time.monotonic()
        with temporary.open("w", encoding="utf-8") as output:
            output.write(f"# 自动语音转写（未经人工校对）\n视频：{video.name}\n")
            output.write(f"来源：{sources.get(video.stem, 'Rhino Finance YouTube 频道，视频链接待补')}\n")
            output.write(f"时长：{stamp(info.duration)}\n\n")
            for segment in segments:
                if segment.text.strip():
                    output.write(f"[{stamp(segment.start)}–{stamp(segment.end)}] {segment.text.strip()}\n")
                    count += 1
                if time.monotonic() - last_report >= 5:
                    event('transcribe', message=f'已转写至 {stamp(segment.end)} / {stamp(info.duration)}',
                          item=video.name, percent=min(100, round(segment.end / max(info.duration, 1) * 100, 1)))
                    last_report = time.monotonic()
        if count == 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"No speech in {video.name}")
        temporary.replace(target)
        print(f"TRANSCRIPT {video.name} SEGMENTS {count}", flush=True)
    event('transcribe', 'success', f'已完成 {len(pending)} 部视频转写')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--date", type=parse_target)
    args = parser.parse_args()
    if not ROOT.is_dir() or not os.path.ismount("/mnt/azkaban-home"):
        raise RuntimeError("Azkaban SMB share is not mounted")
    if args.check:
        print("SHARE_READY")
        return
    event('check', 'success', '共享目录已就绪')
    download_error = None
    try:
        download(args.date)
    except Exception as error:
        download_error = error
        print(f"DOWNLOAD_FAILED {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        event('download', 'failed', '视频获取失败，继续处理已有视频')
    transcribe(args.date)
    if download_error is not None:
        raise download_error


if __name__ == "__main__":
    main()
