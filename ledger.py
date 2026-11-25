#!/usr/bin/env python3
"""STAGE 4b — PAPER LEDGER (no real money, no broker keys, no live orders).

Every paper trade is appended to data/paper_trades.jsonl with entry/SL/TP,
exit, R-multiple and win/loss. `stats()` reports the running win rate —
the number the 80% target is measured against.
"""
import json
import os
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.environ.get("LEDGER_PATH") or os.path.join(_BASE, "data", "paper_trades.jsonl")


def record(trade):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    trade = dict(trade)
    trade.setdefault("recorded_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with open(LEDGER, "a") as f:
        f.write(json.dumps(trade) + "\n")
    return trade


def all_trades():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def open_trades():
    return [t for t in all_trades() if t.get("status") == "open"]


def close_trade(trade_id, exit_price, exit_reason):
    """Close an open trade by id; recomputes R and win/loss. Returns updated trade or None."""
    trades = all_trades()
    updated = None
    for t in trades:
        if t.get("id") == trade_id and t.get("status") == "open":
            entry, sl, tp = t["entry"], t["sl"], t["tp"]
            direction = t["direction"]
            if direction == "long":
                r = (exit_price - entry) / (entry - sl) if entry != sl else 0.0
            else:
                r = (entry - exit_price) / (sl - entry) if sl != entry else 0.0
            t.update({"status": "closed", "exit_price": exit_price,
                      "exit_reason": exit_reason, "r_multiple": round(r, 3),
                      "win": bool(r > 0), "closed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            updated = t
    if updated is not None:
        with open(LEDGER, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")
    return updated


def stats():
    trades = all_trades()
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t.get("win")]
    r_sum = sum(t.get("r_multiple", 0) for t in closed)
    live_closed = [t for t in closed if not t.get("replay")]
    live_wins = [t for t in live_closed if t.get("win")]
    live_r = sum(t.get("r_multiple", 0) for t in live_closed)
    return {
        "total": len(trades),
        "open": len(trades) - len(closed),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
        "total_r": round(r_sum, 2),
        "avg_r": round(r_sum / len(closed), 2) if closed else None,
        "expectancy_r": round(r_sum / len(closed), 2) if closed else None,
        # Live-only (excludes replay-test trades): the number that matters.
        "live_closed": len(live_closed),
        "live_wins": len(live_wins),
        "live_win_rate_pct": round(100 * len(live_wins) / len(live_closed), 1) if live_closed else None,
        "live_total_r": round(live_r, 2),
        "replay_trades": len(trades) - len([t for t in trades if not t.get("replay")]),
    }
