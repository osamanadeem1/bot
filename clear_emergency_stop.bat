@echo off
cd /d "%~dp0"
if exist "data\EMERGENCY_STOP" del "data\EMERGENCY_STOP"
echo Emergency stop cleared. Signals will resume on the next scan.
pause
