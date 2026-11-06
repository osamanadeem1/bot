@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Checking the bot against MetaTrader 5. Keep MT5 open and logged in.
echo This takes about one minute...
echo.
".venv\Scripts\python.exe" diagnose.py
echo.
echo The full report is saved in data\diagnostic_report.txt
pause
