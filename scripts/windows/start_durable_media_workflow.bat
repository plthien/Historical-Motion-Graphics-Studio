@echo off
setlocal
cd /d "%~dp0\..\.."

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [ERROR] %PYTHON_EXE% was not found.
  exit /b 1
)

"%PYTHON_EXE%" -m app.durable_media_workflow_runner
