@echo off
title Massing Explorer UI
cd /d "%~dp0"

echo.
echo  Massing Explorer — studio UI
echo  ----------------------------
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.10+ and try again.
  pause
  exit /b 1
)

set PYTHONPATH=%~dp0src;%PYTHONPATH%

echo Installing / checking package...
python -m pip install -e . -q
if errorlevel 1 (
  echo pip install failed.
  pause
  exit /b 1
)

echo Opening http://127.0.0.1:8765/
echo Close this window or press Ctrl+C to stop the server.
echo.

python -m massing_explorer.ui_app
if errorlevel 1 (
  echo.
  echo UI exited with an error.
  pause
)
