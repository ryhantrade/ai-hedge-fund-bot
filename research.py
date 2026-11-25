#!/usr/bin/env python3
"""STAGE 2 — RESEARCH.

For each filtered coin, gather a research bundle (all keyless sources):
  - CoinGecko market data: mcap rank, supply, ATH distance, 24h mcap change.
  - Candle-derived market structure: trend (EMA50/EMA200), 7d/30d returns,
    realised volatility, distance from recent swing high/low.
  - Categories / narrative (DeFi, L1, meme, ...) from CoinGecko.
  - News/sentiment: NOT available without an API key. This module reports
    that honestly and exposes `news_headlines()` as the plug-in point —
    wire a key (e.g. CryptoPanic, Tavily) later; the pipeline works without it.

Output: a dict the analysis stage consumes. No trading decision here.
"""
import math

from data import coingecko_coin, fetch_candles


def _ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def research(candidate, bars=600):
    """Build the research bundle for one scanner candidate dict."""
    pair = candidate["pair"]
    candles = fetch_candles(pair, "1h", bars)
    if len(candles) < 210:
        return {"pair": pair, "ok": False,
                "reason": f"only {len(candles)} candles, need 210+"}
    closes = [c["c"] for c in candles]
    last = closes[-1]
    e50 = _ema(closes[-60:], 50)
    e200 = _ema(closes, 200)
    hi30 = max(closes[-720:]) if len(closes) >= 720 else max(closes)
    lo30 = min(closes[-720:]) if len(closes) >= 720 else min(closes)
    ret_7d = (last / closes[-169] - 1) * 100 if len(closes) >= 169 else None
    ret_30d = (last / closes[-721] - 1) * 100 if len(closes) >= 721 else None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    vol = (sum(r * r for r in rets[-168:]) / len(rets[-168:])) ** 0.5 * math.sqrt(24) * 100
    detail = coingecko_coin(candidate.get("id") or "")
    bundle = {
        "pair": pair, "ok": True,
        "price": last,
        "ema50": round(e50, 4), "ema200": round(e200, 4),
        "trend": "up" if last > e50 > e200 else "down" if last < e50 < e200 else "mixed",
        "dist_from_30d_high_pct": round((last / hi30 - 1) * 100, 2),
        "dist_from_30d_low_pct": round((last / lo30 - 1) * 100, 2),
        "ret_7d_pct": round(ret_7d, 2) if ret_7d is not None else None,
        "ret_30d_pct": round(ret_30d, 2) if ret_30d is not None else None,
        "realised_vol_24h_pct": round(vol, 2),
        "range24h_pct": candidate.get("range24h_pct"),
        "mcap_rank": candidate.get("mcap_rank"),
        "mcap_usd": candidate.get("mcap"),
        "vol24h_usd": candidate.get("vol24h"),
        "ath_change_pct": candidate.get("ath_change_pct"),
        "categories": (detail.get("categories") or [])[:5],
        "mcap_change_24h_pct": detail.get("mcap_change_24h_pct"),
        "circulating_supply": detail.get("circulating_supply"),
        "max_supply": detail.get("max_supply"),
        "about": (detail.get("description") or "")[:300],
        "news": news_headlines(candidate),
        "candles": candles,
    }
    return bundle


def news_headlines(candidate):
    """Plug-in point: news/sentiment needs an API key (CryptoPanic/Tavily).

    Returns an explicit 'unavailable' marker so downstream stages never
    mistake absence of news for neutral news.
    """
    return {"available": False,
            "reason": "no news API key configured — set NEWS_API_KEY to enable",
            "headlines": []}


def summarize(bundle):
    if not bundle.get("ok"):
        return f"{bundle['pair']}: research failed — {bundle.get('reason')}"
    return (f"{bundle['pair']} ${bundle['price']:,.2f} trend={bundle['trend']} "
            f"7d {bundle['ret_7d_pct']:+.1f}% 30d {bundle['ret_30d_pct'] if bundle['ret_30d_pct'] is not None else '?'}% "
            f"vol24h {bundle['realised_vol_24h_pct']:.1f}% "
            f"vs30d-high {bundle['dist_from_30d_high_pct']:+.1f}% "
            f"ATH {bundle['ath_change_pct']:+.0f}% rank #{bundle['mcap_rank']}")
