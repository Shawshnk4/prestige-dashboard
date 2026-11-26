@echo off
REM PRESTIGE on your own Windows computer. Needs Python 3.13 (python.org, tick "Add python.exe to PATH").
cd /d "%~dp0"
if not exist .env (
  echo Copy .env.example to .env and fill in your FYERS details first.
  pause
  exit /b 1
)
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python tests\smoke_test.py
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
