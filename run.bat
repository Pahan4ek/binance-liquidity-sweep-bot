@echo off
title Binance Sweep Bot Runner
cd /d "%~dp0"
echo ========================================================
echo   Starting Binance Sweep Bot with 24/7 Watchdog...
echo ========================================================
python watchdog.py
if errorlevel 1 (
    echo.
    echo [ERROR] Bot exited with an error.
)
echo.
pause
