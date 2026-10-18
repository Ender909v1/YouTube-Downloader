"""Desktop app: runs the Flask server in the background and shows it in a native window.

Run:  .venv\\Scripts\\pythonw desktop.py
Build .exe:  build.bat
"""
import shutil
import socket
import threading
import time
import urllib.request
from urllib.parse import unquote

import webview

from app import APP_VERSION, app

window = None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """Functions the page can call as window.pywebview.api.<name>()."""

    def __init__(self, base_url: str):
        self.base_url = base_url

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
