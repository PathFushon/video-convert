@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.pyw"
    exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
    start "" "%~dp0.venv\Scripts\python.exe" "%~dp0gui.pyw"
    exit /b 0
)

echo Virtual environment .venv not found. Creating and installing dependencies...
py -3 -m venv "%~dp0.venv"
if errorlevel 1 (
    echo Failed to create .venv. Make sure Python 3 is installed.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.pyw"
