#!/bin/sh
# PRESTIGE on your own Mac/Linux computer. Needs Python 3.13.
cd "$(dirname "$0")"
[ -f .env ] || { echo "Copy .env.example to .env and fill in your FYERS details first."; exit 1; }
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt; }
.venv/bin/python tests/smoke_test.py
( sleep 3; open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null ) &
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
