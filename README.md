<div align="center">

# 🎬 YouTube Downloader

**Download YouTube videos as MP4 or music as MP3 — single links or whole playlists.**
A small, free Windows app. No install, no ads, no account.

![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![yt-dlp](https://img.shields.io/badge/powered%20by-yt--dlp-FF0000?logo=youtube&logoColor=white)

[**⬇️ Download the app**](https://github.com/Ender909v1/YouTube-Downloader/releases/latest)

<img src="docs/screenshot.png" alt="YouTube Downloader app window" width="640">

</div>

---

## ✨ Features

| | |
|---|---|
| 🔗 **Link tab** | Paste one video link and get a single MP4 or MP3 file. |
| 📃 **Playlist tab** | Paste a playlist link, or several video links (one per line). Save each file, or everything at once as a **.zip**. |
| 🎵 **MP3** | Best-quality VBR audio (~245 kbps) with title metadata, no clipping. |
| 🎞️ **MP4** | H.264 video + AAC audio, up to 1080p. Plays in the built-in Windows player. |
| 🔢 **Ordered** | Playlist files are numbered in playlist order: `001 - Title.mp3`, `002 - Title.mp3`, … |
| 📦 **All-in-one** | One `.exe` with FFmpeg built in. Nothing else to install. |
| 🌙 **Dark mode** | Follows your Windows light/dark setting. |

## 🚀 Getting started

1. Download **`YouTube Downloader.exe`** from the [latest release](https://github.com/Ender909v1/YouTube-Downloader/releases/latest).
2. Double-click it. The app opens in its own window (first start takes a few seconds).
3. Pick a tab, paste a link, choose **MP4** or **MP3**, and press **Generate**.
4. When it finishes, click **Download** and choose where to save.

> [!NOTE]
> The app is not code-signed, so Windows may show **"Windows protected your PC"** the first time.
> Click **More info → Run anyway**.

## 🛠️ Build from source

You need **Windows** and **Python 3.10+**.

```bat
git clone https://github.com/Ender909v1/YouTube-Downloader.git
cd YouTube-Downloader\source
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
build.bat
```

`build.bat` creates `YouTube Downloader.exe` in the repository root.

Run it without building:

| Mode | Command |
|---|---|
| Desktop window | `.venv\Scripts\python desktop.py` |
| In your browser | `.venv\Scripts\python app.py`, then open <http://127.0.0.1:5000> |

## 📁 Project structure

```
YouTube-Downloader/
├── source/
│   ├── app.py              # Flask server: runs yt-dlp in the background, reports progress
│   ├── desktop.py          # Native window (pywebview) + Save dialog
│   ├── templates/
│   │   └── index.html      # The UI
│   ├── build.bat           # Builds the single-file .exe with PyInstaller
│   └── requirements.txt
└── docs/
    └── screenshot.png
```

## ❓ Troubleshooting

<details>
<summary><b>Downloads suddenly stopped working</b></summary>

YouTube changes often. Update yt-dlp and rebuild:

```bat
cd source
.venv\Scripts\python -m pip install -U yt-dlp
build.bat
```
</details>

<details>
<summary><b>An MP4 plays sound but shows no picture</b></summary>

Files made by older versions may use the AV1 codec. Download them again with the current version (H.264), or install the free **AV1 Video Extension** from the Microsoft Store.
</details>

<details>
<summary><b>Some videos in a playlist are missing</b></summary>

Private, deleted, or age-restricted videos are skipped so the rest of the playlist can still finish.
</details>

## 🙏 Built with

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the download engine
- [Flask](https://flask.palletsprojects.com/) — local web server for the UI
- [pywebview](https://pywebview.flowrl.com/) — native desktop window
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) — bundled FFmpeg
- [PyInstaller](https://pyinstaller.org/) — single-file `.exe`

## ⚠️ Disclaimer

This tool is for personal use. Only download videos you own or have permission to download, and respect [YouTube's Terms of Service](https://www.youtube.com/t/terms) and copyright law.
