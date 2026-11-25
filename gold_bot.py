#!/usr/bin/env python3
"""GOLD BOT — separate entry point (user decision 2026-11-24 23:27 +06).

  The Gold strategy gets its OWN bot, built only on the Gold strategy —
  NOT the crypto deposit-flow bot. Separate entry point (this file),
  separate config (gold_config.json), separate ledger
  (data/gold_paper_trades.jsonl).

  STATUS: PENDING. The Gold strategy comes from a YouTuber playlist the
  user says he shared; the link has not arrived yet (requested
  2026-11-24 23:27 +06). Until it arrives this bot takes NO trades —
  it exits cleanly with the message below and writes nothing.
  Do NOT assume the strategy is the Brad Goh Market Mechanics
  playlist unless the link says so.

  WHEN THE PLAYLIST ARRIVES (do not start before):
    1. Watch ALL videos, learn the strategy exactly as taught
       (entry, SL, TP, timeframe, filters).
    2. Backtest it on XAUUSD (gold_strategy.py's backtest harness):
       win rate, total R, max drawdown, trade count — with sample size.
       Report the real number; never promise >80% before the backtest.
       If the rules are vague/incomplete, list exactly what is missing
       instead of guessing.
    3. Only then implement the strategy in gold_strategy.py, set
       gold_config.json "strategy" to its name, and wire this runner:
       live signal -> paper trade (1% risk) -> close-check at SL/TP
       against the gold ledger. The independent-search candidate
       (SMC playbook, best backtested 42.9% win, n=7, 2y GC=F 1h)
       stays a fallback candidate, not the default.
"""
import json
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "gold_config.json")) as f:
    CFG = json.load(f)


def main():
    if CFG.get("strategy") in (None, "pending"):
        print("[gold-bot] PENDING — no Gold strategy configured yet.")
        print("[gold-bot] The strategy comes from the user's YouTuber "
              "playlist; the link has not arrived (requested 2026-11-24).")
        print("[gold-bot] No trades taken, nothing written. "
              "This bot starts when the playlist arrives.")
        return 0
    print(f"[gold-bot] strategy '{CFG.get('strategy')}' is not implemented "
          "in this runner yet.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
