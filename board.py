#!/usr/bin/env python3
"""AI HEDGE FUND — BOARD (Flask dashboard + background scan loop).

  Always-on web service on Render Premium (Starter, no sleep) with a
  persistent disk for the paper ledger. A background thread runs the
  scan cycle every SCAN_INTERVAL_SECONDS (default 4h = 14400s, the
  backtest's sampling cadence): close-check -> deposit-flow signal ->
  crypto scan -> gold live signal. The board serves live status only;
  it never places real orders and exposes no mutating endpoints.

  Routes:
    GET /            dashboard: scan status, deposit-flow readings,
                     signals, open/closed paper trades, ledger stats,
                     gold signals + gold backtest summary
    GET /signals     deposit-flow signal dashboard: per-coin ranking with
                     progress bars for expected upside % and deposit flow,
                     sorted by expected upside, signal (entry/TP/SL) for
                     coins that pass; auto-refreshes every 120s
    GET /health      {"ok": true} — Render health check
    GET /api/status  scan state as JSON
    GET /api/trades  open + recently closed paper trades
    GET /api/ledger  ledger stats (win rate, total R, $ PnL)
    GET /api/deposit last deposit-flow check
    GET /api/gold    last gold live signal + backtest summary
"""
import json
import os
import sys
import threading
import time

from flask import Flask, jsonify, render_template_string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fund
import ledger
import deposit_flow
import gold_strategy
import git_persist
import data
import risk

_BASE = os.path.dirname(os.path.abspath(__file__))
INTERVAL = int(os.environ.get("SCAN_INTERVAL_SECONDS", "14400"))
SCAN_LOG = os.environ.get("SCAN_LOG_PATH") or os.path.join(_BASE, "data", "scan_log.jsonl")
GOLD_BT = os.path.join(_BASE, "data", "gold_backtest.json")

app = Flask(__name__)

STATE = {
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scan_count": 0,
    "last_scan_at": None,
    "next_scan_at": None,
    "last_scan_summary": "not run yet",
    "signal_mode": "deposit",
    "last_deposit": {"status": "not run yet"},
    "last_gold": {"ok": False, "reason": "not run yet"},
    "last_error": None,
    "scanning": False,
}
_lock = threading.Lock()


def _log_scan(entry):
    try:
        os.makedirs(os.path.dirname(SCAN_LOG), exist_ok=True)
        with open(SCAN_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[board] scan log write failed: {e}")


def scan_cycle():
    """One full cycle: close-check -> deposit-flow -> crypto scan -> gold."""
    if not _lock.acquire(blocking=False):
        print("[board] previous cycle still running — skipping this tick")
        return
    try:
        STATE["scanning"] = True
        t0 = time.time()
        print(f"[board] === scan cycle #{STATE['scan_count'] + 1} starting ===",
              flush=True)
        dep = deposit_flow.check()
        STATE["last_deposit"] = dep
        results = fund.run_scan(use_llm=False)
        STATE["signal_mode"] = getattr(fund, "LAST_SCAN_MODE", "deposit")
        n_trades = sum(1 for r in results if r.get("signal") in ("BUY", "SELL"))
        gold = gold_strategy.live_signal()
        STATE["last_gold"] = gold
        stats = ledger.stats()
        summary = (f"mode: {STATE['signal_mode']}; {len(results)} coins analyzed, "
                   f"{n_trades} paper trades opened; "
                   f"deposit: {dep['status']}; gold 4H: "
                   f"{gold.get('trend_4h', '?')}; ledger: {stats}")
        STATE["last_scan_summary"] = summary
        STATE["scan_count"] += 1
        STATE["last_scan_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        STATE["next_scan_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + INTERVAL))
        STATE["last_error"] = None
        _log_scan({"ts": STATE["last_scan_at"], "summary": summary,
                   "deposit": dep.get("status"), "stats": stats})
        git_persist.persist_ledger()
        print(f"[board] === cycle done in {time.time() - t0:.0f}s: {summary} ===",
              flush=True)
    except Exception as e:
        STATE["last_error"] = str(e)[:300]
        print(f"[board] cycle FAILED: {e}", flush=True)
    finally:
        STATE["scanning"] = False
        _lock.release()


def _loop():
    time.sleep(10)  # let the web server boot first
    while True:
        scan_cycle()
        time.sleep(INTERVAL)


def _gold_backtest():
    try:
        with open(GOLD_BT) as f:
            return json.load(f)
    except Exception:
        return None


def _trades_json():
    trades = ledger.all_trades()
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"][-20:]
    return open_t, closed


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300">
<title>AI Hedge Fund — Board</title>
<style>
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:20px}
h1{font-size:22px}h2{font-size:16px;border-bottom:1px solid #30363d;padding-bottom:6px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #30363d;padding:6px 8px;text-align:left}
th{color:#8b949e}.win{color:#3fb950}.loss{color:#f85149}.muted{color:#8b949e}
.pill{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;background:#1f6feb;color:#fff}
</style></head><body>
<h1>AI Hedge Fund — Board <span class="pill">PAPER ONLY</span></h1>
<p><a href="/signals" style="color:#58a6ff">Deposit-flow signals &rarr;</a></p>
<p class="muted">Deposit ${{ equity }} &middot; 1% risk/trade &middot; TP &ge; 1.5R &middot;
scan every {{ interval_h }}h &middot; started {{ started_at }} UTC</p>

<div class="card"><h2>Live scan status</h2>
<p>Scans run: <b>{{ scan_count }}</b> &middot; Last scan: <b>{{ last_scan_at or "—" }}</b>
&middot; Next scan: <b>{{ next_scan_at or "—" }}</b>
{% if scanning %}&middot; <b>SCANNING NOW…</b>{% endif %}</p>
<p>Signal mode: <b>{{ signal_mode }}</b></p>
<p>{{ last_scan_summary }}</p>
{% if last_error %}<p class="loss">Last error: {{ last_error }}</p>{% endif %}</div>

<div class="card"><h2>Deposit-flow readings (crypto primary signal)</h2>
<p>Status: <b>{{ deposit.status }}</b> — {{ deposit.reason }}</p>
{% if deposit.signal %}<p>Signal: <b>{{ deposit.symbol }} {{ deposit.direction }}</b>,
expected move {{ deposit.expected_move_pct }}% ({{ deposit.source }})</p>{% endif %}
{% if deposit.source and deposit.source.startswith('free:') %}<p class="muted">Model uncalibrated — see deposit_flow.py. Free route: ETH exchange deposits
from hot wallets ({{ deposit.source }}); no paid key. ETH only — no free source
covers other coins. Mempool large transfers seen (unattributed, context only): {{ deposit.mempool_large_transfers }}</p>
{% elif signal_mode == "smc-only (deposit NO_DATA)" %}<p class="muted">Model uncalibrated — see deposit_flow.py. No attributed
exchange-flow source is configured (Whale Alert paid / CryptoQuant Professional
key not set) — the scanner is running SMC-only until deposit data exists.
Mempool large transfers seen (unattributed, context only): {{ deposit.mempool_large_transfers }}</p>
{% else %}<p class="muted">Model uncalibrated — see deposit_flow.py. An attributed
exchange-flow source needs a Whale Alert (paid) or CryptoQuant Professional key;
without one the scanner runs SMC-only. Mempool large transfers seen
(unattributed, context only): {{ deposit.mempool_large_transfers }}</p>{% endif %}</div>

<div class="card"><h2>Ledger stats</h2>
<p>Open: <b>{{ stats.open }}</b> &middot; Closed: <b>{{ stats.closed }}</b>
&middot; Wins: <b>{{ stats.wins }}</b> &middot; Live win rate:
<b>{{ stats.live_win_rate_pct if stats.live_win_rate_pct is not none else "—" }}%</b>
(live n={{ stats.live_closed }}) &middot; Total R: <b>{{ stats.total_r }}</b>
&middot; Live R: <b>{{ stats.live_total_r }}</b>
&middot; $ PnL at ${{ equity }}: <b>${{ (stats.live_total_r * equity / 100) | round(2) }}</b></p></div>

<div class="card"><h2>Open paper trades</h2>
{% if open_trades %}<table><tr><th>ID</th><th>Symbol</th><th>Dir</th><th>Entry</th>
<th>SL</th><th>TP</th><th>Risk $</th><th>Opened</th></tr>
{% for t in open_trades %}<tr><td>{{ t.id }}</td><td>{{ t.symbol }}</td>
<td>{{ t.direction }}</td><td>{{ t.entry }}</td><td>{{ t.sl }}</td><td>{{ t.tp }}</td>
<td>{{ t.risk_usd }}</td><td>{{ t.opened_at }}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">No open paper trades.</p>{% endif %}</div>

<div class="card"><h2>Closed paper trades (latest 20)</h2>
{% if closed_trades %}<table><tr><th>Symbol</th><th>Dir</th><th>Entry</th>
<th>Exit</th><th>R</th><th>Result</th><th>Reason</th></tr>
{% for t in closed_trades %}<tr><td>{{ t.symbol }}</td><td>{{ t.direction }}</td>
<td>{{ t.entry }}</td><td>{{ t.exit_price }}</td><td>{{ t.r_multiple }}</td>
<td class="{{ 'win' if t.win else 'loss' }}">{{ 'WIN' if t.win else 'LOSS' }}</td>
<td>{{ t.exit_reason }}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">No closed trades yet.</p>{% endif %}</div>

<div class="card"><h2>Gold signals (XAUUSD via PAXGUSDT — SMC playbook, own strategy)</h2>
{% if gold.ok %}<p>Last close: <b>${{ gold.last_close }}</b> &middot; 4H trend:
<b>{{ gold.trend_4h }}</b> &middot; as of {{ gold.asof }}</p>
{% if gold.signals_last_24h %}<p>Signals (last 24h):</p><table>
<tr><th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>Model</th></tr>
{% for s in gold.signals_last_24h %}<tr><td>{{ s.t }}</td><td>{{ 'LONG' if s.d==1 else 'SHORT' }}</td>
<td>{{ s.entry }}</td><td>{{ s.sl }}</td><td>{{ s.tp }}</td><td>{{ s.model }}</td></tr>
{% endfor %}</table>{% else %}<p class="muted">No gold signals in the last 24h.</p>{% endif %}
{% if gold.armed %}<p>Armed setup: {{ gold.armed }}</p>{% endif %}
{% else %}<p class="loss">{{ gold.reason }}</p>{% endif %}
{% if gold_bt %}<p>Backtest (honest, PAXG 15m):</p><table>
<tr><th>Variant</th><th>Trades</th><th>Win rate</th><th>Total R</th><th>Max DD</th><th>$ PnL</th></tr>
{% for name, r in gold_bt.items() %}<tr><td>{{ name }}</td><td>{{ r.n }}</td>
<td>{{ r.win_rate_pct }}%</td><td>{{ r.total_r }}R</td><td>{{ r.max_drawdown_pct }}%</td>
<td>${{ r.pnl_usd }}</td></tr>{% endfor %}</table>
<p class="muted">80% win-rate target: NOT reached by any variant — see the
backtest numbers above (small samples, reported with n).</p>{% endif %}</div>

<p class="muted">Paper trading only. No real money, no broker keys, no live orders.</p>
</body></html>"""


SIGNALS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="120">
<title>Deposit-flow signals — AI Hedge Fund</title>
<style>
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:20px}
h1{font-size:22px}h2{font-size:16px;border-bottom:1px solid #30363d;padding-bottom:6px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #30363d;padding:6px 8px;text-align:left;vertical-align:middle}
th{color:#8b949e}.muted{color:#8b949e}.win{color:#3fb950}
.bar{background:#30363d;border-radius:4px;height:14px;min-width:120px;overflow:hidden}
.fill{background:#3fb950;height:100%}
.fill-flow{background:#58a6ff;height:100%}
.pill{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;background:#1f6feb;color:#fff}
a{color:#58a6ff}
</style></head><body>
<h1>Deposit-flow signals <span class="pill">PAPER ONLY</span></h1>
<p><a href="/">&larr; Back to board</a> &middot; auto-refreshes every 120s &middot;
last scan: <b>{{ last_scan_at or "—" }}</b></p>

<div class="card"><h2>Which coins are getting deposits — and how much up?</h2>
<p>Status: <b>{{ deposit.status }}</b> — {{ deposit.reason }}</p>
{% if ranking %}
<p class="muted">Ranked by expected upside (highest first). Bars are scaled to the
largest value in this table (100% = the top row). Expected upside =
sensitivity &times; deposits / market cap &times; 100. TP = entry &times;
(1 + expected move%); SL = below the sweep.</p>
<table><tr><th>#</th><th>Coin</th><th>Deposits (24h)</th><th>Market cap</th>
<th>Expected upside</th><th>Deposit flow</th><th>Signal</th></tr>
{% for r in ranking|sort(attribute='expected_move_pct', reverse=True) %}
<tr><td>{{ loop.index }}</td><td><b>{{ r.symbol }}</b></td>
<td>${{ "{:,}".format(r.deposit_usd) }}</td>
<td>${{ "{:,}".format(r.market_cap_usd) }}</td>
<td><b>{{ r.expected_move_pct }}%</b>
<div class="bar"><div class="fill" style="width:{{ (r.expected_move_pct / max_move * 100)|round(1) }}%"></div></div></td>
<td>${{ "{:,}".format(r.deposit_usd) }}
<div class="bar"><div class="fill-flow" style="width:{{ (r.deposit_usd / max_dep * 100)|round(1) }}%"></div></div></td>
<td>{% if sig and r.symbol == sig.symbol.replace('USDT','') %}
<span class="win"><b>{{ sig.direction|upper }}</b></span><br>
Entry ${{ sig.entry }}<br>TP ${{ sig.tp }}<br>SL ${{ sig.sl }} ({{ sig.sl_rule }})
{% elif sig and sig.error %}—{% else %}—{% endif %}</td></tr>
{% endfor %}</table>
{% if sig and sig.error %}<p class="muted">Signal price lookup failed: {{ sig.error }}</p>{% endif %}
{% else %}
<p class="muted">No per-coin deposit data — the ranking is empty until an
attributed exchange-flow source is configured (Whale Alert paid or
CryptoQuant Professional). Deposit types and their status:</p>
<table><tr><th>Deposit type</th><th>Status</th><th>Amount (USD)</th><th>Note</th></tr>
{% for name, t in deposit.types.items() %}<tr><td>{{ name }}</td><td>{{ t.status }}</td>
<td>{% if t.amount_usd is not none %}${{ "{:,}".format(t.amount_usd) }}{% else %}—{% endif %}</td>
<td>{{ t.reason }}</td></tr>{% endfor %}</table>
{% endif %}
<p class="muted">Deposit-flow model is UNCALIBRATED (sensitivity has no
historical calibration) — the % is the formula's output, not a measured
forecast. Gold is not in this table (no on-chain deposits); see the board
for gold signals.</p></div>

<p class="muted">Paper trading only. No real money, no broker keys, no live orders.</p>
</body></html>"""


@app.route("/signals")
def signals():
    dep = STATE["last_deposit"]
    if "types" not in dep:
        # Before the first scan cycle: no per-type table yet. Normalize
        # so the template renders the "no data" state instead of 500ing.
        dep = {**dep, "types": {}, "ranking": []}
    ranking = dep.get("ranking") or []
    max_move = max((r["expected_move_pct"] for r in ranking), default=0) or 1
    max_dep = max((r["deposit_usd"] for r in ranking), default=0) or 1
    sig = None
    if dep.get("signal") and dep.get("top"):
        try:
            price = deposit_flow._okx_price_usd(dep["symbol"])
            entry = price
            tp = round(entry * (1 + dep["expected_move_pct"] / 100), 4)
            candles = data.fetch_candles(dep["symbol"], "1h", 200)
            sl, swept, found = risk.sweep_below_sl(
                dep.get("direction", "long"), candles, entry)
            if not found:
                plan = risk.plan(dep.get("direction", "long"), candles,
                                 fund.CFG.get("paper_equity_usd", 100),
                                 fund.CFG.get("risk_pct", 1))
                sl = plan["sl"]
            sig = {"symbol": dep["symbol"], "direction": dep.get("direction"),
                   "entry": round(entry, 4), "tp": tp, "sl": sl,
                   "sl_rule": "sweep" if found else "structural (no recent sweep)",
                   "expected_move_pct": dep["expected_move_pct"]}
        except Exception as e:
            sig = {"error": str(e)[:120]}
    return render_template_string(
        SIGNALS_PAGE, deposit=dep, ranking=ranking,
        max_move=max_move, max_dep=max_dep, sig=sig,
        last_scan_at=STATE["last_scan_at"])


@app.route("/")
def index():
    open_t, closed = _trades_json()
    return render_template_string(
        PAGE, equity=fund.CFG.get("paper_equity_usd", 100),
        interval_h=INTERVAL / 3600, started_at=STATE["started_at"],
        scan_count=STATE["scan_count"], last_scan_at=STATE["last_scan_at"],
        next_scan_at=STATE["next_scan_at"], scanning=STATE["scanning"],
        signal_mode=STATE["signal_mode"],
        last_scan_summary=STATE["last_scan_summary"],
        last_error=STATE["last_error"], deposit=STATE["last_deposit"],
        stats=ledger.stats(), open_trades=open_t, closed_trades=closed,
        gold=STATE["last_gold"], gold_bt=_gold_backtest())


@app.route("/health")
def health():
    return jsonify({"ok": True, "scans": STATE["scan_count"],
                    "last_scan_at": STATE["last_scan_at"]})


@app.route("/api/status")
def api_status():
    return jsonify({k: v for k, v in STATE.items()})


@app.route("/api/trades")
def api_trades():
    open_t, closed = _trades_json()
    return jsonify({"open": open_t, "closed": closed})


@app.route("/api/ledger")
def api_ledger():
    return jsonify(ledger.stats())


@app.route("/api/deposit")
def api_deposit():
    return jsonify(STATE["last_deposit"])


@app.route("/api/gold")
def api_gold():
    return jsonify({"live": STATE["last_gold"], "backtest": _gold_backtest()})


if __name__ == "__main__":
    threading.Thread(target=_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
