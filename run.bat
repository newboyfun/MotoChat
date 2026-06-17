@echo off
chcp 65001 >nul
title MotoChat - Local AI Chat
cls
echo ====================================
echo   MotoChat - Local AI Chat
echo ====================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv!
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat
if not exist ".venv\.deps_installed" (
    echo [INFO] Installing dependencies...
    .venv\Scripts\python.exe -m pip install fastapi uvicorn python-multipart openai requests SQLAlchemy emoji --quiet --disable-pip-version-check
    if not errorlevel 1 (
        echo installed > ".venv\.deps_installed"
    )
) else (
    echo [INFO] Dependencies already installed.
)

echo.
echo [INFO] Starting MotoChat...
echo [INFO] Open http://127.0.0.1:7860 in your browser
echo.
.venv\Scripts\python.exe src\run.py
pause
