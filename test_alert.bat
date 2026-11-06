@echo off
cd /d "%~dp0"
rem Shows the full-screen signal alert once, marked as a test. Close it with Esc.
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" fullscreen_alert.py --test
if errorlevel 1 pause
