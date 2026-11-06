@echo off
cd /d "%~dp0"
if not exist "data" mkdir "data"
> "data\EMERGENCY_STOP" echo Stopped with emergency_stop.bat on %date% %time%
echo EMERGENCY STOP is ON. The bot will output NO TRADE for every symbol until you clear it.
echo To resume: run clear_emergency_stop.bat or press "Resume signals" in the dashboard.
pause
