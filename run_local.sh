#!/bin/sh
# PRESTIGE on your own Mac/Linux computer. Needs Python 3.13.
cd "$(dirname "$0")" || exit 1
[ -f .env ] || { echo "Copy .env.example to .env and fill in your FYERS details first."; exit 1; }
fail() { echo; echo "Setup failed: see the message above. Delete the .venv folder and run this again."; exit 1; }
if [ ! -f .venv/installed.ok ]; then
  echo "Installing libraries, first start only..."
  python3 -m venv .venv || fail
  .venv/bin/python -m pip install --upgrade pip || fail
  .venv/bin/python -m pip install -r requirements.txt || fail
  touch .venv/installed.ok
fi
.venv/bin/python tests/smoke_test.py || fail
( sleep 3; open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null ) &
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
