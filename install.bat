@echo off
chcp 65001 > nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

rem 建立專案內虛擬環境，讓 launch.vbs 能用相對路徑找到解釋器
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
)

set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt

echo.
echo Done. Double-click launch.vbs, or run: .venv\Scripts\python.exe fund_analyzer_gui.py
pause
