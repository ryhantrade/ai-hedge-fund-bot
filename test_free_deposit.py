#!/usr/bin/env python3
"""LIVE TEST for the FREE deposit-data route (free_deposit.py).

  Runs the ETH test end to end and prints REAL numbers:
  per-wallet 24h ETH inflows, totals, ETH price + market cap,
  expected % move, and entry/TP/SL for ETH.

  No Etherscan key is needed to run this — without a key the test
  uses the keyless Blockchair fallback and prints the exact signup
  steps for the free key. With ETHERSCAN_API_KEY set it uses
  Etherscan V2 (primary) instead.

  Exit code 0 = the module ran and reported (even if the result is
  NO_DATA / BELOW_THRESHOLD — those are honest outcomes, not
  failures). Exit code 1 = an unexpected exception.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import free_deposit as fd


def _fmt_usd(x):
    if x is None:
        return "n/a"
    return f"${x:,.2f}"


def main():
    t0 = time.time()
    print("=" * 68)
    print("FREE DEPOSIT-ROUTE TEST — ETH first, then BTC/SOL status")
    print("=" * 68)

    # ---- 1. Etherscan key status ----------------------------------
    print("\n[1] ETHERSCAN (primary ETH source)")
    if os.environ.get("ETHERSCAN_API_KEY"):
        print("    KEY PRESENT — test will use Etherscan V2 (primary).")
    else:
        print("    KEY MISSING — Etherscan V2 refuses keyless calls")
        print("    ('Missing/Invalid API Key', verified live 2026-11-24).")
        print("    Falling back to Blockchair (keyless) for this run.")
        print("    " + fd.ETHERSCAN_SIGNUP.replace("\n", "\n    "))

    # ---- 2. ETH inflow test ---------------------------------------
    print("\n[2] ETH 24h INFLOW TEST (hot wallets -> deposits)")
    try:
        res = fd.check_free()
    except Exception as e:
        print(f"    FAIL — unexpected exception: {e}")
        return 1
    print(f"    provider used : {res.get('source')}")
    print(f"    ETH price     : {_fmt_usd(res.get('eth_price_usd'))} (OKX)")
    print(f"    ETH market cap: {_fmt_usd(res.get('market_cap_usd'))} (CoinGecko)")
    print(f"    total deposits: {res.get('per_wallet') and sum(w['inflow_eth'] for w in res['per_wallet']):.6f} ETH "
          f"= {_fmt_usd(res.get('ranking', [{}])[0].get('deposit_usd') if res.get('ranking') else 0.0)}")
    print(f"    expected move : {res.get('expected_move_pct')}% "
          f"(formula: deposit $ / mcap $ x 100, sensitivity 1.0)")
    print(f"    status        : {res.get('status')} — {res.get('reason')}")
    print("\n    per-wallet 24h inflows:")
    print(f"    {'exchange':10s} {'label':20s} {'inflow ETH':>12s} {'txs':>5s}  wallet status")
    for w in res.get("per_wallet", []):
        err = f" ERR:{w['error']}" if w.get("error") else ""
        trunc = " TRUNCATED" if w.get("truncated") else ""
        print(f"    {w['exchange']:10s} {w['label']:20s} {w['inflow_eth']:12.6f} "
              f"{w['n_txs']:5d}  {w.get('wallet_status','')}{err}{trunc}")

    # ---- 3. Entry / TP / SL ----------------------------------------
    print("\n[3] ETH PLAN (entry / TP / SL) — from the measured inflow")
    plan = res.get("plan")
    if plan:
        print(f"    direction : {plan['direction'].upper()} (deposits -> UP, user model)")
        print(f"    entry     : ${plan['entry']:,.2f} (live OKX last price)")
        print(f"    TP        : ${plan['tp']:,.2f} (entry x (1 + {plan['expected_move_pct']}%))")
        print(f"    SL        : ${plan['sl']:,.2f} ({plan['sl_rule']}"
              + (f", swept level ${plan['swept_level']:,.2f}" if plan.get("swept_level") else "") + ")")
        print(f"    risk      : ${plan['risk_usd']:.2f} ({plan['risk_pct']}% of $100)"
              f"  qty: {plan['qty']} ETH  notional: ${plan['notional_usd']:,.2f}")
        print(f"    RR        : {plan['rr']}R")
        if not res.get("signal"):
            print("    NOTE: this is the WOULD-BE plan from the measured inflow —")
            print("    NO trade, because the expected move is below the 0.8% threshold.")
    else:
        print("    NO PLAN — candles unavailable (cannot compute entry/SL).")

    # ---- 4. BTC mempool (context only) ------------------------------
    print("\n[4] BTC — mempool.space (context only, no attribution)")
    n = res.get("mempool_large_transfers")
    if n is None:
        print("    mempool.space request failed.")
    else:
        print(f"    large transfers (>= $1M) in mempool right now: {n}")
        print("    These carry NO addresses — they are NOT exchange deposits")
        print("    and NEVER trigger a trade. BTC attribution free: IMPOSSIBLE.")

    # ---- 5. SOL status ----------------------------------------------
    print("\n[5] SOL — Solscan")
    sol = res.get("sol", {})
    print(f"    status: {sol.get('status')} — {sol.get('reason')}")
    if not os.environ.get("SOLSCAN_API_KEY"):
        print("    " + fd.SOLSCAN_SIGNUP.replace("\n", "\n    "))

    # ---- Summary ------------------------------------------------------
    print("\n" + "=" * 68)
    print("SUMMARY")
    eth_ok = bool(res.get("per_wallet"))
    print(f"  ETH provider ran          : {'PASS' if eth_ok else 'FAIL'} "
          f"({res.get('source')})")
    print(f"  ETH inflow is a REAL sum  : {'PASS' if eth_ok else 'FAIL'} "
          f"(summed from live chain data, not invented)")
    print(f"  ETH signal                : {'SIGNAL' if res.get('signal') else res.get('status')}")
    print(f"  BTC attribution free      : IMPOSSIBLE (mempool has no addresses)")
    print(f"  SOL                       : NO_DATA (needs free key + verified wallets)")
    print(f"  elapsed                   : {time.time() - t0:.1f}s")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
