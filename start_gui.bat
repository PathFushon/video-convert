@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Creating virtual environment .venv...
    py -3 -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo Failed to create .venv. Make sure Python 3 is installed.
        pause
        exit /b 1
    )
)

"%~dp0.venv\Scripts\python.exe" -c "import imageio_ffmpeg, pywhispercpp, requests" >nul 2>&1
if errorlevel 1 (
    echo Installing/updating dependencies (FFmpeg + Whisper AI)...
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.pyw"
) else (
    start "" "%~dp0.venv\Scripts\python.exe" "%~dp0gui.pyw"
)
exit /b 0
