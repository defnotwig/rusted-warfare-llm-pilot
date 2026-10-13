@echo off
setlocal
title LocalShorts Studio — Release Builder
cd /d "%~dp0"
echo ============================================================================
echo   Building LocalShorts Studio Portable ZIP ^& Windows Installer
echo ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-portable.ps1"
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-installer.ps1"
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo ============================================================================
echo   Release artifacts ready in dist\
echo ============================================================================
exit /b 0
