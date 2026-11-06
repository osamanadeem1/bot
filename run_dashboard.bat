@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
rem Skip Streamlit's one-time email question so the window never waits for input.
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)
echo Opening the dashboard in your browser at http://localhost:8501
echo Keep this window open while you use the dashboard. Close it to stop the bot.
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats false
pause
