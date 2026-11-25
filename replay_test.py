#!/usr/bin/env python3
"""REPLAY TEST — prove the live trade path end-to-end.

Finds the first historical bar where the SMC gates fire (same stage-1
logic as the backtest), then replays the FULL live pipeline
(fund.analyze_symbol) as-of that bar — real Kronos forecast with the
correct historical end_time, committee, risk sizing, paper ledger.
The market is in a selloff right now so the live run says HOLD; this
replay shows the BUY/SELL + paper-order path actually works.

Usage: python replay_test.py [SYMBOL]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from data import fetch_candles
from smc_analysts import all_analysts
import committee as C
import fund
import json

GATES = C.GATES


def find_first_setup(symbol):
    candles = fetch_candles(symbol, "1h", 1700)
    for i in range(260, len(candles) - 50, 4):
        window = candles[:i + 1]
        closes = [c["c"] for c in window]
        an = all_analysts(window)
        w = {"Trend": 1.0, "Liquidity": 1.0, "Structure": 1.0, "Momentum": 0.75}
        tw = sum(w[a["analyst"]] for a in an)
        ws = sum(w[a["analyst"]] * a["score"] for a in an) / tw
        bulls = sum(1 for a in an if a["stance"] == "bull")
        bears = sum(1 for a in an if a["stance"] == "bear")
        direction = "long" if ws > 0 else "short" if ws < 0 else None
        agree = bulls if direction == "long" else bears if direction == "short" else 0
        if not (direction and abs(ws) >= GATES["committee_min_score"] and agree >= 3):
            continue
        ok3, _ = C._structure_ok(window, direction)
        swept = C._opposing_sweep(window, direction)
        seg = window[-24:]
        rng = (max(c["h"] for c in seg) - min(c["l"] for c in seg)) / closes[-1] * 100
        ok6, _ = C._trend_ok(window, direction)
        # G7 long-momentum (longs only) — mirror the live committee gate so
        # the replayed setup actually passes the full live pipeline.
        ok7 = True
        if direction == "long":
            ok7, _ = C._long_momentum_ok(window)
        # G8 short-momentum (shorts only), G10 short-RSI (altcoins only),
        # G11 long-RSI (longs only) — same mirrors.
        ok8 = ok10 = ok11 = True
        if direction == "short":
            ok8, _ = C._short_momentum_ok(window)
            if symbol not in C.MAJORS:
                ok10, _ = C._short_rsi_ok(window)
        if direction == "long":
            ok11, _ = C._long_rsi_ok(window)
        # G9 BTC regime (altcoins only) — BTC candles aligned to this bar.
        ok9 = True
        if symbol not in C.MAJORS:
            _btc = fetch_candles("BTCUSDT", "1h", 1700)
            _bt = [c for c in _btc if c["t"] <= candles[i]["t"]]
            ok9, _ = C._btc_regime_ok(_bt)
        if ok3 and not swept and GATES["min_range24h_pct"] <= rng <= GATES["max_range24h_pct"] and ok6 and ok7 and ok8 and ok9 and ok10 and ok11:
            return window, candles[i]["t"], direction
    return None, None, None


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    window, bar_t, direction = find_first_setup(symbol)
    if window is None:
        print(f"no SMC setup found for {symbol} in recent history")
        sys.exit(1)
    print(f"[replay] first SMC setup for {symbol}: {direction} at bar "
          f"{pd.Timestamp(bar_t, unit='s', tz='UTC')}")
    end_time = pd.Timestamp(bar_t, unit="s", tz="UTC")
    d = fund.analyze_symbol(symbol, use_llm=False, n_paths=10,
                            candles=window, end_time=end_time)
    print("\nREPLAY RESULT:", d["signal"], "| plan:", json.dumps(d.get("plan")))
    import ledger
    print("LEDGER:", json.dumps(ledger.stats()))
