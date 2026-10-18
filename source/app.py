"""YouTube downloader: Flask backend wrapping yt-dlp.

Run:  .venv\\Scripts\\python app.py   then open http://127.0.0.1:5000
"""
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

import imageio_ffmpeg
import yt_dlp
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller .exe
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# The .exe cannot write next to itself reliably, so it keeps work files in %TEMP%.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "yt-downloader" if FROZEN else BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
JOB_TTL_SECONDS = 60 * 60  # delete finished jobs after 1 hour

YT_URL = re.compile(r"^https?://([a-z0-9-]+\.)*(youtube\.com|youtu\.be)/", re.I)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


class QuietLogger:
    """yt-dlp logger that never writes to stdout/stderr (both are None in a windowed .exe)."""

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def update(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs[job_id].update(fields)


def ydl_options(fmt: str, out_dir: Path, playlist: bool, hook) -> dict:
    name = "%(playlist_index)03d - %(title)s.%(ext)s" if playlist else "%(title)s.%(ext)s"
    opts = {
        "outtmpl": str(out_dir / name),
        "ffmpeg_location": FFMPEG,
        "noplaylist": not playlist,
        "ignoreerrors": playlist,  # skip private/removed videos inside a playlist
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": QuietLogger(),
        "progress_hooks": [hook],
    }
    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            # "0" = LAME VBR V0 (~245 kbps), the best MP3 quality setting.
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "FFmpegMetadata"},
        ]
        # Loud YouTube uploads peak above 0 dBFS; without headroom the MP3 clips
        # and crackles on loud parts. 1.5 dB is inaudible as a volume change.
        opts["postprocessor_args"] = {"extractaudio": ["-af", "volume=-1.5dB"]}
    else:
        # Prefer H.264 + AAC: YouTube's best MP4 video is often AV1, which the default
        # Windows player cannot decode (plays sound with no picture).
        opts["format"] = "bv*+ba/b"
        opts["format_sort"] = ["vcodec:h264", "res", "fps", "acodec:aac"]
        opts["merge_output_format"] = "mp4"
    return opts


def run_job(job_id: str, urls: list[str], fmt: str, playlist_mode: bool) -> None:
    out_dir = DOWNLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # A single playlist URL is expanded by yt-dlp; a pasted list of links is looped here.
    use_playlist = playlist_mode and len(urls) == 1

    def hook(d):
        info = d.get("info_dict") or {}
        fields = {"title": info.get("title") or ""}
        if use_playlist and info.get("playlist_index"):
            fields["index"] = info["playlist_index"]
            fields["total"] = info.get("n_entries") or info.get("playlist_count") or 0
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            fields["percent"] = round(d.get("downloaded_bytes", 0) * 100 / total, 1) if total else 0
            fields["phase"] = "downloading"
        elif d["status"] == "finished":
            fields["percent"] = 100
            fields["phase"] = "converting"
        update(job_id, **fields)

    try:
        if use_playlist:
            with yt_dlp.YoutubeDL(ydl_options(fmt, out_dir, True, hook)) as ydl:
                ydl.download(urls)
        else:
            update(job_id, total=len(urls))
            failed = []
            for i, url in enumerate(urls, 1):
                update(job_id, index=i, percent=0)
                try:
                    with yt_dlp.YoutubeDL(ydl_options(fmt, out_dir, False, hook)) as ydl:
                        ydl.download([url])
                except yt_dlp.utils.DownloadError as e:
                    if len(urls) == 1:
                        raise
                    failed.append(f"{url}: {e}")
            if failed:
                update(job_id, warnings=failed)

        files = sorted(p.name for p in out_dir.iterdir() if p.suffix.lower() == f".{fmt}")
        if not files:
            raise RuntimeError("Nothing was downloaded. Check the link.")
        update(job_id, status="done", files=files, phase="done", percent=100)
    except Exception as e:  # report any failure to the UI
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
        update(job_id, status="error", error=msg)
    finally:
        update(job_id, finished_at=time.time())


def cleanup_old_jobs() -> None:
    now = time.time()
    with jobs_lock:
        old = [j for j, v in jobs.items() if v.get("finished_at") and now - v["finished_at"] > JOB_TTL_SECONDS]
        for j in old:
            jobs.pop(j)
    for j in old:
        shutil.rmtree(DOWNLOAD_DIR / j, ignore_errors=True)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/start")
def start():
    data = request.get_json(silent=True) or {}
    fmt = data.get("format")
    mode = data.get("mode")
    urls = [u.strip() for u in re.split(r"[\s,]+", data.get("url", "")) if u.strip()]

    if fmt not in ("mp3", "mp4") or mode not in ("link", "playlist"):
        return jsonify(error="Invalid request."), 400
    if not urls:
        return jsonify(error="Please paste a YouTube link."), 400
    bad = [u for u in urls if not YT_URL.match(u)]
    if bad:
        return jsonify(error=f"Not a YouTube link: {bad[0]}"), 400
    if mode == "link" and len(urls) > 1:
        return jsonify(error="Link tab takes one video. Use the Playlist tab for many."), 400
    if mode == "playlist" and len(urls) == 1 and "list=" not in urls[0]:
        return jsonify(error="That is not a playlist link (it has no 'list='). "
                             "Paste a playlist URL, or several video links, one per line."), 400

    cleanup_old_jobs()
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "running", "phase": "starting", "percent": 0,
                        "index": 0, "total": 0, "title": "", "files": [], "format": fmt}
    threading.Thread(target=run_job, args=(job_id, urls, fmt, mode == "playlist"), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        return jsonify(job)


@app.get("/api/file/<job_id>/<path:name>")
def get_file(job_id, name):
    if job_id not in jobs:
        abort(404)
    return send_from_directory(DOWNLOAD_DIR / job_id, name, as_attachment=True)


@app.get("/api/zip/<job_id>")
def get_zip(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        abort(404)
    job_dir = DOWNLOAD_DIR / job_id
    zip_path = job_dir / f"playlist-{job['format']}.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
            for name in job["files"]:
                z.write(job_dir / name, name)
    return send_from_directory(job_dir, zip_path.name, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), threaded=True)
