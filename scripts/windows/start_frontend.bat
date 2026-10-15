@echo off
setlocal
cd /d "%~dp0\..\.."
title YAS3 Frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Frontend stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
