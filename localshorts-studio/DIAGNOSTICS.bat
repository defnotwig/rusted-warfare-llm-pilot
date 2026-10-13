@echo off
setlocal
title LocalShorts Studio — System Diagnostics
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\diagnostics.ps1" %*
exit /b %ERRORLEVEL%
