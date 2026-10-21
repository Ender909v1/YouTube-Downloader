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

import json

import imageio_ffmpeg
import yt_dlp
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

APP_VERSION = "2.0"  # bump together with the GitHub release tag (v2.0)
FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller .exe
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# The .exe cannot write next to itself reliably, so it keeps work files in %TEMP%.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "yt-downloader" if FROZEN else BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
JOB_TTL_SECONDS = 60 * 60  # delete finished jobs after 1 hour

YT_URL = re.compile(r"^https?://([a-z0-9-]+\.)*(youtube\.com|youtu\.be)/", re.I)

# Sign-in for age-restricted videos: yt-dlp downloads as the user, using the YouTube
# cookies from their own browser or from a cookies.txt file they exported.
SETTINGS_DIR = Path(os.environ.get("APPDATA", tempfile.gettempdir())) / "YouTube Downloader" if FROZEN else BASE_DIR
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
COOKIE_BROWSERS = ("firefox", "chrome", "edge", "brave")
COOKIE_SOURCES = ("none", "app", "file") + COOKIE_BROWSERS
# Written by desktop.py after the user accepts and signs in to Google in an app window.
APP_COOKIE_FILE = SETTINGS_DIR / "youtube-cookies.txt"
DEFAULT_SETTINGS = {"cookie_source": "none", "cookie_file": ""}

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


class QuietLogger:
    """yt-dlp logger that never writes to stdout/stderr (both are None in a windowed .exe)."""

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}
    if settings["cookie_source"] not in COOKIE_SOURCES:
        settings["cookie_source"] = "none"
    if settings["cookie_source"] == "app" and not APP_COOKIE_FILE.is_file():
        settings["cookie_source"] = "none"  # signed out, or the file was deleted
    return settings


def save_settings(settings: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def friendly_error(msg: str, settings: dict, browser_unreadable: bool = False) -> str:
    """Turn yt-dlp's sign-in and cookie errors into steps the user can follow."""
    signed_in = settings["cookie_source"] != "none"
    if browser_unreadable:
        # The job fell back to no sign-in because the browser's cookies were locked.
        msg = "failed to load cookies: " + msg
    elif "confirm your age" in msg or "not a bot" in msg or "members-only" in msg.lower():
        if not signed_in:
            return ("YouTube needs you to be signed in for this video (age-restricted). "
                    "Open Sign in in the menu, or use a cookies.txt file on the Cookies page. "
                    "The YouTube account must be age-verified.")
        if settings["cookie_source"] == "app":
            return ("YouTube still asks for sign-in. Your sign-in may have expired, or the account "
                    "is not age-verified. On the Sign in page, sign out, then sign in again.")
        if settings["cookie_source"] == "file":
            return ("YouTube still asks for sign-in. Your cookies.txt may be expired or from a "
                    "signed-out browser. Sign in to YouTube with an age-verified account, "
                    "then export a fresh cookies.txt file and choose it on the Cookies page.")
        return (f"YouTube still asks for sign-in. Make sure you are signed in to YouTube in "
                f"{settings['cookie_source'].title()} with an age-verified account.")
    if signed_in and settings["cookie_source"] in COOKIE_BROWSERS and (
            "cookie" in msg.lower() or "DPAPI" in msg or "decrypt" in msg.lower()):
        browser = settings["cookie_source"].title()
        if settings["cookie_source"] == "firefox":
            return ("Could not read your sign-in from Firefox. Close Firefox completely and try "
                    "again. If it still fails, use a cookies.txt file on the Cookies page.")
        # Chrome-based browsers now use app-bound cookie encryption that yt-dlp cannot
        # decrypt, so closing the browser rarely helps. Point straight at cookies.txt.
        return (f"{browser} protects its sign-in so this app cannot read it. Use a cookies.txt file "
                f"instead: in {browser}, sign in to YouTube, export cookies with the "
                "\"Get cookies.txt LOCALLY\" extension, then choose it on the Cookies page.")
    return msg


jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
cancel_events: dict[str, threading.Event] = {}  # set by /api/cancel, read by the job thread


def update(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs[job_id].update(fields)


def ydl_options(fmt: str, out_dir: Path, playlist: bool, hook, pp_hook=None,
                cookie_browser: str | None = None, cookie_file: Path | None = None) -> dict:
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
        "postprocessor_hooks": [pp_hook] if pp_hook else [],
    }
    if cookie_browser:
        opts["cookiesfrombrowser"] = (cookie_browser,)
    elif cookie_file:
        opts["cookiefile"] = str(cookie_file)
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

    cancel = cancel_events[job_id]
    settings = load_settings()
    cookie_browser = settings["cookie_source"] if settings["cookie_source"] in COOKIE_BROWSERS else None
    cookie_copy = None
    cookie_src = None
    if settings["cookie_source"] == "file" and settings["cookie_file"]:
        cookie_src = settings["cookie_file"]
    elif settings["cookie_source"] == "app":
        cookie_src = APP_COOKIE_FILE
    if cookie_src:
        # yt-dlp writes cookies back when it finishes, so give it a private copy
        # instead of the user's file. The copy is deleted when the job ends.
        cookie_copy = out_dir / ".cookies.txt"
    auth = {"cookie_browser": cookie_browser, "cookie_file": cookie_copy}

    def check_cancel(_=None):
        # Raising inside a yt-dlp hook stops the download at the next chunk.
        if cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")

    def hook(d):
        check_cancel()
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

    browser_unreadable = False

    def is_cookie_load_error(e: Exception) -> bool:
        # yt-dlp reports CookieLoadError through report_error, which (without ignoreerrors)
        # raises a DownloadError carrying the original exception in exc_info.
        if isinstance(e, yt_dlp.cookies.CookieLoadError):
            return True
        exc_info = getattr(e, "exc_info", None)
        return bool(exc_info) and isinstance(exc_info[1], yt_dlp.cookies.CookieLoadError)

    def run_ydl(target_urls: list[str], playlist: bool) -> None:
        nonlocal auth, browser_unreadable
        try:
            with yt_dlp.YoutubeDL(ydl_options(fmt, out_dir, playlist, hook, check_cancel, **auth)) as ydl:
                ydl.download(target_urls)
        except (yt_dlp.cookies.CookieLoadError, yt_dlp.utils.DownloadError) as e:
            if not (auth["cookie_browser"] and is_cookie_load_error(e)):
                raise
            # The browser's sign-in cannot be read (newer Chrome/Edge/Brave lock it).
            # Download without sign-in instead of failing every video, and say so.
            browser_unreadable = True
            auth = {"cookie_browser": None, "cookie_file": None}
            update(job_id, notes=[f"Could not read your {cookie_browser.title()} sign-in, so this "
                                  "was downloaded without sign-in. Age-restricted videos need the "
                                  "Sign in page or a cookies.txt file."])
            with yt_dlp.YoutubeDL(ydl_options(fmt, out_dir, playlist, hook, check_cancel, **auth)) as ydl:
                ydl.download(target_urls)

    try:
        if cookie_copy:
            try:
                shutil.copyfile(cookie_src, cookie_copy)
            except OSError:
                raise RuntimeError("Cannot open your saved sign-in. Sign in again on the Sign in "
                                   "page, or choose your cookies.txt again on the Cookies page.")
        if use_playlist:
            run_ydl(urls, True)
        else:
            update(job_id, total=len(urls))
            failed = []
            for i, url in enumerate(urls, 1):
                check_cancel()
                update(job_id, index=i, percent=0)
                try:
                    run_ydl([url], False)
                except yt_dlp.utils.DownloadError as e:
                    if len(urls) == 1:
                        raise
                    failed.append(f"{url}: {e}")
            if failed:
                update(job_id, warnings=failed)

        files = sorted(p.name for p in out_dir.iterdir() if p.suffix.lower() == f".{fmt}")
        if not files:
            raise RuntimeError("Nothing was downloaded. Check the link.")
        check_cancel()
        update(job_id, status="done", files=files, phase="done", percent=100)
    except Exception as e:  # report any failure to the UI
        if cancel.is_set():
            update(job_id, status="cancelled")
        else:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
            update(job_id, status="error", error=friendly_error(msg, settings, browser_unreadable))
    finally:
        if cookie_copy:
            cookie_copy.unlink(missing_ok=True)
        update(job_id, finished_at=time.time())
        cancel_events.pop(job_id, None)
        if cancel.is_set():
            shutil.rmtree(out_dir, ignore_errors=True)


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
    return render_template("index.html", version=APP_VERSION, cookie_path=str(APP_COOKIE_FILE))


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
    cancel_events[job_id] = threading.Event()
    threading.Thread(target=run_job, args=(job_id, urls, fmt, mode == "playlist"), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/settings")
def get_settings():
    return jsonify({**load_settings(), "app_signed_in": APP_COOKIE_FILE.is_file()})


@app.post("/api/signout")
def sign_out():
    if not request.is_json:
        return jsonify(error="Expected JSON."), 415
    APP_COOKIE_FILE.unlink(missing_ok=True)
    settings = load_settings()  # falls back to "none" now that the file is gone
    save_settings({"cookie_source": settings["cookie_source"], "cookie_file": settings["cookie_file"]})
    return jsonify({**settings, "app_signed_in": False})


@app.post("/api/settings")
def set_settings():
    # JSON only: a cross-site HTML form cannot send it without a CORS preflight.
    if not request.is_json:
        return jsonify(error="Expected JSON."), 415
    data = request.get_json(silent=True) or {}
    source = data.get("cookie_source", "none")
    cookie_file = str(data.get("cookie_file", "")).strip().strip('"')
    if source not in COOKIE_SOURCES:
        return jsonify(error="Unknown sign-in option."), 400
    if source == "file" and cookie_file and not Path(cookie_file).is_file():
        return jsonify(error="That cookies.txt file does not exist."), 400
    if source == "app" and not APP_COOKIE_FILE.is_file():
        return jsonify(error="Sign in to YouTube in this app first."), 400
    settings = {"cookie_source": source, "cookie_file": cookie_file}
    save_settings(settings)
    return jsonify(settings)


@app.post("/api/cancel/<job_id>")
def cancel(job_id):
    event = cancel_events.get(job_id)
    if not event:  # unknown job, or it already finished
        abort(404)
    event.set()
    update(job_id, phase="cancelling")
    return jsonify(ok=True)


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
