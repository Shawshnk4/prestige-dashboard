"""FYERS access: automatic daily login (TOTP + PIN), daily candles and quotes, with a rate limiter.

Credentials come only from environment variables (set them in Render's Environment settings, never in code):
  FYERS_CLIENT_ID   app id from the FYERS API dashboard, e.g. ABCD1234-100
  FYERS_SECRET_KEY  the app's secret
  FYERS_REDIRECT_URI the app's redirect URL exactly as registered (default: FYERS's own redirect page)
  FYERS_FY_ID       FYERS client id used to sign in (e.g. XY12345)
  FYERS_PIN         4-digit trading PIN
  FYERS_TOTP_KEY    the TOTP secret shown when external 2FA (TOTP) is enabled on the FYERS account
The access token is kept in the database (app_state) and reused until it expires (FYERS tokens end every morning)."""
from __future__ import annotations
import base64, json, os, threading, time
from collections import deque
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
from . import db

IST = ZoneInfo("Asia/Kolkata")
VAGATOR = "https://api-t2.fyers.in/vagator/v2"
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
LOG_DIR = os.getenv("FYERS_LOG_DIR", "/tmp")
TOKEN_KEY = "fyers_token"


class FyersError(RuntimeError):
    pass


def _env(k, default=None, required=True):
    v = os.getenv(k, default)
    if required and not v:
        raise FyersError(f"{k} is not set in the server environment")
    return v


def configured() -> bool:
    return all(os.getenv(k) for k in ("FYERS_CLIENT_ID", "FYERS_SECRET_KEY", "FYERS_FY_ID", "FYERS_PIN", "FYERS_TOTP_KEY"))


SAMPLES = {"FYERS_CLIENT_ID": "ABCD1234-100", "FYERS_SECRET_KEY": "your-app-secret", "FYERS_FY_ID": "your-fyers-client-id",
           "FYERS_PIN": "your-4-digit-pin", "FYERS_TOTP_KEY": "your-totp-secret-key"}


def totp_secret(raw: str) -> str:
    """the TOTP secret from FYERS_TOTP_KEY; accepts the bare key (any case, with spaces) or the whole otpauth:// text"""
    s = (raw or "").strip().strip("'\"")
    if s.lower().startswith("otpauth://"):
        s = parse_qs(urlparse(s).query).get("secret", [""])[0]
    return s.replace(" ", "").upper().rstrip("=")


def settings_problems() -> list:
    """plain-language problems with the FYERS settings, without revealing their values"""
    import re
    out = []
    for k, sample in SAMPLES.items():
        v = (os.getenv(k) or "").strip()
        if not v:
            out.append(f"{k} is empty in .env")
        elif v == sample:
            out.append(f"{k} still has the sample value from .env.example: put your own value there")
    cid = (os.getenv("FYERS_CLIENT_ID") or "").strip()
    if cid and cid != SAMPLES["FYERS_CLIENT_ID"] and not re.fullmatch(r"[A-Z0-9]+-\d{3}", cid):
        out.append("FYERS_CLIENT_ID should be the App ID shown on myapi.fyers.in, like ABCD1234-100 (letters/digits, a dash, 3 digits)")
    pin = (os.getenv("FYERS_PIN") or "").strip()
    if pin and pin != SAMPLES["FYERS_PIN"] and not pin.isdigit():
        out.append("FYERS_PIN should be your 4-digit FYERS PIN (digits only)")
    raw = (os.getenv("FYERS_TOTP_KEY") or "").strip()
    if raw and raw != SAMPLES["FYERS_TOTP_KEY"]:
        t = totp_secret(raw)
        if re.fullmatch(r"\d{6}", t):
            out.append("FYERS_TOTP_KEY is a 6-digit code. It needs the long secret key from the TOTP setup (QR code), "
                       "not the code that changes every 30 seconds")
        elif not re.fullmatch(r"[A-Z2-7]+", t):
            bad = sorted(set(c for c in t if not re.fullmatch(r"[A-Z2-7]", c)))
            out.append("FYERS_TOTP_KEY is not a TOTP secret key: a key uses only letters A-Z and digits 2-7, but this one "
                       f"contains {' '.join(repr(c) for c in bad[:6])}. Copy the key again from the TOTP setup (QR code)")
        elif len(t) < 16:
            out.append(f"FYERS_TOTP_KEY looks too short ({len(t)} characters; FYERS keys are usually 32): copy the whole key")
    return out


# ------------------------------------------------------------------ rate limiter (FYERS: per second / minute caps)
class Limiter:
    def __init__(self, per_sec, per_min):
        self.per_sec, self.per_min = per_sec, per_min
        self.t = deque(); self.lock = threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                while self.t and now - self.t[0] > 60: self.t.popleft()
                last_sec = sum(1 for x in self.t if now - x < 1)
                if len(self.t) < self.per_min and last_sec < self.per_sec:
                    self.t.append(now); return
                sleep = 0.25 if last_sec >= self.per_sec else max(0.25, 60 - (now - self.t[0]) + 0.05)
            time.sleep(sleep)


LIMIT = Limiter(int(os.getenv("FYERS_MAX_PER_SEC", "4")), int(os.getenv("FYERS_MAX_PER_MIN", "45")))


# ------------------------------------------------------------------ login
def _b64(s):
    return base64.b64encode(str(s).encode()).decode()


def _post(url, payload, session=None, step=""):
    import requests
    r = (session or requests).post(url, json=payload, headers=HEADERS, timeout=30)
    try:
        return r.json()
    except ValueError:
        raise FyersError(f"{step}: unexpected response from FYERS (HTTP {r.status_code})")


def _login() -> str:
    """send OTP -> verify TOTP -> verify PIN -> auth code -> access token"""
    import pyotp, requests
    from fyers_apiv3 import fyersModel
    client_id, secret = _env("FYERS_CLIENT_ID"), _env("FYERS_SECRET_KEY")
    probs = settings_problems()
    if probs:
        raise FyersError("Fix .env, then close and restart PRESTIGE: " + "; ".join(probs))
    fy_id, pin, totp = _env("FYERS_FY_ID"), _env("FYERS_PIN").strip(), totp_secret(_env("FYERS_TOTP_KEY"))
    redirect = _env("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html")
    r1 = _post(f"{VAGATOR}/send_login_otp_v2", {"fy_id": _b64(fy_id), "app_id": "2"}, step="send OTP")
    if "request_key" not in r1:
        raise FyersError(f"FYERS login (first step) failed: {r1.get('message', '')} (check FYERS_FY_ID is your FYERS client ID)")
    if datetime.now().second % 30 > 26:            # do not straddle a TOTP window
        time.sleep(5)
    r2 = _post(f"{VAGATOR}/verify_otp", {"request_key": r1["request_key"], "otp": pyotp.TOTP(totp).now()}, step="verify TOTP")
    if "request_key" not in r2:
        raise FyersError(f"FYERS login (TOTP) failed: {r2.get('message', '')} (check FYERS_TOTP_KEY is the key of the TOTP now enabled on this FYERS account, and that your computer's clock is correct)")
    ses = requests.Session()
    r3 = _post(f"{VAGATOR}/verify_pin_v2", {"request_key": r2["request_key"], "identity_type": "pin", "identifier": _b64(pin)},
               session=ses, step="verify PIN")
    tok = (r3.get("data") or {}).get("access_token")
    if not tok:
        raise FyersError(f"FYERS login (PIN) failed: {r3.get('message', '')} (check FYERS_PIN)")
    ses.headers.update({"authorization": f"Bearer {tok}"})
    r4 = _post("https://api-t1.fyers.in/api/v3/token",
               {"fyers_id": fy_id, "app_id": client_id[:-4], "redirect_uri": redirect, "appType": client_id[-3:],
                "code_challenge": "", "state": "None", "scope": "", "nonce": "", "response_type": "code", "create_cookie": True},
               session=ses, step="auth code")
    url = r4.get("Url") or r4.get("url") or (r4.get("data") or {}).get("auth")
    if not url:
        raise FyersError(f"FYERS login (auth code) failed: {r4.get('message', 'check FYERS_CLIENT_ID and FYERS_REDIRECT_URI')}")
    code = parse_qs(urlparse(url).query).get("auth_code", [url])[0]
    s = fyersModel.SessionModel(client_id=client_id, secret_key=secret, redirect_uri=redirect, response_type="code",
                                grant_type="authorization_code")
    s.set_token(code)
    out = s.generate_token()
    if "access_token" not in out:
        raise FyersError(f"FYERS login (token) failed: {out.get('message', 'check FYERS_SECRET_KEY')}")
    return out["access_token"]


def _expiry_cutoff(now=None):
    """tokens are treated as expired at 06:30 IST each day (FYERS resets access tokens every morning)"""
    now = now or datetime.now(IST)
    c = now.replace(hour=6, minute=30, second=0, microsecond=0)
    return c if now >= c else c - timedelta(days=1)


_client = None
_client_lock = threading.Lock()


def client(force=False):
    """a FyersModel with a valid token: from memory, else from the database, else a fresh login"""
    global _client
    from fyers_apiv3 import fyersModel
    with _client_lock:
        cutoff = _expiry_cutoff()
        if not force and _client is not None and _client[1] >= cutoff:
            return _client[0]
        tok = None
        if not force:
            v, _ = db.get_state(TOKEN_KEY)
            if v:
                d = json.loads(v); t = datetime.fromisoformat(d["at"])
                if t >= cutoff: tok = d["token"]
        made = datetime.now(IST)
        if tok is None:
            tok = _login()
            db.set_state(TOKEN_KEY, json.dumps({"token": tok, "at": made.isoformat()}))
        m = fyersModel.FyersModel(client_id=_env("FYERS_CLIENT_ID"), token=tok, is_async=False, log_path=LOG_DIR)
        _client = (m, made)
        return m


def _call(fn, *a, **k):
    """call with the limiter; on an auth error log in again once"""
    for attempt in (0, 1):
        LIMIT.wait()
        out = getattr(client(force=attempt == 1), fn)(*a, **k)
        if isinstance(out, dict) and out.get("s") == "error" and out.get("code") in (-8, -15, -16, -17, -300, 401):
            continue
        return out
    raise FyersError(f"FYERS {fn} failed after re-login: {out}")


def token_status():
    v, _ = db.get_state(TOKEN_KEY)
    if not v:
        return dict(valid=False, at=None)
    d = json.loads(v)
    return dict(valid=datetime.fromisoformat(d["at"]) >= _expiry_cutoff(), at=d["at"])


# ------------------------------------------------------------------ data
def history(fsym: str, start, end) -> pd.DataFrame:
    """daily closes [date, close] between start and end (inclusive), in chunks of up to 365 days"""
    out = []
    a, b = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    while a <= b:
        e = min(b, a + pd.Timedelta(days=364))
        r = _call("history", data={"symbol": fsym, "resolution": "1D", "date_format": "1", "range_from": str(a.date()),
                                   "range_to": str(e.date()), "cont_flag": "1"})
        if not isinstance(r, dict) or r.get("s") not in ("ok", "no_data"):
            raise FyersError(f"history {fsym}: {r.get('message') if isinstance(r, dict) else r}")
        for c in r.get("candles") or []:
            d = pd.Timestamp(c[0], unit="s", tz="UTC").tz_convert(IST).tz_localize(None).normalize()
            out.append((d, float(c[4])))
        a = e + pd.Timedelta(days=1)
    df = pd.DataFrame(out, columns=["date", "close"]).drop_duplicates("date", keep="last")
    return df.sort_values("date")


def quotes(fsyms: list) -> dict:
    """{fyers symbol: {lp, prev_close, chp, time}} in batches of 50"""
    out = {}
    for i in range(0, len(fsyms), 50):
        part = fsyms[i:i + 50]
        r = _call("quotes", data={"symbols": ",".join(part)})
        if not isinstance(r, dict) or r.get("s") != "ok":
            raise FyersError(f"quotes: {r.get('message') if isinstance(r, dict) else r}")
        for x in r.get("d", []):
            v = x.get("v") or {}
            if x.get("s") == "ok" and v.get("lp") is not None:
                out[x.get("n")] = dict(lp=float(v["lp"]), prev_close=float(v.get("prev_close_price") or "nan"),
                                       chp=float(v.get("chp") or 0.0), time=v.get("tt"))
    return out
