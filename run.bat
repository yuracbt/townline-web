@echo off
REM TownLine Web - local run script (Windows).
REM First run: creates .venv and installs dependencies. Then starts the app.
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 is not installed. Get it at https://www.python.org/downloads/
  exit /b 1
)
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment ^(one-time setup^)...
  py -m venv .venv
)
.venv\Scripts\pip install -q -r requirements.txt
echo Starting TownLine Web on http://localhost:5000 ...
echo ^(Ctrl+C to stop^)
.venv\Scripts\python app.py
