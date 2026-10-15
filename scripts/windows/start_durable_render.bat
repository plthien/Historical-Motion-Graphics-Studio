@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python environment not found. Run start.bat first.
  exit /b 1
)

"%PYTHON_EXE%" -c "from app.core.config import get_settings; import pathlib,shutil; s=get_settings(); resolve=lambda v: str(pathlib.Path(v).resolve()) if pathlib.Path(v).exists() else (shutil.which(v) or ''); ff=resolve(s.ffmpeg_path); fp=resolve(s.ffprobe_path); print('[OK] FFmpeg:', ff) if ff else None; print('[OK] FFprobe:', fp) if fp else None; raise SystemExit(0 if ff and fp else 1)"
if errorlevel 1 (
  echo [ERROR] FFmpeg and FFprobe must be available before Durable Render starts.
  echo Configure FFMPEG_PATH / FFPROBE_PATH or add both executables to PATH.
  exit /b 1
)

"%PYTHON_EXE%" -m app.durable_render_runner
exit /b %errorlevel%
