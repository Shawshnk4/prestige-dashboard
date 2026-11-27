"""PRESTIGE production package. On import, settings are read from a .env file in the project folder if one exists
(for running on your own computer); variables already set in the environment (e.g. on Render) always win."""
import os
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))


# Settings pasted into Render or .env often carry a stray space or line break at the end; remove it from every variable
# this program reads, before any module looks at them.
for _k in [k for k in os.environ if k.startswith(("FYERS_", "SUPABASE_", "DATABASE_URL", "ALLOWED_EMAILS", "CRON_SECRET",
                                                  "AUTH_DISABLED", "LIVE_START", "HISTORY_FROM", "QUOTE_TTL"))]:
    _v = os.environ[_k]
    if _v != _v.strip():
        os.environ[_k] = _v.strip()
