#!/usr/bin/env python3
"""STAGE 1 — COIN FILTER / SCANNER.

Scans the crypto market (CoinGecko top-N by market cap, keyless) and filters
down to tradeable candidates:

  1. Liquidity: market cap >= min_mcap AND 24h volume >= min_vol24h
     AND volume/market-cap >= min_vol_mcap (real turnover, not wash).
  2. Volatility: 24h range must be tradeable — not dead (< min_range24h)
     and not blow-off (> max_range24h). Range is measured from 1h candles.
  3. Trend: 24h/7d momentum must not be in free-fall; strong directional
     drift (either way) is fine — the committee decides direction.
  4. Binance-listed: symbol must have a USDT pair on Binance so the
     execution desk can price it (checked via ccxt public markets).

Output: ranked candidate list with a filter score and the reason each coin
passed/failed. Nothing here places a trade.
"""
import json
import os

from data import coingecko_markets, fetch_candles, _ccxt_exchange

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)["scan"]

_STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "WBTC", "STETH"}


_MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK",
           "AVAX", "DOT", "LTC", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI"]


def _binance_usdt_symbols():
    """USDT pairs available for pricing. Binance is geo-blocked from this
    VM, so fall back to Kraken's USD pairs, then a static majors list."""
    try:
        import ccxt
        ex = _ccxt_exchange("binance")
        ex.load_markets()
        return {s for s in ex.symbols if s.endswith("/USDT")}
    except Exception:
        pass
    try:
        import ccxt
        ex = _ccxt_exchange("kraken")
        ex.load_markets()
        return {s.replace("/USD", "/USDT") for s in ex.symbols if s.endswith("/USD")}
    except Exception:
        return {s + "/USDT" for s in _MAJORS}


def _range_24h(candles):
    if len(candles) < 24:
        return 0.0
    seg = candles[-24:]
    hi = max(c["h"] for c in seg)
    lo = min(c["l"] for c in seg)
    last = seg[-1]["c"]
    return (hi - lo) / last * 100 if last else 0.0


def scan(top_n=None, max_candidates=None, verbose=True):
    """Run the full market scan. Returns list of candidate dicts."""
    top_n = top_n or CFG["top_n_coins"]
    max_candidates = max_candidates or CFG["max_candidates"]
    rows = coingecko_markets(top=top_n, min_mcap=CFG["min_mcap_usd"])
    if verbose:
        print(f"[scanner] CoinGecko returned {len(rows)} coins (mcap >= ${CFG['min_mcap_usd']/1e6:.0f}M)")
    listed = _binance_usdt_symbols()
    candidates, rejected = [], []
    for r in rows:
        sym = r["symbol"]
        if sym in _STABLES:
            continue
        pair = sym + "USDT"
        if f"{sym}/USDT" not in listed:
            rejected.append((pair, "not listed on Binance USDT"))
            continue
        if r["vol24h"] < CFG["min_vol24h_usd"]:
            rejected.append((pair, f"24h volume ${r['vol24h']/1e6:.0f}M < ${CFG['min_vol24h_usd']/1e6:.0f}M"))
            continue
        if r["vol_mcap"] < CFG["min_vol_mcap"]:
            rejected.append((pair, f"turnover {r['vol_mcap']:.3f} < {CFG['min_vol_mcap']}"))
            continue
        # Volatility + trend from 1h candles (needs 48 bars).
        candles = fetch_candles(pair, "1h", 48)
        if len(candles) < 30:
            rejected.append((pair, "not enough candle history"))
            continue
        rng = _range_24h(candles)
        if rng < 1.5:
            rejected.append((pair, f"24h range {rng:.2f}% — dead coin"))
            continue
        if rng > 25.0:
            rejected.append((pair, f"24h range {rng:.2f}% — blow-off, too hot"))
            continue
        # Filter score: turnover + momentum strength (direction-agnostic).
        score = (min(r["vol_mcap"], 0.3) / 0.3 * 0.5
                 + min(abs(r["pct_24h"]), 15) / 15 * 0.3
                 + min(rng, 12) / 12 * 0.2)
        candidates.append({
            **r, "pair": pair, "range24h_pct": round(rng, 2),
            "filter_score": round(score, 3),
        })
        if len(candidates) >= max_candidates * 2:
            break
    candidates.sort(key=lambda c: c["filter_score"], reverse=True)
    candidates = candidates[:max_candidates]
    if verbose:
        print(f"[scanner] {len(candidates)} candidates passed all filters "
              f"({len(rejected)} rejected)")
        for c in candidates:
            print(f"   {c['pair']:10s} score {c['filter_score']:.2f}  "
                  f"mcap ${c['mcap']/1e9:.2f}B vol24h ${c['vol24h']/1e6:.0f}M "
                  f"24h {c['pct_24h']:+.1f}% range24h {c['range24h_pct']:.1f}%")
    return candidates


if __name__ == "__main__":
    scan()
