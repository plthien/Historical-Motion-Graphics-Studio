@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title YouTube AI Studio V3 Launcher

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "PYTHON_STAMP=%CD%\.venv\.yas3_requirements.sha256"
set "NPM_STAMP=%CD%\node_modules\.yas3_package_lock.sha256"
set "REQ_FILE=%CD%\requirements.txt"
set "NPM_LOCK_FILE=%CD%\package-lock.json"

call :ensure_python_environment
if errorlevel 1 goto :failed

call :ensure_python_dependencies
if errorlevel 1 goto :failed

call :ensure_frontend_dependencies
if errorlevel 1 goto :failed

call :upgrade_database
if errorlevel 1 goto :failed

echo.
echo [READY] Dependencies are installed and current. Database schema is current.
echo Starting Backend API at http://127.0.0.1:8000
echo Starting Durable Media Workflow Host. This process resumes Scene Assets V2, Visual References V2, and Voice operations from SQLite.
echo Starting Dedicated Durable Render Host. Render is scheduled and recovered from SQLite; Redis/RQ is not required.
echo Starting Frontend at http://127.0.0.1:5173

start "YAS3 Backend API" "%ComSpec%" /k call "%CD%\scripts\windows\start_backend.bat"
start "YAS3 Durable Media Workflow" "%ComSpec%" /k call "%CD%\scripts\windows\start_durable_media_workflow.bat"
start "YAS3 Durable Render" "%ComSpec%" /k call "%CD%\scripts\windows\start_durable_render.bat"
start "YAS3 Frontend" "%ComSpec%" /k call "%CD%\scripts\windows\start_frontend.bat"

timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:5173"

endlocal
exit /b 0

:ensure_python_environment
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
  if not errorlevel 1 exit /b 0

  echo [ERROR] The existing .venv does not use Python 3.12.
  echo Delete the .venv folder, then run start.bat again.
  exit /b 1
)

echo [SETUP] Creating Python 3.12 virtual environment...
set "BOOTSTRAP_PYTHON="
where py.exe >nul 2>&1
if not errorlevel 1 (
  py -3.12 -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
  if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3.12"
)

if not defined BOOTSTRAP_PYTHON (
  where python.exe >nul 2>&1
  if not errorlevel 1 (
    python -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') * 8 == 64 else 1)" >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
  )
)

if not defined BOOTSTRAP_PYTHON (
  echo [ERROR] Python 3.12 x64 was not found.
  echo Install it, reopen this folder, then run start.bat again.
  echo Suggested command:
  echo   winget install -e --id Python.Python.3.12
  exit /b 1
)

%BOOTSTRAP_PYTHON% -m venv ".venv"
if errorlevel 1 (
  echo [ERROR] Failed to create .venv.
  exit /b 1
)

if not exist "%PYTHON_EXE%" (
  echo [ERROR] .venv was created without a usable Python executable.
  exit /b 1
)
exit /b 0

:ensure_python_dependencies
if not exist "%REQ_FILE%" (
  echo [ERROR] requirements.txt was not found.
  exit /b 1
)

set "REQ_HASH="
for /f "usebackq delims=" %%H in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath $env:REQ_FILE).Hash.ToLowerInvariant()"`) do set "REQ_HASH=%%H"
if not defined REQ_HASH (
  echo [ERROR] Unable to calculate the requirements.txt fingerprint.
  exit /b 1
)

set "INSTALLED_REQ_HASH="
if exist "%PYTHON_STAMP%" set /p INSTALLED_REQ_HASH=<"%PYTHON_STAMP%"

set "PYTHON_DEPS_READY=0"
if /i "!INSTALLED_REQ_HASH!"=="!REQ_HASH!" (
  "%PYTHON_EXE%" -c "import fastapi, uvicorn, sqlalchemy" >nul 2>&1
  if not errorlevel 1 set "PYTHON_DEPS_READY=1"
)

if "!PYTHON_DEPS_READY!"=="1" (
  echo [OK] Python dependencies are current.
  exit /b 0
)

echo [SETUP] Installing Python dependencies. This can take several minutes on the first run...
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [ERROR] Failed to update pip, setuptools, or wheel.
  exit /b 1
)

"%PYTHON_EXE%" -m pip install --disable-pip-version-check -r "%REQ_FILE%"
if errorlevel 1 (
  echo [ERROR] Python dependency installation failed.
  echo Check the error above, your network, and that Python 3.12 x64 is being used.
  exit /b 1
)

"%PYTHON_EXE%" -c "import fastapi, uvicorn, sqlalchemy" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python dependencies were installed but the runtime import check failed.
  exit /b 1
)

>"%PYTHON_STAMP%" echo !REQ_HASH!
echo [OK] Python dependencies installed.
exit /b 0

:ensure_frontend_dependencies
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm.cmd was not found.
  echo Install Node.js LTS, reopen this folder, then run start.bat again.
  exit /b 1
)

set "NPM_HASH_SOURCE=%CD%\package.json"
if exist "%NPM_LOCK_FILE%" set "NPM_HASH_SOURCE=%NPM_LOCK_FILE%"
set "NPM_HASH_FILE=!NPM_HASH_SOURCE!"
set "NPM_HASH="
for /f "usebackq delims=" %%H in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath $env:NPM_HASH_FILE).Hash.ToLowerInvariant()"`) do set "NPM_HASH=%%H"
if not defined NPM_HASH (
  echo [ERROR] Unable to calculate the frontend dependency fingerprint.
  exit /b 1
)

set "INSTALLED_NPM_HASH="
if exist "%NPM_STAMP%" set /p INSTALLED_NPM_HASH=<"%NPM_STAMP%"

if /i "!INSTALLED_NPM_HASH!"=="!NPM_HASH!" if exist "node_modules\.bin\vite.cmd" (
  echo [OK] Frontend dependencies are current.
  exit /b 0
)

echo [SETUP] Installing frontend dependencies. This can take several minutes on the first run...
if exist "%NPM_LOCK_FILE%" (
  call npm.cmd ci --no-audit --no-fund
) else (
  call npm.cmd install --no-audit --no-fund
)
if errorlevel 1 (
  echo [ERROR] Frontend dependency installation failed.
  echo Check the npm error above, your network, or registry configuration.
  exit /b 1
)

if not exist "node_modules\.bin\vite.cmd" (
  echo [ERROR] Frontend dependencies were installed but Vite was not found.
  exit /b 1
)

>"%NPM_STAMP%" echo !NPM_HASH!
echo [OK] Frontend dependencies installed.
exit /b 0

:upgrade_database
echo [SETUP] Applying database migrations before runtime hosts start...
"%PYTHON_EXE%" -c "from app.db.migrations import upgrade_database; upgrade_database()"
if errorlevel 1 (
  echo [ERROR] Database migration failed. Runtime processes were not started.
  exit /b 1
)
echo [OK] Database schema is current.
exit /b 0

:failed
echo.
echo Startup was stopped because setup did not complete successfully.
echo Fix the reported error, then run start.bat again.
pause
endlocal
exit /b 1
