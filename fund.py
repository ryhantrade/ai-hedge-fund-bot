#!/usr/bin/env python3
"""FUND.PY — the Extraordinary AI Agent orchestrator (A-to-Z pipeline).

  STAGE 1  SCAN     CoinGecko top coins -> liquidity/volatility/trend filters
  STAGE 2  RESEARCH per-coin bundle (structure, supply, ATH, categories)
  STAGE 3  ANALYSIS Kronos forecast + SMC analysts + committee gates
                      (+ TradingAgents LLM debate if GROQ_API_KEY is set)
  STAGE 4  TRADE    1%-risk sizing, SL/TP, PAPER ledger only (no real money)
  STAGE 5  REPORT   signal, gates, paper order, running win rate

Usage:
  python fund.py --scan                    full pipeline over scanned coins
  python fund.py --symbol BTCUSDT          single-symbol pipeline
  python fund.py --symbol BTCUSDT --no-llm skip the LLM seat explicitly
  python fund.py --backtest                historical win-rate test
  python fund.py --ledger                  show paper ledger stats

PAPER TRADING ONLY. No broker keys, no live orders, no Telegram.
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import fetch_candles
import scanner
import research
import committee as C
import risk
import ledger
from kronos_forecast import forecast as kronos_forecast

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)

# Effective signal mode of the most recent run_scan() cycle, for the
# board to display honestly. "deposit" = a deposit signal drove the
# cycle; "smc-only (deposit NO_DATA)" = deposit data missing, SMC
# fallback ran; "smc" = plain SMC mode.
LAST_SCAN_MODE = "deposit"


def analyze_symbol(pair, use_llm=True, n_paths=10, verbose=True,
                   candles=None, end_time=None, deposit=None,
                   require_direction=None):
    """Run stages 2-4 for one symbol. Returns the decision dict.
    candles/end_time: optional replay inputs (historical window ending at
    end_time); when omitted, live candles ending now are fetched."""
    if verbose:
        print(f"\n===== {pair} =====")
    if candles is None:
        candles = fetch_candles(pair, CFG["timeframe"], CFG["lookback_bars"])
    if len(candles) < 210:
        return {"symbol": pair, "signal": "HOLD", "ok": False,
                "reason": f"only {len(candles)} candles"}
    closes = [c["c"] for c in candles]
    if verbose:
        print(f"[research] {len(candles)} x {CFG['timeframe']} candles, "
              f"last ${closes[-1]:,.2f}")
    fc = kronos_forecast(closes, n_paths=n_paths, pred_len=12, end_time=end_time)
    if verbose:
        if fc.get("available"):
            print(f"[kronos] p_up {fc['p_up']:.2f}  exp move {fc['exp_move_pct']:+.2f}% "
                  f"({fc['took_sec']}s, {fc['n_paths']} paths)")
        else:
            print(f"[kronos] UNAVAILABLE — {fc.get('reason')}")
    # G9 BTC-regime gate (altcoins only): fetch BTC candles aligned to
    # the same last bar (for replays this slices to the replay end bar).
    btc_candles = None
    if pair.upper() not in C.MAJORS:
        try:
            _btc = fetch_candles("BTCUSDT", CFG["timeframe"], CFG["lookback_bars"])
            _last_t = candles[-1]["t"]
            btc_candles = [c for c in _btc if c["t"] <= _last_t]
        except Exception as e:
            print(f"[btc regime] could not fetch BTC candles: {e} — G9 fails closed")
    decision = C.decide(pair, candles, fc, use_llm=use_llm, btc_candles=btc_candles)
    if verbose:
        print(f"[committee] score {decision['weighted_score']:+.2f}  "
              f"votes bull {decision['bull_votes']} / bear {decision['bear_votes']} "
              f"of {decision['n_voters']}")
        for v in decision["votes"]:
            print(f"   {v['analyst']:18s} {v['stance']:7s} score {v['score']:+.2f}  "
                  f"{'; '.join(v['reasons'][:2])}")
        if decision["failed_gates"]:
            print("[gates] FAILED:")
            for g in decision["failed_gates"]:
                print(f"   ✗ {g}")
        else:
            print("[gates] ALL PASSED ✓")
        for w in decision.get("warnings", []):
            print(f"[warning] ⚠ {w}")
    # Deposit-flow confirmation: the committee must agree with the
    # deposit-flow direction, otherwise HOLD (no trade is recorded).
    if require_direction and decision.get("direction") != require_direction:
        if verbose:
            print(f"[confirmation] committee {decision.get('direction')} != "
                  f"deposit {require_direction} — HOLD")
        decision["signal"] = "HOLD"
    plan = None
    if decision["signal"] in ("BUY", "SELL"):
        direction = "long" if decision["signal"] == "BUY" else "short"
        plan = risk.plan(direction, candles, CFG["paper_equity_usd"], CFG["risk_pct"])
        if deposit:
            # TP comes from the deposit-flow calculation ONLY (user spec
            # 2026-11-24): TP varies per trade, NO 1.5R floor here. The
            # 1.5R floor stays for the old SMC strategy/backtest only.
            # SL goes BELOW THE SWEEP (user decision 2026-11-24; settles
            # the 2026-10-17 SL-at-sweep vs SL-below-OB question): the
            # sweep is the swing low/high that got swept, and the SL
            # sits beyond it (risk.sweep_below_sl). If no recent sweep
            # exists, the structural SL from risk.plan stands and the
            # fallback is recorded on the trade.
            entry = plan["entry"]
            tp_dist = entry * deposit["expected_move_pct"] / 100.0
            plan["tp"] = round(entry + tp_dist if direction == "long"
                               else entry - tp_dist, 4)
            sl, swept, found = risk.sweep_below_sl(direction, candles, entry)
            if found:
                plan["sl"] = sl
                plan["sl_rule"] = "sweep"
                plan["swept_level"] = swept
            else:
                plan["sl_rule"] = "structural (no recent sweep)"
            risk_unit = abs(entry - plan["sl"])
            plan["risk_per_unit"] = round(risk_unit, 4)
            plan["risk_usd"] = round(CFG["paper_equity_usd"] * CFG["risk_pct"] / 100, 2)
            plan["qty"] = round(plan["risk_usd"] / risk_unit, 6)
            plan["notional_usd"] = round(plan["qty"] * entry, 2)
            plan["rr"] = round(tp_dist / risk_unit, 2)
        trade = {"id": "paper-" + uuid.uuid4().hex[:10], "symbol": pair,
                 "direction": direction, "status": "open", **plan,
                 "signal": decision["signal"],
                 "score": decision["weighted_score"],
                 "kronos_p_up": decision["kronos"].get("p_up"),
                 "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if deposit:
            trade.update({
                "deposit_expected_move_pct": deposit["expected_move_pct"],
                "deposit_source": deposit.get("source"),
                "deposit_direction": deposit.get("direction"),
            })
        ledger.record(trade)
        if verbose:
            print(f"[paper trade] {direction.upper()} {plan['qty']} {pair} @ "
                  f"${plan['entry']:,.2f}  SL ${plan['sl']:,.2f}  TP ${plan['tp']:,.2f}  "
                  f"risk ${plan['risk_usd']:.2f} ({plan['risk_pct']}%)  id {trade['id']}")
    else:
        if verbose:
            print("[paper trade] no trade — signal HOLD (no trade beats a low-probability trade)")
    decision["plan"] = plan
    decision["ok"] = True
    return decision


def close_open_trades():
    """STAGE 4c — CLOSE CHECK (live SL/TP execution for paper trades).

    The ledger had close_trade() but nothing ever called it live, so open
    paper trades never closed. Runs at the start of every scan cycle: for
    each open trade, fetch the latest 1h candles and compare the latest
    close against SL/TP. Close at SL (-1R) or TP (+1.5R) via
    ledger.close_trade(); leave open otherwise.
    Limitation (honest): only the latest 1h CLOSE is checked, not
    intrabar wicks — a wick that touched SL/TP mid-bar but closed back
    inside is caught only when a later close crosses. With a 4h cycle
    this is acceptable for paper; it never overstates fills.
    """
    closed = []
    for t in ledger.open_trades():
        try:
            candles = fetch_candles(t["symbol"], CFG["timeframe"], 2)
        except Exception as e:
            print(f"[close-check] {t['symbol']} fetch failed: {e}")
            continue
        if not candles:
            print(f"[close-check] {t['symbol']}: no candles, skipping")
            continue
        last = candles[-1]["c"]
        done = None
        if t["direction"] == "long":
            if last <= t["sl"]:
                done = ledger.close_trade(t["id"], t["sl"], "SL")
            elif last >= t["tp"]:
                done = ledger.close_trade(t["id"], t["tp"], "TP")
        else:
            if last >= t["sl"]:
                done = ledger.close_trade(t["id"], t["sl"], "SL")
            elif last <= t["tp"]:
                done = ledger.close_trade(t["id"], t["tp"], "TP")
        if done:
            print(f"[close-check] CLOSED {done['symbol']} {done['direction']} "
                  f"@ {done['exit_price']} ({done['exit_reason']}, "
                  f"{done['r_multiple']:+.2f}R)")
            closed.append(done)
    if not closed:
        print("[close-check] no open trades hit SL/TP")
    return closed


def run_scan(use_llm=True):
    print("=" * 64)
    print("AI HEDGE FUND — full pipeline: scan -> research -> analysis -> trade")
    print("PAPER ONLY. No real money, no broker keys, no live orders.")
    print("=" * 64)
    # STAGE 4c — close check first: open paper trades hit SL/TP?
    close_open_trades()
    # STAGE 0 — deposit-flow primary signal (user spec 2026-11-24).
    # Fallback (user decision 2026-11-24): no paid deposit API, so when
    # the deposit check returns NO_DATA the SMC scan path runs instead
    # of holding forever. Deposit data existing but producing no signal
    # (e.g. BELOW_THRESHOLD) still HOLDs; a deposit signal uses the
    # deposit path unchanged (TP from the calc, SL below the sweep).
    global LAST_SCAN_MODE
    mode = os.environ.get("FUND_SIGNAL_MODE",
                          CFG.get("deposit_flow", {}).get("mode", "deposit"))
    if mode == "deposit":
        import deposit_flow
        dep = deposit_flow.check()
        print(f"[deposit-flow] {dep['status']}: {dep['reason']}")
        if dep.get("signal"):
            LAST_SCAN_MODE = "deposit"
            d = analyze_symbol(dep["symbol"], use_llm=use_llm,
                               deposit=dep, require_direction=dep["direction"])
            results = [d]
            n_trades = sum(1 for r in results if r.get("signal") in ("BUY", "SELL"))
            print("\n" + "=" * 64)
            print(f"SCAN DONE: deposit-flow signal on {dep['symbol']}, "
                  f"{n_trades} paper trades opened")
            print("LEDGER:", json.dumps(ledger.stats()))
            return results
        if dep.get("status") == "NO_DATA":
            LAST_SCAN_MODE = "smc-only (deposit NO_DATA)"
            print("[deposit-flow] deposit NO_DATA — SMC-only this cycle")
            return _run_smc_scan(use_llm)
        print("[deposit-flow] no deposit-driven signal this cycle — holding")
        print("LEDGER:", json.dumps(ledger.stats()))
        return []
    LAST_SCAN_MODE = "smc"
    return _run_smc_scan(use_llm)


def _run_smc_scan(use_llm=True):
    """STAGE 1-4 — the SMC scan path (scan -> research -> committee -> trade).

    Runs in plain SMC mode and as the deposit-mode fallback when the
    deposit check returns NO_DATA (user decision 2026-11-24: no paid
    deposit API, proceed with SMC signals only for now). TP 1.5R,
    1% risk on the $100 paper equity — unchanged.
    """
    cands = scanner.scan()
    results = []
    for c in cands:
        pair = c["pair"]
        bundle = research.research(c)
        print(f"[research] {research.summarize(bundle)}")
        if not bundle.get("ok"):
            continue
        d = analyze_symbol(pair, use_llm=use_llm)
        d["candidate"] = {k: v for k, v in c.items() if k != "candles"}
        results.append(d)
    n_trades = sum(1 for d in results if d.get("signal") in ("BUY", "SELL"))
    print("\n" + "=" * 64)
    print(f"SCAN DONE: {len(results)} coins analyzed, {n_trades} paper trades opened")
    print("LEDGER:", json.dumps(ledger.stats()))
    return results


def main():
    ap = argparse.ArgumentParser(description="AI Hedge Fund — paper-only A-to-Z agent")
    ap.add_argument("--scan", action="store_true", help="full pipeline over scanned coins")
    ap.add_argument("--symbol", help="single symbol, e.g. BTCUSDT")
    ap.add_argument("--no-llm", action="store_true", help="skip the TradingAgents LLM seat")
    ap.add_argument("--backtest", action="store_true", help="historical win-rate test")
    ap.add_argument("--ledger", action="store_true", help="show paper ledger stats")
    ap.add_argument("--close-check", action="store_true",
                    help="close open paper trades at SL/TP, then exit")
    ap.add_argument("--paths", type=int, default=10, help="Kronos sample paths")
    args = ap.parse_args()
    use_llm = not args.no_llm
    if args.ledger:
        print(json.dumps(ledger.stats(), indent=2))
        for t in ledger.all_trades()[-10:]:
            print(t)
        return
    if args.close_check:
        closed = close_open_trades()
        print("LEDGER:", json.dumps(ledger.stats()))
        return
    if args.backtest:
        import backtest
        backtest.run()
        return
    if args.scan:
        run_scan(use_llm=use_llm)
        return
    if args.symbol:
        d = analyze_symbol(args.symbol.upper(), use_llm=use_llm, n_paths=args.paths)
        print("\nLEDGER:", json.dumps(ledger.stats()))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
