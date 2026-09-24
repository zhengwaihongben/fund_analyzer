@echo off
chcp 65001 > nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo Done. Run: python fund_analyzer_gui.py
pause