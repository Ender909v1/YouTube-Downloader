"""Desktop app: runs the Flask server in the background and shows it in a native window.

Run:  .venv\\Scripts\\pythonw desktop.py
Build .exe:  build.bat
"""
import shutil
import socket
import threading
import time
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import unquote

import webview

from app import APP_COOKIE_FILE, APP_VERSION, app, load_settings, save_settings

window = None

# Google's own sign-in page; after sign-in it returns to YouTube, which sets the YouTube cookies.
LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true&hl=en"
             "&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue"
             "%26app%3Ddesktop%26next%3D%252F")
SIGN_IN_TIMEOUT = 15 * 60  # seconds


def write_netscape_cookies(cookies, path) -> int:
    """Save pywebview cookies (a list of SimpleCookie) as a Netscape cookies.txt for yt-dlp."""
    lines = ["# Netscape HTTP Cookie File", "# Saved by YouTube Downloader after you signed in."]
    for jar in cookies:
        for name, m in jar.items():
            domain = m["domain"] or "www.youtube.com"
            try:
                expires = int(parsedate_to_datetime(m["expires"]).timestamp())
            except (TypeError, ValueError):
                expires = 0  # session cookie
            prefix = "#HttpOnly_" if m["httponly"] else ""
            lines.append("\t".join([
                prefix + domain, "TRUE" if domain.startswith(".") else "FALSE", m["path"] or "/",
                "TRUE" if m["secure"] else "FALSE", str(max(expires, 0)), name, m.value,
            ]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return len(lines) - 2


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """Functions the page can call as window.pywebview.api.<name>()."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._login_window = None

    def sign_in(self) -> dict:
        """Open Google's sign-in page in its own window (only after the user accepted in the page).

        The user types their password into Google, never into this app. Once YouTube has set its
        sign-in cookies, they are saved to APP_COOKIE_FILE and the window closes.
        """
        if self._login_window:
            return {"ok": False, "error": "The sign-in window is already open."}
        login = webview.create_window("Sign in to YouTube", LOGIN_URL, width=520, height=740)
        self._login_window = login
        closed = threading.Event()
        login.events.closed += lambda: closed.set()
        try:
            deadline = time.time() + SIGN_IN_TIMEOUT
            while not closed.wait(1) and time.time() < deadline:
                try:
                    url = login.get_current_url() or ""
                    if not url.startswith("https://www.youtube.com"):
                        continue
                    cookies = login.get_cookies() or []
                except Exception:
                    continue  # window still loading
                names = {name for jar in cookies for name in jar}
                if "LOGIN_INFO" in names and names & {"SAPISID", "__Secure-3PAPISID"}:
                    write_netscape_cookies(cookies, APP_COOKIE_FILE)
                    settings = load_settings()
                    save_settings({"cookie_source": "app", "cookie_file": settings["cookie_file"]})
                    return {"ok": True}
            if closed.is_set():
                return {"ok": False, "error": "The sign-in window was closed before sign-in finished."}
            return {"ok": False, "error": "Sign-in timed out. Try again."}
        finally:
            if not closed.is_set():
                login.destroy()
            self._login_window = None

    def pick_cookie_file(self) -> str:
        """Show an Open dialog for the user's exported cookies.txt. Returns the path or ''."""
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Cookie files (*.txt)", "All files (*.*)"))
        if not result:
            return ""
        return result if isinstance(result, str) else result[0]

    def save(self, path: str, filename: str) -> str:
        """Ask where to save, then copy the file from the local server. Returns the saved path or ''."""
        result = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=unquote(filename))
        if not result:
            return ""
        target = result if isinstance(result, str) else result[0]
        with urllib.request.urlopen(self.base_url + path) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return target


def main() -> None:
    global window
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False),
        daemon=True,
    ).start()
    # Wait until the server accepts connections so the window never shows an error page.
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    window = webview.create_window(
        f"YouTube Downloader v{APP_VERSION}", base_url, js_api=Api(base_url),
        width=820, height=780, min_size=(420, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
