# run local: uvicorn main:app --host 0.0.0.0 --port 8000
# run render: handled by Dockerfile CMD
# requires: ffmpeg on PATH (merge + remux for bv*+ba)

import asyncio
import os
import re
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
STATIC = BASE / "static"

ALLOWED_FORMAT = re.compile(r"^(best|[A-Za-z0-9_.+\-]{1,64})$")
MAX_URL_LEN = 2048
CHUNK = 64 * 1024

RESOLVE_LIMIT = 12
DOWNLOAD_LIMIT = 6
WINDOW = 60.0

app = FastAPI(title="linkdrop", docs_url=None, redoc_url=None)

COOKIE_FILE = os.environ.get("COOKIES_FILE", "")

_buckets: dict[str, deque] = defaultdict(deque)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def rate_limit(ip: str, cap: int) -> None:
    now = time.monotonic()
    q = _buckets[ip]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= cap:
        raise HTTPException(status_code=429, detail="rate limited")
    q.append(now)


def validate(url: str) -> str:
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LEN:
        raise HTTPException(status_code=400, detail="bad url")
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(status_code=400, detail="bad url")
    return url


def safe_name(name: str, ext: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name or "download").strip(" ._")
    name = name[:120] or "download"
    if not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name


YDL_INFO = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "socket_timeout": 15,
    "extract_flat": False,
}

if COOKIE_FILE and Path(COOKIE_FILE).is_file():
    YDL_INFO["cookiefile"] = COOKIE_FILE


def extract(url: str) -> dict:
    with yt_dlp.YoutubeDL(YDL_INFO) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise HTTPException(status_code=422, detail="empty playlist")
        info = entries[0]

    formats = []
    seen = set()

    for f in info.get("formats") or []:
        fid = f.get("format_id") or ""
        if not fid or not ALLOWED_FORMAT.match(fid):
            continue
        if f.get("vcodec") in (None, "none"):
            continue

        height = f.get("height") or 0
        ext = f.get("ext") or "mp4"
        progressive = f.get("acodec") not in (None, "none")
        key = (height, ext, progressive)

        if key in seen:
            continue
        seen.add(key)

        formats.append({
            "id": fid,
            "label": f"{height}p" if height else "video",
            "ext": ext,
            "progressive": progressive,
            "filesize": f.get("filesize") or f.get("filesize_approx"),
        })

    formats.sort(key=lambda x: (x["progressive"], x["label"]), reverse=True)

    return {
        "title": info.get("title") or "untitled",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "extractor": info.get("extractor_key") or "",
        "webpage_url": info.get("webpage_url") or url,
        "formats": formats[:12],
    }


@app.post("/api/resolve")
async def resolve(request: Request, payload: dict):
    rate_limit(request.client.host, RESOLVE_LIMIT)
    url = validate(payload.get("url", ""))

    try:
        data = await asyncio.get_running_loop().run_in_executor(None, extract, url)
    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=f"extract failed: {str(e)[:160]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"resolver error: {str(e)[:160]}")

    return data


@app.get("/api/download")
async def download(request: Request, url: str = Query(...), format_id: str = Query("best")):
    rate_limit(request.client.host, DOWNLOAD_LIMIT)
    url = validate(url)

    if not ALLOWED_FORMAT.match(format_id or "best"):
        raise HTTPException(status_code=400, detail="bad format")

    selector = (
        "bv*+ba/b" if format_id == "best"
        else f"{format_id}+ba/{format_id}/bv*+ba/b"
    )

    cmd = [
        "yt-dlp",
        "-f", selector,
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--no-part",
        "--merge-output-format", "mp4",
        "-o", "-",
        url,
    ]

    if COOKIE_FILE and Path(COOKIE_FILE).is_file():
        cmd[1:1] = ["--cookies", COOKIE_FILE]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=CHUNK,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="yt-dlp binary missing")

    filename = safe_name("video", "mp4")
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    }

    def stream():
        try:
            while True:
                if proc.poll() is not None and not proc.stdout.readable():
                    break
                buf = proc.stdout.read(CHUNK)
                if not buf:
                    break
                yield buf
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    return StreamingResponse(stream(), media_type="video/mp4", headers=headers)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
