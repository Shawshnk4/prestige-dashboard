"use strict";
/* PRESTIGE dashboard: book, lots, live P&L, rankings, status. Talks only to this server's /api. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const FACT = ["MOM", "IR1", "SECTOR", "IR1F"];
let SB = null, TOKEN = null, CFG = null;

function lsGet(k, d) { try { const v = localStorage.getItem("prestige." + k); return v === null ? d : v; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem("prestige." + k, v); } catch (e) { /* private mode */ } }

const S = {
  model: null, status: null, ports: null,
  sched: lsGet("sched", "monthly"), which: lsGet("which", "held"),
  capital: Number(lsGet("capital", "50000000")), hedge: Number(lsGet("hedge", "100")),
  px: {}, pxAsof: {}, rankSort: { k: "rank", d: 1 }, liveTimer: null, livePx: null, liveAsof: null,
};

/* ---------------------------------------------------------------- formatting */
const inr = x => (x == null || !isFinite(x)) ? "–" : (x < 0 ? "−₹" : "₹") + Math.abs(Math.round(x)).toLocaleString("en-IN");
const num = (x, d = 2) => (x == null || !isFinite(x)) ? "–" : Number(x).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 2) => (x == null || !isFinite(x)) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(d) + "%";
const sgn = x => x > 0 ? "pos" : x < 0 ? "neg" : "";
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function day(s, year = false) {
  if (!s) return "–";
  const d = new Date(String(s).slice(0, 10) + "T00:00:00");
  return `${DOW[d.getDay()]} ${d.getDate()}-${MON[d.getMonth()]}${year ? "-" + d.getFullYear() : ""}`;
}
const futName = c => { const d = new Date(c + "T00:00:00"); return `${MON[d.getMonth()]}-${String(d.getFullYear()).slice(2)} futures`; };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SECT = { AUTO: "Auto", CAPGOODS_DEF: "Capital Goods & Defence", CEMENT: "Cement", CONS_DURABLE: "Consumer Durables", CONS_SERVICES: "Consumer Services",
  FMCG: "FMCG", INFRA_REALTY: "Infra & Realty", INS_CAPMKT: "Insurance & Cap Mkts", IT: "IT", METALS_MIN: "Metals & Mining", NBFC: "NBFC",
  OIL_GAS: "Oil & Gas", PHARMA_HC: "Pharma & Healthcare", POWER: "Power", PSU_BANKS: "PSU Banks", PVT_BANKS: "Private Banks", TELECOM: "Telecom" };
const sect = s => SECT[s] || s || "–";

/* ---------------------------------------------------------------- API + auth */
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers.Authorization = "Bearer " + TOKEN;
  const r = await fetch(path, { ...opts, headers });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && SB) { showLogin(j.detail || "Sign in again"); throw new Error(j.detail || "Sign in"); }
  if (!r.ok) throw new Error(j.detail || `${r.status} ${r.statusText}`);
  return j;
}

function showLogin(msg) {
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-msg").textContent = msg || "";
}

async function boot() {
  try { CFG = await (await fetch("/api/config")).json(); } catch (e) { showLogin("The server is not reachable."); return; }
  if (CFG.auth_disabled) return start();
  if (!CFG.supabase_url || !CFG.supabase_anon_key) { showLogin("The server has no SUPABASE_URL / SUPABASE_ANON_KEY set."); return; }
  SB = supabase.createClient(CFG.supabase_url, CFG.supabase_anon_key);
  SB.auth.onAuthStateChange((_e, session) => { TOKEN = session ? session.access_token : null; });
  const { data } = await SB.auth.getSession();
  if (data.session) { TOKEN = data.session.access_token; start(); } else showLogin();
}

$("#login-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  $("#login-msg").textContent = "";
  const { data, error } = await SB.auth.signInWithPassword({ email: $("#login-email").value.trim(), password: $("#login-pass").value });
  if (error) { $("#login-msg").textContent = error.message; return; }
  TOKEN = data.session.access_token; start();
});
$("#signout").addEventListener("click", async () => { if (SB) await SB.auth.signOut(); TOKEN = null; showLogin("Signed out."); });

async function start() {
  $("#login").hidden = true; $("#app").hidden = false;
  initControls();
  await loadAll();
  const st = S.status && S.status.run;
  if (st && st.running) pollRun();
}

async function loadAll() {
  const [st, m, p] = await Promise.allSettled([api("/api/status"), api("/api/model"), api("/api/portfolios")]);
  S.status = st.status === "fulfilled" ? st.value : null;
  S.model = m.status === "fulfilled" && !m.value.no_run ? m.value : null;
  S.ports = p.status === "fulfilled" ? p.value : null;
  if (st.status === "rejected" && /team list/.test(st.reason.message)) { showLogin(st.reason.message); return; }
  renderHeader(); renderBook(); renderLive(); renderRank(); renderStatus();
}

/* ---------------------------------------------------------------- header + run */
function renderHeader() {
  const el = $("#datastate"), m = S.model, st = S.status;
  if (!m) { el.innerHTML = "No model run yet. Press <b>Run model</b> to fetch FYERS data and build the books."; return; }
  const tok = st && st.fyers_token && st.fyers_token.valid ? "FYERS connected" : "<span class='bad'>FYERS not logged in today</span>";
  const behind = st && st.expected_session && m.data_end < st.expected_session ? ` <span class="bad">(close of ${day(st.expected_session)} not loaded yet)</span>` : "";
  el.innerHTML = `Data to <b>${day(m.data_end, true)}</b>${behind}&ensp;&ensp;Run ${esc(String(m.run_at || "").slice(0, 16).replace("T", " "))} by ${esc(m.run_by || "")}&ensp;&ensp;${tok}`;
}

$("#run-btn").addEventListener("click", async () => {
  try { await api("/api/run", { method: "POST", body: JSON.stringify({ fetch: true }) }); pollRun(); }
  catch (e) { alertBar(e.message, true); }
});

function alertBar(text, bad) {
  $("#runbar").hidden = false; $("#runbar-fill").style.width = "0";
  $("#runbar-text").innerHTML = bad ? `<span class="bad">${esc(text)}</span>` : esc(text);
}

async function pollRun() {
  $("#run-btn").disabled = true; $("#runbar").hidden = false;
  for (;;) {
    let s;
    try { s = await api("/api/run"); } catch (e) { break; }
    const w = s.steps ? Math.round(100 * s.step / s.steps) : 5;
    $("#runbar-fill").style.width = (s.running ? w : 100) + "%";
    $("#runbar-text").textContent = s.running ? s.progress : (s.error ? "" : "Model updated.");
    if (!s.running) {
      if (s.error) alertBar("Run failed: " + s.error, true);
      break;
    }
    await new Promise(r => setTimeout(r, 2000));
  }
  $("#run-btn").disabled = false;
  S.px = {}; await loadAll();
  setTimeout(() => { if (!$("#runbar-text .bad")) $("#runbar").hidden = true; }, 4000);
}

/* ---------------------------------------------------------------- tabs + controls */
$$(".tabs button").forEach(b => b.addEventListener("click", () => {
  $$(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
  $$(".tab").forEach(t => t.hidden = t.id !== "tab-" + b.dataset.tab);
  lsSet("tab", b.dataset.tab);
  if (b.dataset.tab === "live") refreshLive();
  if (b.dataset.tab === "rank") ensureRankPrices();
}));

function setSeg(id, v) { $$(`#${id} button`).forEach(b => b.setAttribute("aria-pressed", b.dataset.v === v)); }
let controlsReady = false;
function initControls() {
  if (controlsReady) return; controlsReady = true;
  setSeg("sched", S.sched); setSeg("which", S.which);
  $("#capital").value = S.capital.toLocaleString("en-IN"); $("#hedge").value = S.hedge;
  $$("#sched button").forEach(b => b.addEventListener("click", () => { S.sched = b.dataset.v; lsSet("sched", S.sched); setSeg("sched", S.sched); renderBook(); }));
  $$("#which button").forEach(b => b.addEventListener("click", () => { S.which = b.dataset.v; lsSet("which", S.which); setSeg("which", S.which); renderBook(); }));
  $("#capital").addEventListener("change", e => {
    const v = Number(String(e.target.value).replace(/[^0-9.]/g, ""));
    if (v > 0) { S.capital = v; lsSet("capital", v); }
    e.target.value = S.capital.toLocaleString("en-IN"); renderBook();
  });
  $("#hedge").addEventListener("change", e => {
    const v = Number(e.target.value);
    if (v >= 0 && v <= 200) { S.hedge = v; lsSet("hedge", v); }
    e.target.value = S.hedge; renderBook();
  });
  $("#px-refresh").addEventListener("click", () => { const L = leg(); if (L && L.book) fetchPrices(L.book.map(x => x.symbol), L.contract, true).then(renderBook); });
  $("#save-btn").addEventListener("click", openSave);
  $("#live-refresh").addEventListener("click", () => refreshLive(true));
  $("#live-every").value = lsGet("every", "15");
  $("#live-every").addEventListener("change", e => { lsSet("every", e.target.value); scheduleLive(); });
  $("#live-close").addEventListener("click", closePortfolio);
  $("#rank-refresh").addEventListener("click", () => ensureRankPrices(true));
  const t = lsGet("tab", "book"); const tb = $(`.tabs button[data-tab="${t}"]`); if (tb) tb.click();
  scheduleLive();
}

/* ---------------------------------------------------------------- prices */
async function fetchPrices(symbols, contract, force = false) {
  const key = contract || "cash";
  S.px[key] = S.px[key] || {};
  const need = force ? symbols : symbols.filter(s => !S.px[key][s]);
  if (!need.length) return S.px[key];
  try {
    const r = await api("/api/prices", { method: "POST", body: JSON.stringify({ symbols: need, contract }) });
    Object.assign(S.px[key], r.prices); S.pxAsof[key] = r.asof;
    if (r.prices.NIFTY50) S.nifty = r.prices.NIFTY50.cash;
  } catch (e) { S.pxErr = e.message; }
  return S.px[key];
}

/* ---------------------------------------------------------------- book */
function leg() {
  const m = S.model; if (!m) return null;
  const sch = m.schedules[S.sched];
  if (S.which === "held") return { ...sch.held, title: "Book in force" };
  if (S.which === "next") return { ...sch.next, title: "Next rebalance" };
  return { rebalance: m.latest.trade_day, signal: m.latest.signal, kind: "latest", offset: 0, cycle_start: sch.held.cycle_start,
    contract: m.latest.contract, weights: m.latest.weights, book: m.latest.rankings.filter(x => x.side), title: "On latest close", weights_set_on: m.latest.weights_set_on };
}
function kindText(L) {
  if (L.kind === "monthly") return "Monthly deployment (E-1)";
  if (L.kind === "in-cycle") return `In-cycle, E-1 + ${L.offset} sessions`;
  return "Not a rebalance: the model on the latest close";
}

function size(L, px) {
  const lots = (S.model.lots[L.contract] || {}).lots || {};
  const cap = { LONG: S.capital, SHORT: S.capital * S.hedge / 100 };
  return L.book.map(r => {
    const p = px[r.symbol] || {}, fut = p.fut ? p.fut.lp : null, lot = lots[r.symbol] || null;
    const target = r.weight * cap[r.side];
    const n = fut && lot ? Math.round(target / (fut * lot)) : null;
    return { ...r, fut, cash: p.cash ? p.cash.lp : null, chp: p.cash ? p.cash.chp : null, lot, target, lots: n, actual: n != null ? n * fut * lot : null };
  });
}

async function renderBook() {
  const L = leg();
  const head = $("#book-head"), body = $("#book-body"), empty = $("#book-empty");
  if (!L) { head.innerHTML = ""; body.hidden = true; empty.hidden = false; empty.textContent = "No model run yet. Press Run model."; return; }
  const hd = [
    ["View", `${L.title}<br><small>${esc(kindText(L))}</small>`],
    ["Signal close", day(L.signal)],
    [L.kind === "latest" ? "If traded at the close of" : "Trade at close", day(L.rebalance)],
    ["Contract", `${futName(L.contract)}<br><small>expiry ${day(L.contract)}</small>`],
  ];
  head.innerHTML = hd.map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  if (!L.book) {
    body.hidden = true; empty.hidden = false;
    empty.innerHTML = `The ${esc(kindText(L).toLowerCase())} on <b>${day(L.rebalance)}</b> uses the close of <b>${day(L.signal)}</b>. ` +
      `Run the model after that close (16:00 IST) and the book appears here.`;
    return;
  }
  empty.hidden = true; body.hidden = false;
  const held = S.model.schedules[S.sched].held;
  const heldSet = new Set((held.book || []).map(x => x.side + x.symbol));
  const isNew = r => S.which !== "held" && !heldSet.has(r.side + r.symbol);
  renderLadder(L, isNew);
  renderWeights(L.weights);
  const px = await fetchPrices(L.book.map(x => x.symbol), L.contract);
  const rows = size(L, px);
  S.sized = { L, rows };
  const asof = S.pxAsof[L.contract];
  $("#px-asof").textContent = asof ? "Prices " + asof.slice(11, 16) + " IST" : (S.pxErr ? "Prices unavailable: " + S.pxErr : "");
  const longs = rows.filter(r => r.side === "LONG").sort((a, b) => a.rank - b.rank);
  const shorts = rows.filter(r => r.side === "SHORT").sort((a, b) => b.rank - a.rank);
  const H = `<tr><th class="t">Stock</th><th class="t">Sector</th><th>Rank</th><th>Weight</th><th>Target</th><th>Fut price</th><th>Lot</th><th>Lots</th>
    <th>Actual</th><th>vs target</th><th>Final</th>${FACT.map(f => `<th>${f} z</th>`).join("")}<th>Vol 63d</th><th>Cash</th><th>Today</th></tr>`;
  const row = r => `<tr class="${r.side === "LONG" ? "L" : "S"}"><td class="t">${esc(r.symbol)} ${isNew(r) ? '<span class="tag w">new</span>' : ""}</td>
    <td class="t">${esc(sect(r.sector))}</td><td>${r.rank}</td><td>${pct(r.weight, 1).replace("+", "")}</td><td>${inr(r.target)}</td><td>${num(r.fut)}</td>
    <td>${r.lot ?? '<span class="bad">?</span>'}</td><td><b class="${r.side === "LONG" ? "pos" : "neg"}">${r.lots == null ? "–" : (r.side === "LONG" ? "+" : "−") + r.lots}</b></td>
    <td>${inr(r.actual)}</td><td class="${sgn(r.actual - r.target)}">${r.actual == null ? "–" : pct(r.actual / r.target - 1, 0)}</td><td>${num(r.final, 3)}</td>
    ${FACT.map(f => `<td>${num(r[f])}</td>`).join("")}<td>${pct(r.vol63, 0).replace("+", "")}</td><td>${num(r.cash)}</td><td class="${sgn(r.chp)}">${r.chp == null ? "–" : pct(r.chp / 100)}</td></tr>`;
  const tot = (lbl, rs) => {
    const t = rs.reduce((a, r) => a + r.target, 0), a = rs.reduce((s, r) => s + (r.actual || 0), 0);
    return `<tr class="tot"><td class="t">${lbl}</td><td></td><td></td><td>100%</td><td>${inr(t)}</td><td></td><td></td><td>${rs.reduce((s, r) => s + (r.lots || 0), 0)}</td>
      <td>${inr(a)}</td><td class="${sgn(a - t)}">${t ? pct(a / t - 1, 1) : "–"}</td><td colspan="${FACT.length + 4}"></td></tr>`;
  };
  $("#book-table").innerHTML = `<thead>${H}</thead><tbody>${longs.map(row).join("")}${tot("Long leg", longs)}${shorts.map(row).join("")}${tot("Short leg", shorts)}</tbody>`;
  const la = longs.reduce((s, r) => s + (r.actual || 0), 0), sa = shorts.reduce((s, r) => s + (r.actual || 0), 0);
  const zero = rows.filter(r => r.lots === 0).map(r => r.symbol);
  $("#book-sum").innerHTML = `<span>Gross <b>${inr(la + sa)}</b></span><span>Net <b>${inr(la - sa)}</b></span>` +
    (zero.length ? `<span class="warn">0 lots: ${esc(zero.join(", "))} (target below half a lot)</span>` : "") +
    `<span>One lot is roughly ₹4–13 lakh, so legs below about ₹5 Cr round away from their weights.</span>`;
}

function renderLadder(L, isNew) {
  const longs = L.book.filter(r => r.side === "LONG").sort((a, b) => a.rank - b.rank);
  const shorts = L.book.filter(r => r.side === "SHORT").sort((a, b) => b.rank - a.rank);
  const mx = Math.max(...L.book.map(r => r.weight));
  let h = '<div class="spine"></div>';
  for (let i = 0; i < Math.max(longs.length, shorts.length); i++) {
    const s = shorts[i], l = longs[i];
    h += s ? `<div class="rung s${isNew(s) ? " new" : ""}" style="grid-row:${i + 1}" title="${esc(s.symbol)}: rank ${s.rank}, final ${num(s.final, 3)}">
      <span class="nm">${esc(s.symbol)} <span class="wt">${(s.weight * 100).toFixed(1)}%</span></span><div class="track"><div class="bar" style="width:${(s.weight / mx * 100).toFixed(1)}%"></div></div></div>` : "";
    h += l ? `<div class="rung l${isNew(l) ? " new" : ""}" style="grid-row:${i + 1}" title="${esc(l.symbol)}: rank ${l.rank}, final ${num(l.final, 3)}">
      <div class="track"><div class="bar" style="width:${(l.weight / mx * 100).toFixed(1)}%"></div></div><span class="nm">${esc(l.symbol)} <span class="wt">${(l.weight * 100).toFixed(1)}%</span></span></div>` : "";
  }
  $("#ladder").innerHTML = h;
}

function renderWeights(w) {
  $("#fw").innerHTML = FACT.map(k => `<div class="${k}" style="width:${(w[k] * 100).toFixed(2)}%" title="${k} ${(w[k] * 100).toFixed(1)}%">${k} ${(w[k] * 100).toFixed(1)}%</div>`).join("");
}

/* ---------------------------------------------------------------- save portfolio */
function openSave() {
  if (!S.sized) return;
  const { L, rows } = S.sized;
  if (rows.some(r => r.lots == null)) { alertBar("Prices or lot sizes are missing for some stocks: refresh prices first.", true); return; }
  $("#save-sub").textContent = `${L.title}: signal ${day(L.signal)}, trade ${day(L.rebalance)}, ${futName(L.contract)}. Long capital ${inr(S.capital)}, short leg ${S.hedge}% of long.`;
  $("#save-name").value = `${S.sched[0].toUpperCase() + S.sched.slice(1)} book, ${day(L.kind === "latest" ? new Date().toISOString().slice(0, 10) : L.rebalance)}`;
  $("#save-table").innerHTML = `<thead><tr><th class="t">Stock</th><th class="t">Side</th><th>Lots</th><th>Lot</th><th>Entry fut price</th><th>Cash now</th></tr></thead><tbody>` +
    rows.map((r, i) => `<tr class="${r.side === "LONG" ? "L" : "S"}"><td class="t">${esc(r.symbol)}</td><td class="t"><span class="tag ${r.side[0]}">${r.side.toLowerCase()}</span></td>
      <td><input data-i="${i}" data-k="lots" type="number" min="0" step="1" value="${r.lots}"></td><td>${r.lot}</td>
      <td><input data-i="${i}" data-k="entry_fut" type="number" step="0.05" value="${r.fut}"></td><td>${num(r.cash)}</td></tr>`).join("") + "</tbody>";
  $("#save-msg").textContent = "";
  $("#save-dlg").showModal();
}

$("#save-confirm").addEventListener("click", async ev => {
  ev.preventDefault();
  const { L, rows } = S.sized;
  const inputs = $$("#save-table input");
  const pos = rows.map((r, i) => {
    const get = k => Number(inputs.find(x => +x.dataset.i === i && x.dataset.k === k).value);
    return { symbol: r.symbol, side: r.side, rank: r.rank, final: r.final, weight: r.weight, lots: get("lots"), lot_size: r.lot,
      entry_fut: get("entry_fut"), entry_cash: r.cash };
  });
  if (pos.some(p => !(p.lots >= 0) || !(p.entry_fut > 0))) { $("#save-msg").textContent = "Every row needs lots (0 or more) and an entry price."; return; }
  const today = new Date().toISOString().slice(0, 10);
  const body = { name: $("#save-name").value, schedule: S.sched, capital: S.capital, hedge: S.hedge / 100,
    trade_date: L.kind === "latest" ? today : L.rebalance, signal_date: L.signal, contract: L.contract, positions: pos,
    note: JSON.stringify({ view: S.which, weights: L.weights, nifty_entry: S.nifty ? S.nifty.lp : null }) };
  try {
    await api("/api/portfolios", { method: "POST", body: JSON.stringify(body) });
    $("#save-dlg").close();
    S.ports = await api("/api/portfolios"); S.livePx = null;
    $('.tabs button[data-tab="live"]').click();
  } catch (e) { $("#save-msg").textContent = e.message; }
});

/* ---------------------------------------------------------------- live P&L */
function scheduleLive() {
  clearInterval(S.liveTimer);
  S.liveTimer = setInterval(() => refreshLive(true), Number(lsGet("every", "15")) * 60000);
}

async function refreshLive(force = false) {
  const p = S.ports && S.ports.current;
  if (!p) return renderLive();
  if (force || !S.livePx) {
    try {
      const r = await api("/api/prices", { method: "POST", body: JSON.stringify({ symbols: p.positions.map(x => x.symbol), contract: p.contract }) });
      S.livePx = r.prices; S.liveAsof = r.asof;
    } catch (e) { S.liveErr = e.message; }
  }
  renderLive();
}

function pnlOf(p, px) {
  const out = p.positions.map(x => {
    const q = (px || {})[x.symbol] || {}, fut = q.fut ? q.fut.lp : null, cash = q.cash ? q.cash.lp : null, sign = x.side === "LONG" ? 1 : -1;
    const pnl = fut != null ? sign * x.lots * x.lot_size * (fut - x.entry_fut) : null;
    return { ...x, fut, cash, sign, pnl, notional: x.lots * x.lot_size * x.entry_fut, cashMove: cash && x.entry_cash ? cash / x.entry_cash - 1 : null };
  });
  const sum = (rs, f) => rs.reduce((a, r) => a + (f(r) || 0), 0);
  const L = out.filter(r => r.side === "LONG"), Sh = out.filter(r => r.side === "SHORT");
  return { rows: out, L: { pnl: sum(L, r => r.pnl), n: sum(L, r => r.notional) }, S: { pnl: sum(Sh, r => r.pnl), n: sum(Sh, r => r.notional) } };
}

function renderLive() {
  const p = S.ports && S.ports.current;
  $("#live-empty").hidden = !!p; $("#live-body").hidden = !p;
  renderHistory();
  if (!p) return;
  let note = {}; try { note = JSON.parse(p.note || "{}"); } catch (e) { /* free text */ }
  const P = pnlOf(p, S.livePx), net = P.L.pnl + P.S.pnl;
  const held = Math.round((Date.now() - new Date(p.trade_date + "T15:30:00+05:30")) / 864e5);
  $("#live-title").innerHTML = `<b>${esc(p.name || "Current portfolio")}</b><br><span class="muted">Traded ${day(p.trade_date)} on ${futName(p.contract)}, ` +
    `saved by ${esc(p.created_by)}. ${S.liveAsof ? "Prices " + S.liveAsof.slice(11, 16) + " IST." : (S.liveErr ? "Prices unavailable: " + esc(S.liveErr) : "")}</span>`;
  const n50 = S.livePx && S.livePx.NIFTY50 && note.nifty_entry ? S.livePx.NIFTY50.cash.lp / note.nifty_entry - 1 : null;
  const tile = (k, v, s, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`;
  $("#live-tiles").innerHTML =
    tile("Net P&L", inr(net), `${pct(net / p.capital)} of long capital ${inr(p.capital)}`, sgn(net)) +
    tile("Long leg", inr(P.L.pnl), `${pct(P.L.n ? P.L.pnl / P.L.n : null)} on ${inr(P.L.n)}`, sgn(P.L.pnl)) +
    tile("Short leg", inr(P.S.pnl), `${pct(P.S.n ? P.S.pnl / P.S.n : null)} on ${inr(P.S.n)}`, sgn(P.S.pnl)) +
    tile("Nifty 50 since saved", pct(n50), `${held >= 0 ? held : 0} days held`, sgn(n50));
  const rows = P.rows.slice().sort((a, b) => (a.side === b.side ? (b.pnl || 0) - (a.pnl || 0) : a.side === "LONG" ? -1 : 1));
  $("#live-table").innerHTML = `<thead><tr><th class="t">Stock</th><th class="t">Side</th><th>Lots</th><th>Lot</th><th>Entry fut</th><th>Fut now</th><th>Move</th><th>P&amp;L</th><th>Entry cash</th><th>Cash now</th><th>Cash move</th></tr></thead><tbody>` +
    rows.map(r => `<tr class="${r.side[0]}"><td class="t">${esc(r.symbol)}</td><td class="t"><span class="tag ${r.side[0]}">${r.side.toLowerCase()}</span></td><td>${r.lots}</td><td>${r.lot_size}</td>
      <td>${num(r.entry_fut)}</td><td>${num(r.fut)}</td><td class="${sgn(r.fut - r.entry_fut)}">${r.fut ? pct(r.fut / r.entry_fut - 1) : "–"}</td>
      <td class="${sgn(r.pnl)}"><b>${inr(r.pnl)}</b></td><td>${num(r.entry_cash)}</td><td>${num(r.cash)}</td><td class="${sgn(r.cashMove)}">${pct(r.cashMove)}</td></tr>`).join("") + "</tbody>";
}

function renderHistory() {
  const h = (S.ports && S.ports.history) || [];
  if (!h.length) { $("#hist-table").innerHTML = '<tbody><tr><td class="t muted">None yet.</td></tr></tbody>'; return; }
  $("#hist-table").innerHTML = `<thead><tr><th class="t">Name</th><th class="t">Schedule</th><th>Traded</th><th>Contract</th><th class="t">Saved by</th><th class="t">Status</th><th>Closed</th><th>P&amp;L at close</th></tr></thead><tbody>` +
    h.map(p => {
      let real = null;
      if (p.exit_prices) real = p.positions.reduce((a, x) => { const e = p.exit_prices[x.symbol]; return e && e.fut ? a + (x.side === "LONG" ? 1 : -1) * x.lots * x.lot_size * (e.fut - x.entry_fut) : a; }, 0);
      return `<tr><td class="t">${esc(p.name)}</td><td class="t">${esc(p.schedule)}</td><td>${day(p.trade_date, true)}</td><td>${esc(p.contract)}</td><td class="t">${esc(p.created_by)}</td>
        <td class="t">${p.status === "current" ? '<span class="tag L">current</span>' : "closed"}</td><td>${p.closed_at ? esc(String(p.closed_at).slice(0, 16)) : "–"}</td><td class="${sgn(real)}">${inr(real)}</td></tr>`;
    }).join("") + "</tbody>";
}

async function closePortfolio() {
  const p = S.ports && S.ports.current; if (!p) return;
  if (!confirm(`Close "${p.name}"? Its exit prices are recorded at the current futures prices and it moves to Earlier portfolios.`)) return;
  try { await api(`/api/portfolios/${p.id}/close`, { method: "POST" }); S.ports = await api("/api/portfolios"); renderLive(); }
  catch (e) { alertBar(e.message, true); }
}

/* ---------------------------------------------------------------- rankings */
async function ensureRankPrices(force = false) {
  if (!S.model) return;
  await fetchPrices(S.model.latest.rankings.map(r => r.symbol), null, force);
  renderRank();
}

function zcell(v) {
  if (v == null) return "<td>–</td>";
  const a = Math.min(Math.abs(v) / 3, 1) * 0.42, c = v > 0 ? `rgba(14,122,106,${a})` : `rgba(179,38,63,${a})`;
  return `<td style="background:${c}">${num(v)}</td>`;
}

function renderRank() {
  const m = S.model; if (!m) { $("#rank-table").innerHTML = ""; return; }
  const px = S.px.cash || {};
  const held = m.schedules[S.sched].held.book || [];
  const hs = Object.fromEntries(held.map(x => [x.symbol, x.side]));
  $("#rank-title").textContent = `Rankings on the close of ${day(m.latest.signal, true)}`;
  $("#rank-asof").textContent = S.pxAsof.cash ? "Prices " + S.pxAsof.cash.slice(11, 16) + " IST" : "";
  const rows = m.latest.rankings.map(r => ({ ...r, cash: px[r.symbol] && px[r.symbol].cash ? px[r.symbol].cash.lp : null,
    chp: px[r.symbol] && px[r.symbol].cash ? px[r.symbol].cash.chp / 100 : null, inforce: hs[r.symbol] || "" }));
  const k = S.rankSort.k, d = S.rankSort.d;
  rows.sort((a, b) => { const x = a[k], y = b[k]; if (typeof x === "string" || typeof y === "string") return d * String(x).localeCompare(String(y)); return d * ((x ?? -1e9) - (y ?? -1e9)); });
  const cols = [["rank", "Rank"], ["symbol", "Stock", "t"], ["side", "Side now", "t"], ["inforce", "In book in force", "t"], ["sector", "Sector", "t"], ["style", "Style group", "t"],
    ["final", "Final"], ["composite", "Composite"], ...FACT.map(f => [f, f + " z"]), ["vol63", "Vol 63d"], ["cash", "Cash"], ["chp", "Today"]];
  $("#rank-table").innerHTML = `<thead><tr>${cols.map(([c, l, t]) => `<th class="sortable ${t || ""}" data-k="${c}">${l}${c === k ? (d > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(r => `<tr class="${r.side === "LONG" ? "L" : r.side === "SHORT" ? "S" : ""}"><td>${r.rank}</td><td class="t"><b>${esc(r.symbol)}</b></td>
      <td class="t">${r.side ? `<span class="tag ${r.side[0]}">${r.side.toLowerCase()}</span>` : ""}</td><td class="t">${r.inforce ? `<span class="tag ${r.inforce[0]}">${r.inforce.toLowerCase()}</span>` : ""}</td>
      <td class="t">${esc(sect(r.sector))}</td><td class="t">${esc(r.style)}</td><td><b>${num(r.final, 3)}</b></td><td>${num(r.composite, 3)}</td>
      ${FACT.map(f => zcell(r[f])).join("")}<td>${pct(r.vol63, 0).replace("+", "")}</td><td>${num(r.cash)}</td><td class="${sgn(r.chp)}">${pct(r.chp)}</td></tr>`).join("") + "</tbody>";
  $$("#rank-table th.sortable").forEach(th => th.addEventListener("click", () => {
    const kk = th.dataset.k; S.rankSort = { k: kk, d: S.rankSort.k === kk ? -S.rankSort.d : (["symbol", "sector", "style", "side", "inforce"].includes(kk) ? 1 : (kk === "rank" ? 1 : -1)) }; renderRank();
  }));
}

/* ---------------------------------------------------------------- status */
function renderStatus() {
  const st = S.status, m = S.model;
  if (!st) { $("#status-body").innerHTML = '<div class="box">Status unavailable.</div>'; return; }
  const yes = (b, t, f) => b ? `<span class="ok">${t}</span>` : `<span class="bad">${f}</span>`;
  const run = st.run || {};
  const warns = (m && m.warnings) || [], jumps = (m && m.jumps) || [];
  $("#status-body").innerHTML = `
  <div class="box"><h2>Data</h2><dl>
    <dt>Model data to</dt><dd>${m ? day(m.data_end, true) : "no run yet"}</dd>
    <dt>Last completed session</dt><dd>${day(st.expected_session, true)} ${m && m.data_end >= st.expected_session ? '<span class="ok">loaded</span>' : '<span class="warn">run the model to load it</span>'}</dd>
    <dt>FYERS closes stored</dt><dd>${st.stored.symbols} symbols to ${day(st.stored.last_date, true)}${st.stored.stale.length ? ` <span class="warn">(behind: ${esc(st.stored.stale.join(", "))})</span>` : ""}</dd>
    <dt>History base</dt><dd>vendor-clean closes to ${day(st.anchor, true)}, FYERS after that</dd>
    <dt>Universe</dt><dd>Nifty 50 (${st.members.nifty50} members), sectors from Nifty 100 (${st.members.nifty100})</dd>
    <dt>Holiday list runs to</dt><dd>${day(st.holidays_until, true)}</dd></dl></div>
  <div class="box"><h2>Connections</h2><dl>
    <dt>FYERS settings</dt><dd>${(st.fyers_problems || []).length ? `<ul>${st.fyers_problems.map((x) => `<li class="bad">${esc(x)}</li>`).join("")}</ul>` : yes(st.fyers_configured, "filled in", "missing: fill them in .env (or Render, Environment)")}</dd>
    <dt>FYERS login today</dt><dd>${yes(st.fyers_token.valid, "logged in " + esc(String(st.fyers_token.at || "").slice(0, 16).replace("T", " ")), "not yet (logs in automatically on the next run or price refresh)")}</dd>
    <dt>Signed in as</dt><dd>${esc(st.user)}</dd>
    <dt>Last run</dt><dd>${run.error ? `<span class="bad">${esc(run.error)}</span>` : (m ? esc(String(m.run_at).slice(0, 16).replace("T", " ")) + " by " + esc(m.run_by) : "–")}</dd></dl></div>
  <div class="box"><h2>Checks from the last run</h2>
    ${warns.length ? `<ul>${warns.map(w => `<li class="warn">${esc(w)}</li>`).join("")}</ul>` : '<p class="ok">No warnings.</p>'}
    ${jumps.length ? `<p>Daily moves above 15% to confirm (a split or bonus that FYERS has not adjusted would show here):</p><ul>${jumps.map(j => `<li>${esc(j.symbol)} ${day(j.date, true)}: ${pct(j.move)}</li>`).join("")}</ul>` : ""}</div>
  <div class="box"><h2>Upkeep</h2><ul>
    <li>Lot sizes: data/ref/lot_sizes.csv, one row per stock and contract month. Add the new month when NSE revises lots.</li>
    <li>Holidays: data/ref/holidays.csv. Add each year's NSE trading-holiday list when NSE publishes it (usually in December).</li>
    <li>Index changes: data/ref/membership_changes.csv (effective date, index, symbol, add or remove), with the stock's sector in data/ref/sectors.csv.</li>
    <li>A corporate action FYERS has not adjusted: data/ref/price_adjustments.csv (symbol, ex-date, factor).</li>
    <li>Edit these files in GitHub; Render redeploys the site on its own.</li></ul></div>`;
}

boot();
