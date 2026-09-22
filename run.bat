@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv || goto :nopython
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
where ffmpeg >nul 2>nul || echo WARNING: ffmpeg not found on PATH - clip export and video download merging will not work. Install with: winget install Gyan.FFmpeg
start "" http://localhost:8765
.venv\Scripts\python -m app
pause
exit /b

:nopython
echo Python 3.11+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
