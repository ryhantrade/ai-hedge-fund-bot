#!/usr/bin/env python3
"""GOLD STRATEGY — XAUUSD via PAXGUSDT (gold-backed token, OKX).

  Uses the SAME mechanical SMC playbook as the BTC 4H/15m engine
  (smc_engine.py, copied unchanged from ~/workspace/backtest/):
  4H BOS -> premium/discount -> 4H POI -> 1H align+POI -> 15m
  sweep/shift/pullback (Shift) or breakout-retest (Flip).
  1% risk per trade, set & forget.

  SCOPE NOTE (user spec 2026-11-24): deposit-flow does NOT apply to
  gold — gold has no on-chain deposits. Gold gets its own strategy
  here; the deposit logic is never forced onto gold.

  INDEPENDENT-SEARCH CANDIDATE ONLY: this playbook is the best
  strategy found by independent search (best backtested 42.9% win,
  n=7, 2y GC=F 1h). It is NOT the user's playlist strategy — that
  comes from a YouTuber playlist whose link is PENDING (requested
  2026-11-24 23:27 +06) and will be built as a SEPARATE bot
  (gold_bot.py / gold_config.json). Until the playlist arrives this
  module's signals are display-only on the board; no gold paper
  trades are opened automatically.

  DATA: two datasets, each ~60-70 days (the longest honest window
  available for a 15m/1h gold backtest):
  - GC=F 15m from yfinance (60d max, ~4500 bars) — real gold futures,
    true 15m entry TF. Futures close weekends; gaps are handled by
    bar index.
  - PAXGUSDT 1h from OKX (1440 bars ~= 60 days; OKX caps PAXG history
    at 1440 candles per granularity) — 1H-ENTRY variant, a different
    entry TF, measured separately and labelled _1h.
  PAXG tracks spot gold 1:1 by design, but it trades 24/7 (spot gold
  closes weekends) and carries a small premium/discount plus thinner
  liquidity than spot — slippage/fees may differ from XAUUSD.

  VARIANTS (each measured separately; TP floor 1.5R enforced on every
  deployable variant — the 1.0R Flip in the raw engine is measured as
  a baseline only and is NOT tradable under the user's TP floor):
    base             engine as-is (Shift extend3 TP, Flip fixed 1:1)
    floor15          Flip TP raised to fixed 1.5R (Shift unchanged)
    floor15_session  floor15 + entries only in London 07:00-11:00 UTC
                     or New York 12:00-16:00 UTC
    fixed15          every TP fixed at 1.5R (no POI extension)

  Backtest accounting: $100 equity, $1 risk/trade (1%), 0.05%/side
  fee (same as the BTC engine). R multiples come from the engine's
  simulate(); the equity curve + max drawdown are rebuilt from those
  R values in trade order.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import smc_engine as E
from data import _okx_candles

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)
GOLD = CFG.get("gold", {})
EQUITY = CFG.get("paper_equity_usd", 100)
RISK_PCT = CFG.get("risk_pct", 1.0)
TP_FLOOR_R = GOLD.get("tp_floor_r", 1.5)
SESSIONS = GOLD.get("session_hours_utc", [[7, 11], [12, 16]])


def load_gold_data(limit=6300):
    """PAXGUSDT 15m from OKX (+ 4H resampled). Returns (m15, h4) DataFrames
    with tz-aware UTC index, or (None, None) on failure."""
    rows = _okx_candles("PAXGUSDT", "15m", limit)
    if len(rows) < 500:
        print(f"[gold] OKX returned only {len(rows)} PAXG bars")
        return None, None
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.set_index("t").sort_index()
    m15 = df[["o", "h", "l", "c", "v"]].rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    h4 = m15.resample("4h").agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last",
                                  "volume": "sum"}).dropna()
    print(f"[gold] PAXGUSDT OKX: {len(m15)} x 15m bars "
          f"({m15.index[0]} -> {m15.index[-1]}), {len(h4)} x 4H bars")
    return m15, h4


def _apply_variant(sigs, variant):
    """Transform raw engine signals per variant. Returns new signal list."""
    out = []
    for s in sigs:
        s = dict(s)
        risk = abs(s["entry"] - s["sl"])
        if variant in ("floor15", "floor15_session", "fixed15"):
            if s["model"] == "flip" or variant == "fixed15":
                s["tp"] = s["entry"] + s["d"] * TP_FLOOR_R * risk
                s["rr"] = TP_FLOOR_R
        if variant == "floor15_session":
            h = pd.Timestamp(s["t"]).hour
            if not any(a <= h < b for a, b in SESSIONS):
                continue
        out.append(s)
    return out


def _equity_curve(ledger, equity=EQUITY, risk_usd=None):
    """Rebuild equity from R multiples in trade order; return (final, maxdd%)."""
    risk_usd = risk_usd if risk_usd is not None else equity * RISK_PCT / 100
    eq, peak, maxdd = equity, equity, 0.0
    for t in ledger:
        eq += t["r"] * risk_usd
        peak = max(peak, eq)
        maxdd = min(maxdd, (eq - peak) / peak)
    return eq, maxdd * 100


def run_variant(variant, m15=None, h4=None):
    """Backtest one variant. Returns a results dict (or ok=False)."""
    if m15 is None or h4 is None:
        m15, h4 = load_gold_data()
    if m15 is None:
        return {"variant": variant, "ok": False, "reason": "no gold data"}
    ctx = E.prepare(m15, h4)
    sigs, diag, armed = E.scan_signals(ctx)
    sigs = _apply_variant(sigs, variant)
    ledger = E.simulate(ctx, sigs)
    n = len(ledger)
    wins = sum(1 for t in ledger if t["r"] > 0)
    tot_r = sum(t["r"] for t in ledger)
    gp = sum(t["r"] for t in ledger if t["r"] > 0)
    gl = -sum(t["r"] for t in ledger if t["r"] < 0)
    final_eq, maxdd = _equity_curve(ledger)
    return {
        "variant": variant, "ok": True,
        "period": f"{m15.index[0].date()} -> {m15.index[-1].date()}",
        "bars_15m": len(m15),
        "n": n, "wins": wins, "losses": n - wins,
        "win_rate_pct": round(100 * wins / n, 1) if n else None,
        "total_r": round(tot_r, 2),
        "avg_r": round(tot_r / n, 2) if n else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "max_drawdown_pct": round(maxdd, 2),
        "equity_start_usd": EQUITY,
        "equity_end_usd": round(final_eq, 2),
        "pnl_usd": round(final_eq - EQUITY, 2),
        "signals_found": len(sigs),
        "diag": diag,
        "trades": ledger,
    }


VARIANTS = ["base", "floor15", "floor15_session", "fixed15"]


def load_gold_yfinance():
    """GC=F 15m from yfinance (60d max, ~4500 bars). Real gold futures —
    the cross-check dataset for the 15m entry TF. Note: futures trade
    Sun–Fri (closed weekends); the engine handles the gaps by bar index.
    Returns (m15, h4) with tz-aware UTC index, or (None, None)."""
    try:
        import yfinance as yf
        df = yf.Ticker("GC=F").history(period="60d", interval="15m")
    except Exception as e:
        print(f"[gold] yfinance GC=F failed: {str(e)[:120]}")
        return None, None
    if df is None or len(df) < 500:
        print(f"[gold] yfinance GC=F returned only {0 if df is None else len(df)} bars")
        return None, None
    df = df.tz_convert("UTC")
    m15 = pd.DataFrame({"open": df["Open"].astype(float),
                        "high": df["High"].astype(float),
                        "low": df["Low"].astype(float),
                        "close": df["Close"].astype(float),
                        "volume": df["Volume"].astype(float)})
    m15.index = pd.to_datetime(m15.index).tz_convert("UTC")
    h4 = m15.resample("4h").agg({"open": "first", "high": "max",
                                 "low": "min", "close": "last",
                                 "volume": "sum"}).dropna()
    print(f"[gold] GC=F yfinance: {len(m15)} x 15m bars "
          f"({m15.index[0]} -> {m15.index[-1]}), {len(h4)} x 4H bars")
    return m15, h4


def load_paxg_1h(limit=6300):
    """PAXGUSDT 1h from OKX (1440 bars ~= 60 days — OKX caps PAXG
    history at 1440 candles per granularity). Used for the 1H-ENTRY
    variant: the engine runs with 1h bars as its entry TF. This is a
    DIFFERENT entry TF from the 15m playbook, measured separately and
    labelled _1h. Returns (h1_as_m15, h4) or (None, None)."""
    rows = _okx_candles("PAXGUSDT", "1h", limit)
    if len(rows) < 300:
        print(f"[gold] OKX returned only {len(rows)} PAXG 1h bars")
        return None, None
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.set_index("t").sort_index()
    m1 = df[["o", "h", "l", "c", "v"]].rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    h4 = m1.resample("4h").agg({"open": "first", "high": "max",
                                "low": "min", "close": "last",
                                "volume": "sum"}).dropna()
    print(f"[gold] PAXGUSDT OKX 1h: {len(m1)} x 1h bars "
          f"({m1.index[0]} -> {m1.index[-1]}), {len(h4)} x 4H bars")
    return m1, h4


def load_gc_1h():
    """GC=F 1h from yfinance (730d max — the LONG window). Used for the
    1H-ENTRY variant on real gold futures: the engine runs with 1h bars
    as its entry TF. Labelled _1h. Returns (h1_as_m15, h4) or (None, None)."""
    try:
        import yfinance as yf
        df = yf.Ticker("GC=F").history(period="730d", interval="1h")
    except Exception as e:
        print(f"[gold] yfinance GC=F 1h failed: {str(e)[:120]}")
        return None, None
    if df is None or len(df) < 500:
        print(f"[gold] yfinance GC=F 1h returned only {0 if df is None else len(df)} bars")
        return None, None
    df = df.tz_convert("UTC")
    m1 = pd.DataFrame({"open": df["Open"].astype(float),
                       "high": df["High"].astype(float),
                       "low": df["Low"].astype(float),
                       "close": df["Close"].astype(float),
                       "volume": df["Volume"].astype(float)})
    m1.index = pd.to_datetime(m1.index).tz_convert("UTC")
    h4 = m1.resample("4h").agg({"open": "first", "high": "max",
                                "low": "min", "close": "last",
                                "volume": "sum"}).dropna()
    print(f"[gold] GC=F yfinance 1h: {len(m1)} x 1h bars "
          f"({m1.index[0]} -> {m1.index[-1]}), {len(h4)} x 4H bars")
    return m1, h4


def run_all():
    datasets = []
    m15, h4 = load_gold_yfinance()
    if m15 is not None:
        datasets.append(("gc15m", m15, h4))
    m1, h1 = load_paxg_1h()
    if m1 is not None:
        datasets.append(("paxg1h", m1, h1))
    mg, hg = load_gc_1h()
    if mg is not None:
        datasets.append(("gc1h", mg, hg))
    if not datasets:
        print("[gold] NO DATA — backtest cannot run")
        return {}
    results = {}
    for tag, m15, h4 in datasets:
        for v in VARIANTS:
            key = f"{v}_{tag}"
            print(f"[gold] variant {key} ...", flush=True)
            r = run_variant(v, m15, h4)
            r["variant"] = key
            r["dataset"] = tag
            results[key] = r
            if r.get("ok"):
                print(f"  {key}: n={r['n']} win={r['win_rate_pct']}% "
                      f"total {r['total_r']}R avg {r['avg_r']}R PF {r['profit_factor']} "
                      f"DD {r['max_drawdown_pct']}% PnL ${r['pnl_usd']} "
                      f"({r['period']})")
    with open(os.path.join(_BASE, "data", "gold_backtest.json"), "w") as f:
        json.dump({v: {k: val for k, val in r.items() if k != "trades"}
                   for v, r in results.items()}, f, indent=2)
    return results


def live_signal():
    """Current gold signal state for the board: latest signals + armed
    state machine + 4H trend. Returns a dict (never raises)."""
    try:
        m15, h4 = load_gold_data(limit=1500)
        if m15 is None:
            return {"ok": False, "reason": "no gold data"}
        ctx = E.prepare(m15, h4)
        sigs, diag, armed = E.scan_signals(ctx)
        last_close = float(m15["close"].iloc[-1])
        # prepare() makes the 4H index tz-naive; localise the lookup time too.
        last_t = m15.index[-1]
        if getattr(last_t, "tzinfo", None) is not None:
            last_t = last_t.tz_localize(None)
        j4 = ctx["h4_pos"](last_t)
        trend4 = int(ctx["trend"][j4]) if j4 < len(ctx["trend"]) else 0
        recent = [s for s in sigs if s["i"] >= len(m15) - 96]
        return {"ok": True, "symbol": "PAXGUSDT (XAUUSD proxy)",
                "last_close": round(last_close, 2),
                "trend_4h": ("Bullish" if trend4 == 1 else
                             "Bearish" if trend4 == -1 else "No BOS bias"),
                "signals_last_24h": recent,
                "armed": armed,
                "asof": str(m15.index[-1])}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


if __name__ == "__main__":
    if "--live" in sys.argv:
        print(json.dumps(live_signal(), indent=2, default=str))
    else:
        run_all()
