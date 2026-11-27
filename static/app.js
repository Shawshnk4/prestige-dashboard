"use strict";
/* PRESTIGE dashboard: book, lots, live P&L with chart, track record vs Nifty 50 TRI, rankings, status, CSV export. */
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
  series: null, charts: {}, nifty: null,
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
const short = s => { const d = new Date(String(s).slice(0, 10) + "T00:00:00"); return `${d.getDate()} ${MON[d.getMonth()]}`; };
const futName = c => { const d = new Date(c + "T00:00:00"); return `${MON[d.getMonth()]}-${String(d.getFullYear()).slice(2)} futures`; };
const todayIST = () => new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SECT = { AUTO: "Auto", CAPGOODS_DEF: "Capital Goods & Defence", CEMENT: "Cement", CONS_DURABLE: "Consumer Durables", CONS_SERVICES: "Consumer Services",
  FMCG: "FMCG", INFRA_REALTY: "Infra & Realty", INS_CAPMKT: "Insurance & Cap Mkts", IT: "IT", METALS_MIN: "Metals & Mining", NBFC: "NBFC",
  OIL_GAS: "Oil & Gas", PHARMA_HC: "Pharma & Healthcare", POWER: "Power", PSU_BANKS: "PSU Banks", PVT_BANKS: "Private Banks", TELECOM: "Telecom" };
const sect = s => SECT[s] || s || "–";
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------------------------------------------------------------- CSV export (opens in Excel; numbers unformatted) */
function downloadCSV(name, header, rows) {
  const cell = v => { if (v == null || (typeof v === "number" && !isFinite(v))) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [header, ...rows].map(r => r.map(cell).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
const r6 = x => (x == null || !isFinite(x)) ? null : Math.round(x * 1e6) / 1e6;

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
function showLogin(msg) { $("#app").hidden = true; $("#login").hidden = false; $("#login-msg").textContent = msg || ""; }

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
  ev.preventDefault(); $("#login-msg").textContent = "";
  const { data, error } = await SB.auth.signInWithPassword({ email: $("#login-email").value.trim(), password: $("#login-pass").value });
  if (error) { $("#login-msg").textContent = error.message; return; }
  TOKEN = data.session.access_token; start();
});
$("#signout").addEventListener("click", async () => { if (SB) await SB.auth.signOut(); TOKEN = null; showLogin("Signed out."); });

async function start() {
  $("#login").hidden = true; $("#app").hidden = false;
  initControls();
  await loadAll();
  refreshNifty(); setInterval(refreshNifty, 5 * 60000);
  const st = S.status && S.status.run;
  if (st && st.running) pollRun();
}

async function loadAll() {
  const [st, m, p] = await Promise.allSettled([api("/api/status"), api("/api/model"), api("/api/portfolios")]);
  S.status = st.status === "fulfilled" ? st.value : null;
  S.statusErr = st.status === "rejected" ? st.reason.message : null;
  if (S.statusErr) alertBar(S.statusErr, true);
  S.model = m.status === "fulfilled" && !m.value.no_run ? m.value : null;
  S.ports = p.status === "fulfilled" ? p.value : null;
  if (st.status === "rejected" && /team list/.test(st.reason.message)) { showLogin(st.reason.message); return; }
  S.series = null;
  renderHeader(); renderBook(); renderLive(); renderTrack(); renderRank(); renderStatus();
  if (activeTab() === "live") refreshLive(true);
}
const activeTab = () => ($(".tabs button[aria-selected='true']") || {}).dataset?.tab;

/* ---------------------------------------------------------------- header band */
const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function isDark() { const t = document.documentElement.dataset.theme; return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
function paintThemeBtn() { $("#theme-btn").innerHTML = isDark() ? SUN : MOON; }
$("#theme-btn").addEventListener("click", () => {
  const t = isDark() ? "light" : "dark"; document.documentElement.dataset.theme = t; lsSet("theme", t); paintThemeBtn();
  renderLiveChart(); renderTrack();
});

function renderHeader() {
  const el = $("#datastate"), m = S.model, st = S.status;
  paintThemeBtn();
  if (!m) { el.innerHTML = "No model run yet. Press <b>Run model</b> to fetch FYERS data and build the books."; $("#next-chip").textContent = ""; return; }
  const tok = st && st.fyers_token && st.fyers_token.valid ? "FYERS connected" : "<span class='bad'>FYERS not logged in today</span>";
  const behind = st && st.expected_session && m.data_end < st.expected_session ? ` <span class="bad">(close of ${day(st.expected_session)} not loaded yet)</span>` : "";
  el.innerHTML = `Data to <b>${day(m.data_end, true)}</b>${behind}&ensp;&ensp;Run ${esc(String(m.run_at || "").slice(0, 16).replace("T", " "))}&ensp;&ensp;${tok}`;
  const n = m.schedules[S.sched].next;
  $("#next-chip").innerHTML = `Next ${S.sched} rebalance <b>${day(n.rebalance)}</b>, signal ${day(n.signal)}`;
}

async function refreshNifty() {
  try {
    const r = await api("/api/prices", { method: "POST", body: JSON.stringify({ symbols: [], index: true }) });
    S.nifty = r.prices.NIFTY50 ? r.prices.NIFTY50.cash : null;
  } catch (e) { S.nifty = null; }
  const n = S.nifty;
  $("#nifty").innerHTML = n ? `<span class="lbl">Nifty 50</span><span class="val">${num(n.lp)}</span><span class="chg ${sgn(n.chp)}">${pct(n.chp / 100)}</span>` : "";
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
    if (!s.running) { if (s.error) alertBar("Run failed: " + s.error, true); break; }
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
  if (b.dataset.tab === "live") refreshLive(!S.livePx);
  if (b.dataset.tab === "rank") ensureRankPrices();
  if (b.dataset.tab === "track") renderTrack();
}));
function setSeg(sel, v) { $$(`${sel} button`).forEach(b => b.setAttribute("aria-pressed", b.dataset.v === v)); }
let controlsReady = false;
function initControls() {
  if (controlsReady) return; controlsReady = true;
  setSeg(".sched", S.sched); setSeg("#which", S.which);
  $("#capital").value = S.capital.toLocaleString("en-IN"); $$(".hedge").forEach(h => h.value = S.hedge);
  $$(".sched button").forEach(b => b.addEventListener("click", () => {
    S.sched = b.dataset.v; lsSet("sched", S.sched); setSeg(".sched", S.sched); renderHeader(); renderBook(); renderTrack();
  }));
  $$("#which button").forEach(b => b.addEventListener("click", () => { S.which = b.dataset.v; lsSet("which", S.which); setSeg("#which", S.which); renderBook(); }));
  $("#capital").addEventListener("change", e => {
    const v = Number(String(e.target.value).replace(/[^0-9.]/g, ""));
    if (v > 0) { S.capital = v; lsSet("capital", v); }
    e.target.value = S.capital.toLocaleString("en-IN"); renderBook();
  });
  $$(".hedge").forEach(h => h.addEventListener("change", e => {
    const v = Number(e.target.value);
    if (v >= 0 && v <= 200) { S.hedge = v; lsSet("hedge", v); }
    $$(".hedge").forEach(x => x.value = S.hedge); renderBook(); renderTrack();
  }));
  $("#px-refresh").addEventListener("click", () => { const L = leg(); if (L && L.book) fetchPrices(L.book.map(x => x.symbol), L.contract, true).then(renderBook); });
  $("#save-btn").addEventListener("click", openSave);
  $("#book-csv").addEventListener("click", exportBook);
  $("#live-refresh").addEventListener("click", () => refreshLive(true));
  $("#live-every").value = lsGet("every", "15");
  $("#live-every").addEventListener("change", e => { lsSet("every", e.target.value); scheduleLive(); });
  $("#live-close").addEventListener("click", closePortfolio);
  $("#live-csv").addEventListener("click", exportLive);
  $("#series-csv").addEventListener("click", exportSeries);
  $("#rank-refresh").addEventListener("click", () => ensureRankPrices(true));
  $("#rank-csv").addEventListener("click", exportRank);
  $("#track-csv").addEventListener("click", exportTrack);
  $("#ports-csv").addEventListener("click", exportPorts);
  $("#entry-load").addEventListener("click", loadEntryCloses);
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
    contract: m.latest.contract, weights: m.latest.weights, book: m.latest.rankings.filter(x => x.side), title: "On latest close" };
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
  const hd = [["View", `${L.title}<br><small>${esc(kindText(L))}</small>`], ["Signal close", day(L.signal)],
    [L.kind === "latest" ? "If traded at the close of" : "Trade at close", day(L.rebalance)],
    ["Contract", `${futName(L.contract)}<br><small>expiry ${day(L.contract)}</small>`]];
  head.innerHTML = hd.map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  if (!L.book) {
    body.hidden = true; empty.hidden = false;
    empty.innerHTML = `The ${esc(kindText(L).toLowerCase())} on <b>${day(L.rebalance)}</b> uses the close of <b>${day(L.signal)}</b>. Run the model after that close (16:00 IST) and the book appears here.`;
    return;
  }
  empty.hidden = true; body.hidden = false;
  const held = S.model.schedules[S.sched].held;
  const heldSet = new Set((held.book || []).map(x => x.side + x.symbol));
  const isNew = r => S.which !== "held" && !heldSet.has(r.side + r.symbol);
  renderLadder(L, isNew); renderWeights(L.weights);
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
function exportBook() {
  if (!S.sized) return;
  const { L, rows } = S.sized;
  const hdr = ["schedule", "view", "signal_date", "trade_date", "contract_expiry", "side", "rank", "symbol", "sector", "style", "weight_in_leg", "target_notional_inr",
    "fut_price", "lot_size", "lots", "actual_notional_inr", "final_score", ...FACT.map(f => f + "_z"), "vol_63d", "cash_price"];
  downloadCSV(`prestige_book_${S.sched}_${L.signal}.csv`, hdr, rows.map(r => [S.sched, L.title, L.signal, L.rebalance, L.contract, r.side, r.rank, r.symbol, sect(r.sector), r.style,
    r6(r.weight), Math.round(r.target), r.fut, r.lot, r.lots == null ? null : (r.side === "LONG" ? r.lots : -r.lots), r.actual == null ? null : Math.round(r.actual),
    r6(r.final), ...FACT.map(f => r6(r[f])), r6(r.vol63), r.cash]));
}

/* ---------------------------------------------------------------- save portfolio */
function openSave() {
  if (!S.sized) return;
  const { L, rows } = S.sized;
  if (rows.some(r => r.lots == null)) { alertBar("Prices or lot sizes are missing for some stocks: refresh prices first.", true); return; }
  $("#save-sub").textContent = `${L.title}: signal ${day(L.signal)}, trade ${day(L.rebalance)}, ${futName(L.contract)}. Long capital ${inr(S.capital)}, short leg ${S.hedge}% of long.`;
  const past = S.which === "held" && L.rebalance < todayIST();
  $(`input[name="entry"][value="${past ? "close" : "live"}"]`).checked = true;
  $("#entry-date").value = past ? L.rebalance : todayIST();
  $("#entry-msg").textContent = past ? "This book traded at an earlier close: Load closes fills in that day's futures closes." : "";
  $("#save-name").value = `${S.sched[0].toUpperCase() + S.sched.slice(1)} book, ${day(past ? L.rebalance : todayIST())}`;
  $("#save-table").innerHTML = `<thead><tr><th class="t">Stock</th><th class="t">Side</th><th>Lots</th><th>Lot</th><th>Entry fut price</th><th>Fut now</th></tr></thead><tbody>` +
    rows.map((r, i) => `<tr class="${r.side === "LONG" ? "L" : "S"}"><td class="t">${esc(r.symbol)}</td><td class="t"><span class="tag ${r.side[0]}">${r.side.toLowerCase()}</span></td>
      <td><input data-i="${i}" data-k="lots" type="number" min="0" step="1" value="${r.lots}"></td><td>${r.lot}</td>
      <td><input data-i="${i}" data-k="entry_fut" type="number" step="0.05" value="${r.fut}"></td><td>${num(r.fut)}</td></tr>`).join("") + "</tbody>";
  $("#save-msg").textContent = "";
  $("#save-dlg").showModal();
}
async function loadEntryCloses() {
  const { L, rows } = S.sized, d = $("#entry-date").value;
  if (!d) return;
  $(`input[name="entry"][value="close"]`).checked = true;
  $("#entry-msg").textContent = `Fetching ${futName(L.contract)} closes for ${day(d)} from FYERS (up to a minute)…`;
  try {
    const r = await api("/api/fut_close", { method: "POST", body: JSON.stringify({ symbols: rows.map(x => x.symbol), contract: L.contract, date: d }) });
    rows.forEach((x, i) => { if (r.closes[x.symbol] != null) $(`#save-table input[data-i="${i}"][data-k="entry_fut"]`).value = r.closes[x.symbol]; });
    $("#entry-msg").textContent = r.missing.length ? `No close found for ${r.missing.join(", ")} on ${day(d)}: enter those prices yourself.` : `Entry prices set to the ${day(d)} futures closes.`;
  } catch (e) { $("#entry-msg").textContent = "Could not load closes: " + e.message; }
}
$("#save-confirm").addEventListener("click", async ev => {
  ev.preventDefault();
  const { L, rows } = S.sized;
  const inputs = $$("#save-table input");
  const pos = rows.map((r, i) => {
    const get = k => Number(inputs.find(x => +x.dataset.i === i && x.dataset.k === k).value);
    return { symbol: r.symbol, side: r.side, rank: r.rank, final: r.final, weight: r.weight, lots: get("lots"), lot_size: r.lot, entry_fut: get("entry_fut"), entry_cash: r.cash };
  });
  if (pos.some(p => !(p.lots >= 0) || !(p.entry_fut > 0))) { $("#save-msg").textContent = "Every row needs lots (0 or more) and an entry price."; return; }
  const mode = $('input[name="entry"]:checked').value;
  const entryDate = mode === "live" ? todayIST() : $("#entry-date").value;
  if (!entryDate) { $("#save-msg").textContent = "Choose the entry date."; return; }
  const body = { name: $("#save-name").value, schedule: S.sched, capital: S.capital, hedge: S.hedge / 100, trade_date: entryDate, signal_date: L.signal,
    contract: L.contract, positions: pos,
    note: JSON.stringify({ view: S.which, rebalance: L.rebalance, entry: mode, weights: L.weights, nifty_entry: mode === "live" && S.nifty ? S.nifty.lp : null }) };
  try {
    await api("/api/portfolios", { method: "POST", body: JSON.stringify(body) });
    $("#save-dlg").close();
    S.ports = await api("/api/portfolios"); S.livePx = null; S.series = null;
    $('.tabs button[data-tab="live"]').click();
    setTimeout(() => refreshLive(true), 20000);            // futures closes are being collected in the background
  } catch (e) { $("#save-msg").textContent = e.message; }
});

/* ---------------------------------------------------------------- live P&L */
function scheduleLive() { clearInterval(S.liveTimer); S.liveTimer = setInterval(() => refreshLive(true), Number(lsGet("every", "15")) * 60000); }
async function refreshLive(force = false) {
  const p = S.ports && S.ports.current;
  if (!p) return renderLive();
  const jobs = [];
  if (force || !S.livePx) jobs.push(api("/api/prices", { method: "POST", body: JSON.stringify({ symbols: p.positions.map(x => x.symbol), contract: p.contract }) })
    .then(r => { S.livePx = r.prices; S.liveAsof = r.asof; if (r.prices.NIFTY50) S.nifty = r.prices.NIFTY50.cash; }).catch(e => { S.liveErr = e.message; }));
  if (force || !S.series || S.series.id !== p.id) jobs.push(api(`/api/portfolios/${p.id}/series`).then(r => { S.series = r; }).catch(() => { S.series = null; }));
  await Promise.all(jobs);
  renderLive();
}
function pnlOf(p, px) {
  const out = p.positions.map(x => {
    const q = (px || {})[x.symbol] || {}, fut = q.fut ? q.fut.lp : null, cash = q.cash ? q.cash.lp : null, sign = x.side === "LONG" ? 1 : -1;
    const pnl = fut != null ? sign * x.lots * x.lot_size * (fut - x.entry_fut) : null;
    return { ...x, fut, cash, sign, pnl, notional: x.lots * x.lot_size * x.entry_fut, move: fut != null ? fut / x.entry_fut - 1 : null };
  });
  const sum = (rs, f) => rs.reduce((a, r) => a + (f(r) || 0), 0);
  const L = out.filter(r => r.side === "LONG"), Sh = out.filter(r => r.side === "SHORT");
  return { rows: out, L: { pnl: sum(L, r => r.pnl), n: sum(L, r => r.notional) }, S: { pnl: sum(Sh, r => r.pnl), n: sum(Sh, r => r.notional) } };
}
function livePoint(p, P) {
  /* today's point from live quotes, when today is after the last daily close in the series */
  const s = S.series, n = S.nifty;
  const last = s && s.dates.length ? s.dates[s.dates.length - 1] : null;
  const today = todayIST();
  if (last && last >= today) return null;
  let bench = null;
  if (s && s.bench.length && s.bench_last_date && n && n.prev_close) bench = (1 + s.bench[s.bench.length - 1]) * (n.lp / n.prev_close) - 1;
  else if (n) { let note = {}; try { note = JSON.parse(p.note || "{}"); } catch (e) { /* */ } if (note.nifty_entry) bench = n.lp / note.nifty_entry - 1; }
  return { date: today, ret: (P.L.pnl + P.S.pnl) / p.capital, bench };
}
function renderLive() {
  const p = S.ports && S.ports.current;
  $("#live-empty").hidden = !!p; $("#live-body").hidden = !p;
  if (!p) return;
  const P = pnlOf(p, S.livePx), net = P.L.pnl + P.S.pnl;
  const held = Math.max(0, Math.round((Date.now() - new Date(p.trade_date + "T15:30:00+05:30")) / 864e5));
  $("#live-title").innerHTML = `<b>${esc(p.name || "Current portfolio")}</b><br><span class="muted">Entry ${day(p.trade_date, true)} on ${futName(p.contract)}, saved by ${esc(p.created_by)}. ` +
    `${S.liveAsof ? "Prices " + S.liveAsof.slice(11, 16) + " IST." : (S.liveErr ? "Prices unavailable: " + esc(S.liveErr) : "")}</span>`;
  const lp = livePoint(p, P);
  const s = S.series;
  const benchNow = lp && lp.bench != null ? lp.bench : (s && s.bench.length ? s.bench[s.bench.length - 1] : null);
  const tile = (k, v, sub, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${sub}</div></div>`;
  const ret = net / p.capital;
  $("#live-tiles").innerHTML =
    tile("Net P&L", inr(net), `${pct(ret)} of long capital ${inr(p.capital)}`, sgn(net)) +
    tile("Nifty 50 TRI since entry", pct(benchNow), benchNow == null ? "after the first daily close" : `portfolio ${ret - benchNow >= 0 ? "ahead by" : "behind by"} ${pct(Math.abs(ret - benchNow)).replace("+", "")}`, sgn(benchNow)) +
    tile("Long leg", inr(P.L.pnl), `${pct(P.L.n ? P.L.pnl / P.L.n : null)} on ${inr(P.L.n)}`, sgn(P.L.pnl)) +
    tile("Short leg", inr(P.S.pnl), `${pct(P.S.n ? P.S.pnl / P.S.n : null)} on ${inr(P.S.n)}; ${held} days held`, sgn(P.S.pnl));
  renderLiveChart(lp);
  const rows = P.rows.slice().sort((a, b) => (a.side === b.side ? (b.pnl || 0) - (a.pnl || 0) : a.side === "LONG" ? -1 : 1));
  $("#live-table").innerHTML = `<thead><tr><th class="t">Stock</th><th class="t">Side</th><th>Lots</th><th>Lot</th><th>Entry fut</th><th>Fut now</th><th>Move</th><th>P&amp;L</th><th>Cash now</th></tr></thead><tbody>` +
    rows.map(r => `<tr class="${r.side[0]}"><td class="t">${esc(r.symbol)}</td><td class="t"><span class="tag ${r.side[0]}">${r.side.toLowerCase()}</span></td><td>${r.lots}</td><td>${r.lot_size}</td>
      <td>${num(r.entry_fut)}</td><td>${num(r.fut)}</td><td class="${sgn(r.sign * (r.move || 0))}">${pct(r.move)}</td>
      <td class="${sgn(r.pnl)}"><b>${inr(r.pnl)}</b></td><td>${num(r.cash)}</td></tr>`).join("") + "</tbody>";
  S.liveRows = rows;
}
function lineChart(id, labels, sets, yFmt = v => pct(v, 1)) {
  if (S.charts[id]) S.charts[id].destroy();
  const ink2 = cssVar("--ink-2"), line = cssVar("--line");
  S.charts[id] = new Chart(document.getElementById(id), {
    type: "line", data: { labels, datasets: sets },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: ink2, boxWidth: 14, usePointStyle: true } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${yFmt(c.parsed.y)}` } } },
      scales: { x: { ticks: { color: ink2, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { display: false } },
        y: { ticks: { color: ink2, callback: v => yFmt(v) }, grid: { color: line } } } },
  });
}
function renderLiveChart(lp) {
  const p = S.ports && S.ports.current, s = S.series;
  if (!p || typeof Chart === "undefined") return;
  if (lp === undefined) lp = livePoint(p, pnlOf(p, S.livePx));
  const dates = s ? s.dates.slice() : [], ret = s ? s.ret.slice() : [], bench = s ? s.bench.slice() : [];
  if (lp) { dates.push(lp.date); ret.push(lp.ret); bench.push(lp.bench); }
  const start = p.trade_date;
  if (!dates.length || dates[0] > start) { dates.unshift(start); ret.unshift(0); bench.unshift(0); }
  $("#live-chart-note").textContent = (s && s.pending ? "Daily closes are being collected from FYERS; the line fills in after the next refresh. " : "") +
    (lp ? "Last point is live." : "") + (s && s.missing && s.missing.length ? ` No futures data yet for ${s.missing.join(", ")}.` : "");
  const long = cssVar("--long"), benchC = cssVar("--bench");
  lineChart("live-chart", dates.map(short), [
    { label: "Portfolio (% of long capital)", data: ret, borderColor: long, backgroundColor: long + "22", fill: "origin", tension: 0.15, pointRadius: 2, borderWidth: 2 },
    { label: "Nifty 50 TRI", data: bench, borderColor: benchC, borderDash: [6, 4], pointRadius: 0, borderWidth: 2, spanGaps: true },
  ]);
}
function exportLive() {
  const p = S.ports && S.ports.current; if (!p || !S.liveRows) return;
  downloadCSV(`prestige_live_${p.trade_date}.csv`, ["portfolio", "entry_date", "contract_expiry", "symbol", "side", "lots", "lot_size", "entry_fut", "fut_now", "move", "pnl_inr", "cash_now", "prices_at"],
    S.liveRows.map(r => [p.name, p.trade_date, p.contract, r.symbol, r.side, r.lots, r.lot_size, r.entry_fut, r.fut, r6(r.move), r.pnl == null ? null : Math.round(r.pnl), r.cash, S.liveAsof]));
}
function exportSeries() {
  const p = S.ports && S.ports.current, s = S.series; if (!p || !s) return;
  downloadCSV(`prestige_portfolio_series_${p.trade_date}.csv`, ["date", "pnl_inr", "long_pnl_inr", "short_pnl_inr", "return_on_long_capital", "nifty50_tri_return"],
    s.dates.map((d, i) => [d, s.pnl[i], s.long_pnl[i], s.short_pnl[i], s.ret[i], s.bench[i]]));
}
async function closePortfolio() {
  const p = S.ports && S.ports.current; if (!p) return;
  if (!confirm(`Close "${p.name}"? Its exit prices are recorded at the current futures prices and it moves to the track record.`)) return;
  try { await api(`/api/portfolios/${p.id}/close`, { method: "POST" }); S.ports = await api("/api/portfolios"); S.series = null; renderLive(); renderTrack(); }
  catch (e) { alertBar(e.message, true); }
}

/* ---------------------------------------------------------------- track record */
function trackRows() {
  const m = S.model; if (!m || !m.track) return [];
  const h = S.hedge / 100;
  return m.track[S.sched].map(t => {
    const n = t.dates.length - 1;
    const L = t.long[n], Sh = t.short[n], B = t.bench[n], R = L - h * Sh;
    return { ...t, sessions: n, L, Sh, B, R, X: R - B };
  });
}
function chained() {
  const m = S.model; if (!m || !m.track) return null;
  const h = S.hedge / 100, out = { dates: [], book: [], bench: [] };
  let V = 1, Bv = 1;
  m.track[S.sched].forEach((t, k) => {
    t.dates.forEach((d, j) => {
      if (k > 0 && j === 0) return;
      out.dates.push(d); out.book.push(V * (1 + t.long[j] - h * t.short[j]) - 1); out.bench.push(Bv * (1 + t.bench[j]) - 1);
    });
    const n = t.dates.length - 1;
    V *= 1 + t.long[n] - h * t.short[n]; Bv *= 1 + t.bench[n];
  });
  return out;
}
function portRows() {
  const m = S.model, hist = (S.ports && S.ports.history) || [];
  const bench = m ? m.bench || {} : {};
  const bd = Object.keys(bench).sort();
  const at = d => { let v = null; for (const x of bd) { if (x <= d) v = bench[x]; else break; } return v; };
  return hist.map(p => {
    const end = p.closed_at ? String(p.closed_at).slice(0, 10) : (m ? m.data_end : null);
    let pnl = null;
    if (p.exit_prices) pnl = p.positions.reduce((a, x) => { const e = p.exit_prices[x.symbol]; return e && e.fut ? a + (x.side === "LONG" ? 1 : -1) * x.lots * x.lot_size * (e.fut - x.entry_fut) : a; }, 0);
    let B = null;
    if (p.status === "current" && S.livePx) {
      const P = pnlOf(p, S.livePx); pnl = P.L.pnl + P.S.pnl;
      if (S.series && S.series.id === p.id) {                       // same moment as the live P&L
        const lp = livePoint(p, P);
        B = lp && lp.bench != null ? lp.bench : (S.series.bench.length ? S.series.bench[S.series.bench.length - 1] : null);
      }
    }
    const b0 = at(p.trade_date), b1 = end ? at(end) : null;
    if (B == null) B = b0 && b1 ? b1 / b0 - 1 : null;
    const R = pnl != null ? pnl / p.capital : null;
    return { ...p, end, pnl, R, B, X: R != null && B != null ? R - B : null };
  });
}
function renderTrack() {
  const m = S.model;
  const rows = trackRows();
  $("#track-note").textContent = m ? `Live track record from ${day(m.live_start, true)}: each ${S.sched} rebalance's book, held to the next rebalance (or the latest close), against the Nifty 50 TRI over the same days. Short leg at ${S.hedge}% of long; no charges.` : "";
  $("#bench-note").textContent = m && m.bench_note ? "Benchmark: " + m.bench_note + "." : "";
  if (!m || !rows.length) { $("#track-tiles").innerHTML = ""; $("#track-table").innerHTML = '<tbody><tr><td class="t muted">No rebalances yet.</td></tr></tbody>'; renderPorts(); return; }
  const c = chained(), n = c.dates.length - 1;
  const done = rows.filter(r => !r.open);
  const hit = rows.reduce((a, r) => a + (r.hit_long + r.hit_short) / 2, 0) / rows.length;
  const beat = done.length ? done.filter(r => r.X > 0).length / done.length : null;
  const tile = (k, v, sub, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${sub}</div></div>`;
  $("#track-tiles").innerHTML =
    tile("Model book since " + short(m.live_start), pct(c.book[n]), `${rows.length} rebalances, ${S.sched}`, sgn(c.book[n])) +
    tile("Nifty 50 TRI, same days", pct(c.bench[n]), `difference ${pct(c.book[n] - c.bench[n])}`, sgn(c.bench[n])) +
    tile("Rebalances beating the TRI", beat == null ? "–" : Math.round(beat * 100) + "%", `${done.filter(r => r.X > 0).length} of ${done.length} completed`) +
    tile("Average hit rate", Math.round(hit * 100) + "%", "longs that rose and shorts that fell");
  if (typeof Chart !== "undefined") {
    lineChart("track-chart", c.dates.map(short), [
      { label: "Model book", data: c.book, borderColor: cssVar("--long"), backgroundColor: cssVar("--long") + "22", fill: "origin", tension: 0.15, pointRadius: 0, borderWidth: 2 },
      { label: "Nifty 50 TRI", data: c.bench, borderColor: cssVar("--bench"), borderDash: [6, 4], pointRadius: 0, borderWidth: 2 },
    ]);
    if (S.charts["track-bars"]) S.charts["track-bars"].destroy();
    const ink2 = cssVar("--ink-2");
    S.charts["track-bars"] = new Chart($("#track-bars"), {
      type: "bar",
      data: { labels: rows.map(r => short(r.rebalance) + (r.open ? " (open)" : "")),
        datasets: [{ label: "Book", data: rows.map(r => r.R), backgroundColor: rows.map(r => r.R >= 0 ? cssVar("--pos-bar") : cssVar("--neg-bar")), borderRadius: 3 },
          { label: "Nifty 50 TRI", data: rows.map(r => r.B), backgroundColor: cssVar("--bench") + "99", borderRadius: 3 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: ink2, boxWidth: 14 } }, tooltip: { callbacks: { label: x => `${x.dataset.label}: ${pct(x.parsed.y)}` } } },
        scales: { x: { ticks: { color: ink2 }, grid: { display: false } }, y: { ticks: { color: ink2, callback: v => pct(v, 1) }, grid: { color: cssVar("--line") } } } },
    });
  }
  $("#track-table").innerHTML = `<thead><tr><th class="t">Rebalance</th><th class="t">Type</th><th>Signal</th><th>Held to</th><th>Sessions</th><th>Book</th><th>Long leg</th><th>Short leg</th>
    <th>Nifty 50 TRI</th><th>Book − TRI</th><th>Hit long</th><th>Hit short</th><th class="t">Factor weights</th></tr></thead><tbody>` +
    rows.slice().reverse().map(r => `<tr class="${r.open ? "open" : ""}"><td class="t"><b>${day(r.rebalance, true)}</b>${r.open ? ' <span class="tag w">open</span>' : ""}</td>
      <td class="t">${r.kind === "monthly" ? "Monthly (E-1)" : "E-1 + " + r.offset}</td><td>${day(r.signal)}</td><td>${day(r.end)}</td><td>${r.sessions}</td>
      <td class="${sgn(r.R)}"><b>${pct(r.R)}</b></td><td class="${sgn(r.L)}">${pct(r.L)}</td><td class="${sgn(-r.Sh)}">${pct(r.Sh)}</td><td class="${sgn(r.B)}">${pct(r.B)}</td>
      <td class="${sgn(r.X)}"><b>${pct(r.X)}</b></td><td>${Math.round(r.hit_long * 100)}%</td><td>${Math.round(r.hit_short * 100)}%</td>
      <td class="t small">${FACT.map(k => `${k} ${(r.weights[k] * 100).toFixed(0)}%`).join(", ")}</td></tr>`).join("") + "</tbody>";
  renderPorts();
}
function renderPorts() {
  const rows = portRows();
  if (!rows.length) { $("#ports-table").innerHTML = '<tbody><tr><td class="t muted">None yet. Saved portfolios appear here with their result against the Nifty 50 TRI.</td></tr></tbody>'; return; }
  $("#ports-table").innerHTML = `<thead><tr><th class="t">Portfolio</th><th class="t">Schedule</th><th>Entry</th><th>Exit</th><th>Contract</th><th>Long capital</th><th>P&amp;L</th>
    <th>Return</th><th>Nifty 50 TRI</th><th>Return − TRI</th><th class="t">Status</th><th class="t">Saved by</th></tr></thead><tbody>` +
    rows.map(p => `<tr class="${p.status === "current" ? "open" : ""}"><td class="t">${esc(p.name)}</td><td class="t">${esc(p.schedule)}</td><td>${day(p.trade_date, true)}</td>
      <td>${p.closed_at ? day(p.end, true) : "–"}</td><td>${esc(p.contract)}</td><td>${inr(p.capital)}</td><td class="${sgn(p.pnl)}">${inr(p.pnl)}</td>
      <td class="${sgn(p.R)}"><b>${pct(p.R)}</b></td><td class="${sgn(p.B)}">${pct(p.B)}</td><td class="${sgn(p.X)}"><b>${pct(p.X)}</b></td>
      <td class="t">${p.status === "current" ? '<span class="tag L">current</span>' : "closed"}</td><td class="t">${esc(p.created_by)}</td></tr>`).join("") + "</tbody>";
}
function exportTrack() {
  const rows = trackRows(); if (!rows.length) return;
  downloadCSV(`prestige_track_${S.sched}_hedge${S.hedge}.csv`, ["schedule", "rebalance_date", "type", "signal_date", "held_to", "sessions", "open", "book_return", "long_leg", "short_leg",
    "nifty50_tri", "book_minus_tri", "hit_rate_long", "hit_rate_short", ...FACT.map(k => "w_" + k), "longs", "shorts"],
    rows.map(r => [S.sched, r.rebalance, r.kind === "monthly" ? "E-1" : "E-1+" + r.offset, r.signal, r.end, r.sessions, r.open ? "yes" : "no", r6(r.R), r6(r.L), r6(r.Sh), r6(r.B), r6(r.X),
      r6(r.hit_long), r6(r.hit_short), ...FACT.map(k => r.weights[k]), r.longs.join(" "), r.shorts.join(" ")]));
}
function exportPorts() {
  const rows = portRows(); if (!rows.length) return;
  downloadCSV("prestige_saved_portfolios.csv", ["name", "schedule", "entry_date", "exit_date", "contract_expiry", "long_capital", "hedge", "pnl_inr", "return_on_long_capital", "nifty50_tri", "return_minus_tri", "status", "saved_by"],
    rows.map(p => [p.name, p.schedule, p.trade_date, p.closed_at ? p.end : null, p.contract, p.capital, p.hedge, p.pnl == null ? null : Math.round(p.pnl), r6(p.R), r6(p.B), r6(p.X), p.status, p.created_by]));
}

/* ---------------------------------------------------------------- rankings */
async function ensureRankPrices(force = false) {
  if (!S.model) return;
  await fetchPrices(S.model.latest.rankings.map(r => r.symbol), null, force);
  renderRank();
}
function zcell(v) {
  if (v == null) return "<td>–</td>";
  const a = Math.min(Math.abs(v) / 3, 1) * 0.42, c = v > 0 ? `rgba(14,140,120,${a})` : `rgba(190,40,63,${a})`;
  return `<td style="background:${c}">${num(v)}</td>`;
}
function rankRows() {
  const m = S.model, px = S.px.cash || {};
  const hs = Object.fromEntries((m.schedules[S.sched].held.book || []).map(x => [x.symbol, x.side]));
  return m.latest.rankings.map(r => ({ ...r, cash: px[r.symbol] && px[r.symbol].cash ? px[r.symbol].cash.lp : null,
    chp: px[r.symbol] && px[r.symbol].cash ? px[r.symbol].cash.chp / 100 : null, inforce: hs[r.symbol] || "" }));
}
function renderRank() {
  const m = S.model; if (!m) { $("#rank-table").innerHTML = ""; return; }
  $("#rank-title").textContent = `Rankings on the close of ${day(m.latest.signal, true)}`;
  $("#rank-asof").textContent = S.pxAsof.cash ? "Prices " + S.pxAsof.cash.slice(11, 16) + " IST" : "";
  const rows = rankRows();
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
    const kk = th.dataset.k; S.rankSort = { k: kk, d: S.rankSort.k === kk ? -S.rankSort.d : (["symbol", "sector", "style", "side", "inforce", "rank"].includes(kk) ? 1 : -1) }; renderRank();
  }));
}
function exportRank() {
  const m = S.model; if (!m) return;
  downloadCSV(`prestige_rankings_${m.latest.signal}.csv`, ["rank", "symbol", "side_now", "in_book_in_force", "sector", "style", "final_score", "composite", ...FACT.map(f => f + "_z"), "vol_63d", "cash_price", "today"],
    rankRows().sort((a, b) => a.rank - b.rank).map(r => [r.rank, r.symbol, r.side, r.inforce, sect(r.sector), r.style, r6(r.final), r6(r.composite), ...FACT.map(f => r6(r[f])), r6(r.vol63), r.cash, r6(r.chp)]));
}

/* ---------------------------------------------------------------- status */
function renderStatus() {
  const st = S.status, m = S.model;
  if (!st) { $("#status-body").innerHTML = `<div class="box"><h2>Status unavailable</h2><p class="bad">${esc(S.statusErr || "The server did not answer.")}</p></div>`; return; }
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
    <dt>FYERS settings</dt><dd>${(st.fyers_problems || []).length ? `<ul>${st.fyers_problems.map(x => `<li class="bad">${esc(x)}</li>`).join("")}</ul>` : yes(st.fyers_configured, "filled in", "missing: fill them in .env (or Render, Environment)")}</dd>
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
    <li>A corporate action FYERS has not adjusted: data/ref/price_adjustments.csv (symbol, ex-date, factor).</li></ul></div>`;
}

boot();
