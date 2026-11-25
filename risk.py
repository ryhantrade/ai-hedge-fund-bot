#!/usr/bin/env python3
"""STAGE 4a — RISK: 1%-risk position sizing + SL/TP.

  SL: beyond the invalidation swing (last swing high/low), at least
      sl_atr_mult x ATR away, at least min_sl_pct from entry.
  TP: tp_r x risk (1.5R — user constraint: TP must stay >= 1.5R;
      the TP 1.0R variant measured 87.5% / +0.58R but is REJECTED by
      the user. At TP 1.5R with the G7 long-momentum gate the measured
      rate is 100% (6/6) on 1h/60d, +1.02R/trade — see README).
      Win-rate math: breakeven win rate with TP 1.5R / SL 1R is
      1 / (1 + 1.5) = 40%. At the measured 100% (n=6), expectancy is
      +1.02R/trade after timeout dilution (measured).
  Size: risk_usd = equity * risk_pct; qty = risk_usd / |entry - SL|.
"""
import json
import os

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)
TRADE = CFG["trade"]


def atr(candles, n=14):
    seg = candles[-n:]
    return sum(c["h"] - c["l"] for c in seg) / len(seg)


def sweep_below_sl(direction, candles, entry, lookback=24):
    """SL below the sweep (user decision 2026-11-24).

    Settles the open 2026-10-17 question (SL-at-sweep vs SL-below-OB):
    the user chose SL-at-sweep, citing his comparison — 92% win,
    +3.5% on BTC 4H/15m for SL-at-sweep vs 58% / -4.3% for SL-below-OB
    (figures as relayed by the user; the comparison file was not found
    in the workspace — only a "SL below OB" reference in
    backtest/btc_4h15m_liqTP.py — so treat them as user-stated, not
    re-verified here). Same placement the mechanical playbook uses for
    its invalidation: SL beyond the swept level.

    Long: find the most recent 1h sweep — a bar whose low went below
    the prior 12-bar low and closed back above it. SL = sweep low -
    buffer, buffer = max(0.15*ATR14, 0.0004*entry) (same buffer the
    playbook's Shift model uses). Short: mirror image above the
    swept swing high.
    Returns (sl, swept_level, found). Returns (None, None, False) when
    no sweep is found in the window, or when the sweep SL would sit on
    the wrong side of the entry — the caller then falls back to the
    structural SL from plan().
    """
    if len(candles) < 13:
        return None, None, False
    a = atr(candles)
    buf = max(0.15 * a, 0.0004 * entry)
    for i in range(len(candles) - 1, max(0, len(candles) - lookback), -1):
        prev = candles[max(0, i - 12):i]
        if not prev:
            continue
        if direction == "long":
            prev_low = min(c["l"] for c in prev)
            if candles[i]["l"] < prev_low and candles[i]["c"] > prev_low:
                sl = candles[i]["l"] - buf
                if sl < entry:
                    return round(sl, 4), round(candles[i]["l"], 4), True
                return None, None, False
        else:
            prev_high = max(c["h"] for c in prev)
            if candles[i]["h"] > prev_high and candles[i]["c"] < prev_high:
                sl = candles[i]["h"] + buf
                if sl > entry:
                    return round(sl, 4), round(candles[i]["h"], 4), True
                return None, None, False
    return None, None, False


def plan(direction, candles, equity_usd, risk_pct=None):
    """Return the trade plan dict, or None if sizing is impossible."""
    risk_pct = CFG["risk_pct"] if risk_pct is None else risk_pct
    closes = [c["c"] for c in candles]
    entry = closes[-1]
    a = atr(candles)
    if direction == "long":
        swing = min(c["l"] for c in candles[-20:])
        sl = min(entry - a * TRADE["sl_atr_mult"], swing - a * 0.2)
    else:
        swing = max(c["h"] for c in candles[-20:])
        sl = max(entry + a * TRADE["sl_atr_mult"], swing + a * 0.2)
    min_dist = entry * TRADE["min_sl_pct"] / 100
    if abs(entry - sl) < min_dist:
        sl = entry - min_dist if direction == "long" else entry + min_dist
    risk_per_unit = abs(entry - sl)
    risk_usd = equity_usd * risk_pct / 100
    qty = risk_usd / risk_per_unit
    tp = entry + risk_per_unit * TRADE["tp_r"] if direction == "long" \
        else entry - risk_per_unit * TRADE["tp_r"]
    return {
        "direction": direction, "entry": round(entry, 4),
        "sl": round(sl, 4), "tp": round(tp, 4),
        "risk_per_unit": round(risk_per_unit, 4),
        "risk_usd": round(risk_usd, 2), "risk_pct": risk_pct,
        "qty": round(qty, 6), "notional_usd": round(qty * entry, 2),
        "rr": TRADE["tp_r"], "atr": round(a, 4),
    }
