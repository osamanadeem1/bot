@echo off
setlocal
cd /d "%~dp0"
echo === Personal Trading Bot - setup ===
set "PYEXE="
for %%V in (312 313 311) do if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (312 313 311) do if not defined PYEXE if exist "%ProgramFiles%\Python%%V\python.exe" set "PYEXE=%ProgramFiles%\Python%%V\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
if not defined PYEXE (
  echo Python 3 was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  echo Then run setup.bat again.
  pause
  exit /b 1
)
echo Using Python: "%PYEXE%"
if not exist ".venv\Scripts\python.exe" echo Creating virtual environment...
if not exist ".venv\Scripts\python.exe" "%PYEXE%" -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Could not create the virtual environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installing packages failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo Installing the MetaTrader5 package - optional, needed only for live MT5 data...
".venv\Scripts\python.exe" -m pip install MetaTrader5
if errorlevel 1 echo WARNING: MetaTrader5 did not install. Demo and CSV modes still work.
if not exist "settings.json" copy "settings.example.json" "settings.json" >nul
if not exist "data" mkdir "data"
echo.
echo Setup complete. Next: run_tests.bat, then run_dashboard.bat
pause
