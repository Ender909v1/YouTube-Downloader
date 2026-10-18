@echo off
rem Builds "YouTube Downloader.exe" (single file, no console window)
rem into the folder above this one, so users only see the .exe.
cd /d "%~dp0"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "YouTube Downloader" ^
  --distpath .. ^
  --workpath build ^
  --specpath . ^
  --add-data "templates;templates" ^
  --collect-all imageio_ffmpeg ^
  --collect-submodules yt_dlp ^
  --collect-all webview ^
  desktop.py
