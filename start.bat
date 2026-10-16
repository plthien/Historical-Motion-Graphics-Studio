@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Historical Motion Graphics Studio - P1
where powershell.exe >nul 2>&1
if errorlevel 1 (echo [ERROR] Windows PowerShell is required. & exit /b 1)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start-history.ps1" %*
if errorlevel 1 (echo [ERROR] History Studio startup failed. & exit /b 1)
endlocal
