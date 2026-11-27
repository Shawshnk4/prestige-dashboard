# PRESTIGE dashboard

An internal website for the PRESTIGE Nifty 50 long/short model. It is a separate model and site from F3. You can:

- open the site and press **Run model**. The site brings FYERS daily closes up to date, recomputes the model and shows the books.
- switch between the **monthly**, **biweekly** and **weekly** rebalance schedules. Each schedule shows the book in force, the next rebalance, and the book the model picks on the latest close.
- enter long capital and the short leg as a % of long. Lots are sized on that month's stock futures using NSE lot sizes and live FYERS prices.
- **save a book as the current portfolio** and follow its live P&L, with prices refreshed every 5, 15 or 30 minutes.
- see every Nifty 50 stock's rank and factor scores.

- follow every rebalance since the live start (26-Oct-2026) against the Nifty 50 TRI in the **Track record** tab, for monthly, biweekly or weekly rebalancing, together with every portfolio you saved.
- export any table to CSV (opens in Excel).

The site shows no charges and no backtest. The Nifty 50 TRI is the official index to 23-Oct-2026, then extended with Nifty 50 price returns from FYERS, which has no TRI; dividends after that date (about 0.1% a month) are not included. Set `LIVE_START` (YYYY-MM-DD) to change where the track record starts.

The model code reproduces the research exactly. For the same data, it gives the same factor values, factor weights, all 189 historical monthly books, and the November-2026 book. `tests/smoke_test.py` checks the November book on any machine.

## Two ways to run it

| | A. Your own computer | B. Team website |
|---|---|---|
| Who can open it | you, on that computer (http://127.0.0.1:8000) | your team, from anywhere, with a sign-in |
| You need | Python 3.13 + a FYERS API app | a FYERS API app + Supabase + Render (+ GitHub) |
| Runs when | the computer is on and the program is started | always |
| Setup time | about 20 minutes | about an hour |

**A. Your own computer**
1. Install Python 3.13 from python.org. On Windows, tick "Add python.exe to PATH".
2. Unzip the folder. Copy `.env.example` to `.env`, open it in Notepad, and fill in the six `FYERS_...` lines (step 1 below). Leave `AUTH_DISABLED=1` and the `sqlite` `DATABASE_URL` as they are.
3. Double-click `run_local.bat` (Windows) or run `./run_local.sh` (Mac).
   - The first start installs the libraries (a few minutes), checks that the November book reproduces, and opens the dashboard in your browser.
4. Press **Run model**. The first run fetches FYERS closes from 23-Oct-2026 (2–3 minutes). After that, the books, lots and live prices are on screen.

**B. Team website:** steps 1 to 5 below.

## How it fits together

```
Browser (team, email sign-in via Supabase Auth)
   |
Render web service (this repo: FastAPI + the model, Python 3.13)
   |-- FYERS API (your account): daily closes, live quotes; logs in by itself each morning (TOTP + PIN)
   |-- Supabase Postgres: FYERS closes, model runs, saved portfolios, the day's FYERS token
```

Price history to 23-Oct-2026 is the clean history used in the research, stored in `data/base`. Everything after that date comes from FYERS. Each FYERS series is chained onto that clean history, so splits and bonuses before 23-Oct stay adjusted.

## Setup (once)

### 1. FYERS API app and TOTP
1. Sign in at https://myapi.fyers.in/dashboard and choose **Create non-trading app**.
   - PRESTIGE only reads prices, so it needs no static IP: the Primary/Secondary IP fields belong to *trading* apps.
2. App name: `PRESTIGE`. Redirect URL: `https://trade.fyers.in/api-login/redirect-uri/index.html` (FYERS's own page; put the same text in `FYERS_REDIRECT_URI`). Accept the terms and click **Create**.
3. Note the **App ID** (`ABCD1234-100`) and **Secret ID**.
4. On fyers.in/web: profile icon → **Others** → **External 2FA TOTP** → **Enable**. Scan the QR code with Google Authenticator and also keep the text key it encodes (`FYERS_TOTP_KEY`).
5. You also need your FYERS client ID (`FYERS_FY_ID`) and 4-digit PIN (`FYERS_PIN`).

Keep these values out of chat, email and code: they go only into `.env` on your computer or Render's Environment settings.

### 2. Supabase (database and team sign-in)
1. Create a new project at https://supabase.com and choose the Mumbai region.
2. **Project Settings → Database → Connection string → Session pooler**: copy the URI and put in the database password. This is `DATABASE_URL`.
3. **Project Settings → API**: copy the Project URL (`SUPABASE_URL`) and the `anon` public key (`SUPABASE_ANON_KEY`).
4. **Authentication → Sign In / Providers**: turn **off** "Allow new users to sign up".
5. **Authentication → Users → Add user**: create each team member with an email and password.

The tables are created automatically on first start. `supabase/schema.sql` holds the same SQL if you prefer to create them yourself.

### 3. GitHub
Push this folder to a **private** repository.

### 4. Render
1. Go to https://dashboard.render.com and choose **New → Blueprint**, then pick the repository. `render.yaml` sets up the service.
   - The service uses Render's **free** plan (512 MB; a model run peaks near 330 MB). A free service sleeps after 15 minutes with no visitors; the next visit wakes it in about a minute.
2. Fill in the environment variables it asks for:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase Session pooler URI (step 2) |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | step 2 |
| `ALLOWED_EMAILS` | team emails, comma-separated |
| `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI` | step 1 |
| `FYERS_FY_ID`, `FYERS_PIN`, `FYERS_TOTP_KEY` | step 1 |
| `CRON_SECRET` | generated by Render (optional scheduler) |

3. Deploy. The site's address is shown at the top of the service page.

### 5. First run
Open the site, sign in and press **Run model**. The first run fetches FYERS closes from 23-Oct-2026 for about 100 symbols, which takes about 2–3 minutes. After that, the **Book** tab shows the November book.

## Daily use
- After 16:00 IST, press **Run model** to load the day's close. If the data is already current, a run takes a few seconds.
- On a rebalance day, the signal is the previous session's close. Run the model after that close: the **Next rebalance** view then shows the book to trade at the next close. For example, for Mon 30-Nov (weekly) the signal is Fri 27-Nov.
- When you deploy a book, choose **Save as current portfolio** and enter your actual fill prices if they differ. The earlier portfolio is closed at the current prices and kept under **Earlier portfolios**.
- Optional: to run automatically each evening, have any scheduler call
  `POST https://<your-site>/api/cron/run` with header `X-Cron-Secret: <CRON_SECRET>`, for example at 16:20 IST on weekdays.

## Upkeep (edit in GitHub; Render redeploys on its own)
| File | When |
|---|---|
| `data/ref/lot_sizes.csv` | NSE revises lot sizes or a new contract month starts. Add rows `symbol,contract(YYYY-MM),lot_size`. Until then, the latest known lot is used and the Status tab says so. |
| `data/ref/holidays.csv` | Each year, add NSE's trading-holiday list. It drives expiry and rebalance dates. |
| `data/ref/membership_changes.csv` | Nifty 50 / Nifty 100 changes (March and September reviews). A new stock also needs a row in `sectors.csv`, and optionally `styles.csv`. |
| `data/ref/price_adjustments.csv` | Only if FYERS leaves a split or bonus unadjusted. The Status tab lists any daily move above 15% to check. |

Never edit `data/model/*.json`. They hold the frozen model parameters and the calibrated score tables (fitted on 2011–18).

## Model summary
- **Universe:** point-in-time Nifty 50. **Book:** 15 long and 15 short.
- **Factors:**
  - MOM: RSI(14) → EMA(9) → Bollinger(20, 1.5), daily and weekly, through calibrated score tables, weighted 60% weekly / 40% daily.
  - IR1: stock vs its sector peers, contrarian.
  - SECTOR: equal-weight Nifty 100 sector baskets vs the Nifty 50 TRI, blended with the sector's average MOM.
  - IR1F: residual vs the market and the stock's style, contrarian.
- **Factor weights:** walk-forward ICIR over 36 monthly ICs with a 2-cycle lag, 50% shrink to equal weights. They are re-estimated at each monthly signal.
  - Weekly and biweekly rebalances inside a cycle keep the monthly weights and only refresh the factor scores, as in the backtest.
- **Ranking:** score minus its style-group average. Positions are weighted by inverse 63-day volatility within each leg.
- **Timing:** signal at the close one session before the rebalance; trade at the rebalance close.
  - The monthly deployment is E-1, one trading day before the monthly expiry. Weekly adds E-1 + 5, + 10 and + 15 sessions. Biweekly adds E-1 + 10 sessions.

## Local testing
```
pip install -r requirements.txt
python tests/smoke_test.py                       # reproduces the November-2026 book offline
DATABASE_URL=sqlite:///./local.db AUTH_DISABLED=1 uvicorn app.main:app --port 8000
```
`AUTH_DISABLED=1` works only off Render.

## Security
- Secrets live only in Render's environment, never in this repository. `.gitignore` excludes `.env` files.
- Supabase tables have row-level security on with no policies, so the public key cannot read them. Only the server can.
- The site only reads market data from FYERS. It has no code that places, modifies or cancels orders.
