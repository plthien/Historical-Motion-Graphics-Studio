@echo off
setlocal
cd /d "%~dp0\..\.."
title YAS3 Backend API
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Backend API stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
