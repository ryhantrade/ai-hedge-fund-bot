#!/usr/bin/env python3
"""STAGE 3a — SMC ANALYSTS (rule-based, no LLM needed).

Four independent analysts read the same candles from the user's
Market-Mechanics (SMC) playbook. Each returns {stance, score, confidence,
reasons} where score is in [-1, +1] (+1 = strongly bullish).

  Trend      — EMA50/EMA200 structure + BOS + premium/discount position.
  Liquidity  — sweep of prior swing high/low + reclaim (displacement).
  Structure  — order blocks / fair-value gaps: price tapping a bullish OB
               or FVG in a discount zone (long) / bearish OB-FVG in premium.
  Momentum   — RSI-style pressure + 24h realised move vs ATR.

These are the fallback analysts used when no LLM key is present. When a
Groq key IS present, committee.py runs the same questions through
TradingAgents' LLM analysts and blends the two votes.
"""
import math


def _ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, n + 1):
        d = closes[-i] - closes[-i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / n
    al = sum(losses) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _atr(candles, n=14):
    seg = candles[-n:]
    return sum(c["h"] - c["l"] for c in seg) / len(seg)


def trend_analyst(candles):
    closes = [c["c"] for c in candles]
    if len(closes) < 210:
        return {"analyst": "Trend", "stance": "neutral", "score": 0.0,
                "confidence": 0.3, "reasons": ["not enough history for 4H bias"]}
    e50 = _ema(closes[-60:], 50)
    e200 = _ema(closes, 200)
    last = closes[-1]
    hi, lo = max(closes[-30:]), min(closes[-30:])
    score, reasons = 0.0, []
    # Partial EMA credit (not all-or-nothing): price vs EMA50 and
    # EMA50 vs EMA200 each contribute, so transitions still score.
    if last > e50:
        score += 0.25
        reasons.append("price above EMA50")
    else:
        score -= 0.25
        reasons.append("price below EMA50")
    if e50 > e200:
        score += 0.25
        reasons.append("EMA50 above EMA200 — uptrend structure")
    else:
        score -= 0.25
        reasons.append("EMA50 below EMA200 — downtrend structure")
    if last > hi * 1.001:
        score += 0.3
        reasons.append("bullish BOS — closed above prior 30-bar swing high")
    elif last < lo * 0.999:
        score -= 0.3
        reasons.append("bearish BOS — closed below prior 30-bar swing low")
    pos = (last - lo) / (hi - lo + 1e-9)
    if pos > 0.7:
        score -= 0.15
        reasons.append("premium zone — longs less attractive")
    elif pos < 0.3:
        score += 0.15
        reasons.append("discount zone — longs attractive")
    score = max(-1.0, min(1.0, score))
    stance = "bull" if score > 0.15 else "bear" if score < -0.15 else "neutral"
    return {"analyst": "Trend", "stance": stance, "score": round(score, 2),
            "confidence": round(min(0.9, abs(score) + 0.3), 2), "reasons": reasons}


def liquidity_analyst(candles):
    if len(candles) < 40:
        return {"analyst": "Liquidity", "stance": "neutral", "score": 0.0,
                "confidence": 0.3, "reasons": ["not enough history"]}
    closes = [c["c"] for c in candles]
    hi, lo = max(closes[:-5][-25:]), min(closes[:-5][-25:])
    score, reasons = 0.0, []
    for c in candles[-5:]:
        swept_high = c["h"] > hi * 1.001 and c["c"] < hi
        swept_low = c["l"] < lo * 0.999 and c["c"] > lo
        if swept_high:
            score -= 0.35
            reasons.append("sell-side sweep: wicked above highs, closed back — bearish")
            break
        if swept_low:
            score += 0.35
            reasons.append("buy-side sweep: wicked below lows, closed back — bullish")
            break
    ranges = [c["h"] - c["l"] for c in candles[-20:]]
    avg = sum(ranges) / len(ranges)
    if ranges[-1] > avg * 1.8:
        reasons.append("displacement candle — institutional footprint")
        score += 0.1 if closes[-1] > closes[-2] else -0.1
    if not reasons:
        reasons.append("no fresh sweep — waiting for liquidity raid")
    score = max(-1.0, min(1.0, score))
    stance = "bull" if score > 0.15 else "bear" if score < -0.15 else "neutral"
    return {"analyst": "Liquidity", "stance": stance, "score": round(score, 2),
            "confidence": round(min(0.85, abs(score) + 0.35), 2), "reasons": reasons}


def structure_analyst(candles):
    """Order-block / FVG: did price just tap a bullish/bearish OB or FVG?"""
    if len(candles) < 60:
        return {"analyst": "Structure", "stance": "neutral", "score": 0.0,
                "confidence": 0.3, "reasons": ["not enough history"]}
    closes = [c["c"] for c in candles]
    last = closes[-1]
    hi, lo = max(closes[-30:]), min(closes[-30:])
    pos = (last - lo) / (hi - lo + 1e-9)
    score, reasons = 0.0, []
    # FVG: 3-bar gap — bar[i-1].high < bar[i+1].low (bullish) or reverse.
    bull_fvg = bear_fvg = False
    for i in range(len(candles) - 6, len(candles) - 1):
        if candles[i - 1]["h"] < candles[i + 1]["l"]:
            bull_fvg = True
        if candles[i - 1]["l"] > candles[i + 1]["h"]:
            bear_fvg = True
    # Bullish OB: last down-close before a strong up displacement.
    bull_ob = bear_ob = False
    for i in range(len(candles) - 12, len(candles) - 1):
        up_move = candles[i + 1]["c"] - candles[i + 1]["o"]
        atr = _atr(candles[:i + 2])
        if up_move > 1.2 * atr and candles[i]["c"] < candles[i]["o"]:
            if abs(last - candles[i]["l"]) / (atr + 1e-9) < 1.5:
                bull_ob = True
        if -up_move > 1.2 * atr and candles[i]["c"] > candles[i]["o"]:
            if abs(last - candles[i]["h"]) / (atr + 1e-9) < 1.5:
                bear_ob = True
    if (bull_fvg or bull_ob) and pos < 0.55:
        score += 0.4
        reasons.append("tapping bullish " + ("FVG" if bull_fvg else "order block")
                       + " in discount — long zone")
    if (bear_fvg or bear_ob) and pos > 0.45:
        score -= 0.4
        reasons.append("tapping bearish " + ("FVG" if bear_fvg else "order block")
                       + " in premium — short zone")
    if not reasons:
        reasons.append("no fresh OB/FVG tap at current price")
    score = max(-1.0, min(1.0, score))
    stance = "bull" if score > 0.15 else "bear" if score < -0.15 else "neutral"
    return {"analyst": "Structure", "stance": stance, "score": round(score, 2),
            "confidence": round(min(0.85, abs(score) + 0.35), 2), "reasons": reasons}


def momentum_analyst(candles):
    closes = [c["c"] for c in candles]
    if len(closes) < 30:
        return {"analyst": "Momentum", "stance": "neutral", "score": 0.0,
                "confidence": 0.3, "reasons": ["not enough history"]}
    rsi = _rsi(closes)
    chg_24h = (closes[-1] / closes[-25] - 1) * 100 if len(closes) >= 25 else 0.0
    atr = _atr(candles)
    score, reasons = 0.0, []
    if rsi > 65:
        score -= 0.2
        reasons.append(f"RSI {rsi:.0f} — overbought, longs stretched")
    elif rsi < 35:
        score += 0.2
        reasons.append(f"RSI {rsi:.0f} — oversold, shorts stretched")
    else:
        reasons.append(f"RSI {rsi:.0f} — neutral zone")
    if chg_24h > 1.0 * (atr / closes[-1] * 100) * 1.5:
        score += 0.25
        reasons.append(f"24h +{chg_24h:.1f}% — momentum with the move")
    elif chg_24h < -1.0 * (atr / closes[-1] * 100) * 1.5:
        score -= 0.25
        reasons.append(f"24h {chg_24h:.1f}% — momentum against longs")
    score = max(-1.0, min(1.0, score))
    stance = "bull" if score > 0.15 else "bear" if score < -0.15 else "neutral"
    return {"analyst": "Momentum", "stance": stance, "score": round(score, 2),
            "confidence": round(min(0.85, abs(score) + 0.35), 2),
            "reasons": reasons, "rsi": round(rsi, 1)}


def all_analysts(candles):
    return [trend_analyst(candles), liquidity_analyst(candles),
            structure_analyst(candles), momentum_analyst(candles)]
