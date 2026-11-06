@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Downloading about 3 months of 1-minute history from MetaTrader 5. Keep MT5 open...
echo.
".venv\Scripts\python.exe" download_history.py
pause
