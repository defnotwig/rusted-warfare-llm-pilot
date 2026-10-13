@echo off
setlocal
title LocalShorts Studio — Free Local AI Shorts Factory
cd /d "%~dp0"
echo ============================================================================
echo   LocalShorts Studio v0.1.0 — Free Local AI Shorts Factory
echo   Local-First ^& Zero-Subscription YouTube Shorts Production
echo ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] LocalShorts Studio exited with code %ERRORLEVEL%.
    pause
)
