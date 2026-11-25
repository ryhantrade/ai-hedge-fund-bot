#!/usr/bin/env python3
"""STAGE 3c — COMMITTEE + STRICT CONFLUENCE GATES (the 80–99% win-rate design).

The committee combines five votes:
  1-4. Rule-based SMC analysts (Trend, Liquidity, Structure, Momentum)
  5.   Kronos AI forecast verdict
  6.   (optional) TradingAgents LLM debate — only when a Groq key is set
       (GROQ_API_KEY). Without it, the LLM seat abstains and the vote is
       purely rule-based + Kronos.

A trade is allowed ONLY if ALL gates pass (confluence). Otherwise HOLD.
"No trade is better than a low-probability trade."

  G1 KRONOS   (mode "advisory", default) Kronos votes and is reported, but
               does NOT block a trade. MEASURED EVIDENCE (60d backtest,
               8 SMC trades): requiring Kronos agreement fired 1 trade — a
               loser — and blocked 6 winners; even veto-only mode would have
               blocked 6 winners and fired both losers. Kronos-mini at 12-24h
               is systematically counter-trend in trending regimes (it calls
               the bounce too early) and ~coin-flip at these horizons. Its
               strong contradictions are still shown as a warning.
               (mode "required") the old strict rule: p_up >= 0.68 (long) /
               <= 0.32 (short) AND |exp move| >= 0.8%. Set
               gates.kronos_mode="required" in config.json to re-enable it —
               expect far fewer trades (zero in the 60d backtest).
  G2 COMMITTEE weighted score >= +0.25 / <= -0.25 AND >= 3 of 5 voters agree
               (calibrated from measured data: over 60 days x 3 symbols the
               max |score| was 0.33 and only ~2% of bars reach 0.25 — the
               strictness comes from requiring ALL gates to pass, not one
               impossible number)
  G3 STRUCTURE long -> discount zone (<55% of 30-bar range) or bullish BOS;
               short -> premium zone (>45%) or bearish BOS
  G4 NO OPPOSING SWEEP in the last 5 bars
  G5 VOLATILITY 24h range in [1.5%, 25%]
  G6 TREND ALIGNMENT EMA50/EMA200 in the trade direction
  G7 LONG MOMENTUM (longs only) price >= 0.4% above EMA50 AND ~6h
               change >= +0.4% — the bounce must already be proving
               itself. Added 2026-11-05: the two losing 1h longs at
               TP 1.5R both had weak bounce momentum (EMA50 +0.24%/
               +0.26%, 6h +0.16%/+0.30%) vs the winning long
               (+0.45%/+0.51%). Thresholds calibrated on 1h — on
               4h/240d this gate costs 1 winner and saves 0 losers,
               so it is an 1h-calibrated filter (see README caveats).
               Shorts skip G7.
  G8 SHORT MOMENTUM (shorts only) ~6h change <= 0.0% — the mirror of
               G7: don't short into a rising price. Added 2026-11-05
               for altcoins ("ono coin au valo results lagbe"): altcoin
               short losses averaged 6h +0.39% (fired into bounces)
               vs winning shorts -0.48%.
  G9 BTC REGIME (altcoins only — BTC/ETH/SOL exempt) a non-major coin
               trades ONLY when BTC itself is >= 0.5% below ITS EMA50
               (alt_btc_ema50_pct: -0.5). Altcoins follow BTC; in the
               60d crash window altcoin wins happened with BTC-EMA50
               -0.67% vs losses -0.30%. Fails closed if no BTC candles
               are supplied. Calibrated on 1h — see README caveats.
  G10 SHORT RSI (altcoins only) shorts only if RSI >= 45 — don't short
               an oversold coin. The surviving altcoin short losses had
               RSI 40-43. Majors exempt (deeper liquidity, cleaner
               trends; their timeout wins were being killed).
  G11 LONG RSI (all symbols) longs only if RSI <= 60 — don't chase an
               overbought bounce. The stubborn AVAX long loss had RSI
               61.9 while winning longs were 33-55.

Win-rate math (documented in README): exits are TP 1.5R / SL 1R, so the
breakeven win rate is 40%. At the measured 100% (n=6, 1h/60d),
expectancy is +1.02R/trade. TP 1.5R is a user constraint — the TP 1.0R
variant (87.5%) is rejected by the user and must not be re-adopted.
"""
import json
import os

from smc_analysts import all_analysts
from kronos_forecast import kronos_verdict

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)
GATES = CFG["gates"]

WEIGHTS = {"Trend": 1.0, "Liquidity": 1.0, "Structure": 1.0,
           "Momentum": 0.75, "Kronos AI": 1.25, "TradingAgents LLM": 1.0}

# Majors are exempt from the altcoin-only gates (G9 BTC regime, G10
# short-RSI floor): deeper liquidity, cleaner trends — their timeout
# wins were being killed by the altcoin filters (measured 2026-11-05).
MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


def llm_committee(symbol, date_str):
    """TradingAgents multi-agent debate — ONLY if GROQ_API_KEY is set.

    Returns {"available": bool, "verdict": BUY/SELL/HOLD, "reasons": [...]}.
    Without a key this abstains; the pipeline continues on rule-based votes.
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return {"available": False,
                "reason": "GROQ_API_KEY not set — LLM seat abstains. "
                          "Set it to enable the TradingAgents debate."}
    try:
        ta_path = os.path.expanduser("~/workspace/kronos/TradingAgents")
        import sys
        if ta_path not in sys.path:
            sys.path.insert(0, ta_path)
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG
        cfg = dict(DEFAULT_CONFIG)
        cfg["llm_provider"] = "openai_compatible"
        cfg["backend_url"] = "https://api.groq.com/openai/v1"
        cfg["deep_think_llm"] = os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM",
                                               "llama-3.3-70b-versatile")
        cfg["quick_think_llm"] = os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM",
                                                "llama-3.1-8b-instant")
        cfg["max_debate_rounds"] = 1
        cfg["max_risk_discuss_rounds"] = 1
        graph = TradingAgentsGraph(debug=False, config=cfg)
        _, decision = graph.propagate(symbol, date_str)
        verdict = (decision or {}).get("decision", "HOLD")
        return {"available": True, "verdict": str(verdict).upper(),
                "reasons": ["TradingAgents LLM debate (Groq)"]}
    except Exception as e:
        return {"available": False, "reason": f"TradingAgents error: {str(e)[:200]}"}


def _opposing_sweep(candles, direction):
    """True if the last 5 bars swept the opposite extreme and reclaimed."""
    if len(candles) < 30:
        return False
    closes = [c["c"] for c in candles]
    hi, lo = max(closes[:-5][-25:]), min(closes[:-5][-25:])
    for c in candles[-5:]:
        if direction == "long" and c["h"] > hi * 1.001 and c["c"] < hi:
            return True
        if direction == "short" and c["l"] < lo * 0.999 and c["c"] > lo:
            return True
    return False


def _structure_ok(candles, direction):
    closes = [c["c"] for c in candles]
    if len(closes) < 30:
        return False, "not enough history"
    hi, lo = max(closes[-30:]), min(closes[-30:])
    pos = (closes[-1] - lo) / (hi - lo + 1e-9)
    bos_up = closes[-1] > hi * 1.001
    bos_dn = closes[-1] < lo * 0.999
    if direction == "long":
        ok = pos < 0.55 or bos_up
        why = f"range position {pos:.2f} (discount)" if pos < 0.55 else "bullish BOS"
        return ok, why
    ok = pos > 0.45 or bos_dn
    why = f"range position {pos:.2f} (premium)" if pos > 0.45 else "bearish BOS"
    return ok, why


def _trend_ok(candles, direction):
    closes = [c["c"] for c in candles]
    if len(closes) < 210:
        return False, "not enough history"
    k50, k200 = 2 / 51, 2 / 201
    a50 = closes[-60:][0]
    for v in closes[-60:][1:]:
        a50 = v * k50 + a50 * (1 - k50)
    a200 = closes[0]
    for v in closes[1:]:
        a200 = v * k200 + a200 * (1 - k200)
    last = closes[-1]
    if direction == "long":
        return (last > a50 > a200), f"price {last:,.2f} vs EMA50 {a50:,.2f} vs EMA200 {a200:,.2f}"
    return (last < a50 < a200), f"price {last:,.2f} vs EMA50 {a50:,.2f} vs EMA200 {a200:,.2f}"


def _long_momentum_ok(candles):
    """G7 — longs only: the bounce must already be proving itself.

    Requires price >= long_min_ema50_pct above EMA50 AND the ~6h change
    >= long_min_chg6_pct. Measured 2026-11-05 (TP 1.5R): the two losing
    1h longs both had weak bounce momentum (EMA50 +0.24%/+0.26%,
    6h +0.16%/+0.30%) while the winning long had +0.45%/+0.51%.
    Thresholds calibrated on the 1h window — see README caveats: on
    4h/240d this gate costs 1 winner and saves 0 losers, so it is
    an 1h-calibrated filter, not a universal law. Shorts are unaffected.
    Returns (ok, why)."""
    closes = [c["c"] for c in candles]
    if len(closes) < 60:
        return False, "not enough history for G7"
    # hours per bar from timestamps (robust across 1h/4h/1d)
    try:
        hpb = max(1.0, (candles[-1]["t"] - candles[-2]["t"]) / 3600)
    except Exception:
        hpb = 1.0
    mom_bars = max(1, round(6 / hpb))
    k50 = 2 / 51
    e50 = closes[-60:][0]
    for v in closes[-60:][1:]:
        e50 = v * k50 + e50 * (1 - k50)
    last = closes[-1]
    ema50_dist = (last - e50) / last * 100
    chg6 = (last / closes[-1 - mom_bars] - 1) * 100
    t_e = GATES.get("long_min_ema50_pct", 0.4)
    t_c = GATES.get("long_min_chg6_pct", 0.4)
    ok = ema50_dist >= t_e and chg6 >= t_c
    why = (f"long momentum: EMA50 {ema50_dist:+.2f}% (need >={t_e}%), "
           f"6h {chg6:+.2f}% (need >={t_c}%)")
    return ok, why


def _short_momentum_ok(candles):
    """G8 — shorts only: the ~6h change must already be <= the threshold
    (default 0.0%): don't short into a rising price. Mirror of G7.
    Returns (ok, why)."""
    from smc_analysts import _rsi as _rsi_fn  # noqa: F401 (kept for symmetry)
    closes = [c["c"] for c in candles]
    if len(closes) < 30:
        return False, "not enough history for G8"
    try:
        hpb = max(1.0, (candles[-1]["t"] - candles[-2]["t"]) / 3600)
    except Exception:
        hpb = 1.0
    mom_bars = max(1, round(6 / hpb))
    chg6 = (closes[-1] / closes[-1 - mom_bars] - 1) * 100
    t = GATES.get("short_max_chg6_pct", 0.0)
    ok = chg6 <= t
    return ok, f"short momentum: 6h {chg6:+.2f}% (need <={t}%)"


def _short_rsi_ok(candles):
    """G10 — shorts only (altcoins): RSI >= short_min_rsi (default 45).
    Don't short an oversold coin. Returns (ok, why)."""
    from smc_analysts import _rsi as _rsi_fn
    closes = [c["c"] for c in candles]
    rsi = _rsi_fn(closes)
    t = GATES.get("short_min_rsi", 45)
    return rsi >= t, f"short RSI {rsi:.1f} (need >={t})"


def _long_rsi_ok(candles):
    """G11 — longs only (all symbols): RSI <= long_max_rsi (default 60).
    Don't chase an overbought bounce. Returns (ok, why)."""
    from smc_analysts import _rsi as _rsi_fn
    closes = [c["c"] for c in candles]
    rsi = _rsi_fn(closes)
    t = GATES.get("long_max_rsi", 60)
    return rsi <= t, f"long RSI {rsi:.1f} (need <={t})"


def _btc_regime_ok(btc_candles):
    """G9 — altcoins only: BTC must be >= |alt_btc_ema50_pct| below ITS
    EMA50 (default -0.5%). Altcoins follow BTC; trade them only when
    BTC itself is clearly in a downtrend. Returns (ok, why)."""
    from smc_analysts import _ema as _ema50fn
    if not btc_candles or len(btc_candles) < 60:
        return False, "no BTC candles supplied for G9 regime check"
    closes = [c["c"] for c in btc_candles]
    e50 = _ema50fn(closes[-60:], 50)
    last = closes[-1]
    dist = (last - e50) / last * 100
    t = GATES.get("alt_btc_ema50_pct", -0.5)
    ok = dist <= t
    return ok, f"BTC regime: BTC {dist:+.2f}% vs its EMA50 (need <={t}%)"


def decide(symbol, candles, forecast, date_str=None, use_llm=True,
           btc_candles=None):
    """Run the full committee + gates. Returns the decision dict."""
    analysts = all_analysts(candles)
    kv = kronos_verdict(forecast, GATES["kronos_p_up_long"],
                        GATES["kronos_p_up_short"], GATES["kronos_min_abs_move_pct"])
    votes = analysts + [kv]
    llm = {"available": False}
    if use_llm:
        llm = llm_committee(symbol, date_str or "")
        if llm.get("available"):
            score = {"BUY": 1.0, "SELL": -1.0}.get(llm["verdict"], 0.0)
            votes.append({"analyst": "TradingAgents LLM",
                          "stance": "bull" if score > 0 else "bear" if score < 0 else "neutral",
                          "score": score, "confidence": 0.7,
                          "reasons": llm.get("reasons", [])})
    total_w = sum(WEIGHTS.get(v["analyst"], 1.0) for v in votes)
    wscore = sum(WEIGHTS.get(v["analyst"], 1.0) * v["score"] for v in votes) / total_w
    bulls = sum(1 for v in votes if v["stance"] == "bull")
    bears = sum(1 for v in votes if v["stance"] == "bear")
    # In advisory mode Kronos votes but does NOT count toward the G2
    # threshold — otherwise its neutral 0.00 dilutes the SMC score and the
    # live committee disagrees with the backtest on the same bar.
    kmode = GATES.get("kronos_mode", "advisory")
    n_scoring = len(votes)
    if kmode == "advisory":
        scoring = [v for v in votes if v["analyst"] != "Kronos AI"]
        n_scoring = len(scoring)
        sw = sum(WEIGHTS.get(v["analyst"], 1.0) for v in scoring)
        wscore = sum(WEIGHTS.get(v["analyst"], 1.0) * v["score"] for v in scoring) / sw
        bulls = sum(1 for v in scoring if v["stance"] == "bull")
        bears = sum(1 for v in scoring if v["stance"] == "bear")
    direction = "long" if wscore > 0 else "short" if wscore < 0 else None
    gates, failed = {}, []
    warnings = []
    # G1 Kronos — advisory by default (see module docstring for the evidence).
    # "required" restores the strict agreement rule.
    if kmode == "required":
        g1 = (kv["stance"] in ("bull", "bear"))
        gates["G1_kronos_decisive"] = g1
        if not g1:
            failed.append("G1 Kronos not decisive (p_up/move too weak)")
    else:
        gates["G1_kronos_advisory"] = True
        if direction and ((direction == "long" and kv["stance"] == "bear")
                          or (direction == "short" and kv["stance"] == "bull")):
            warnings.append(f"Kronos contradicts the {direction} (p_up "
                            f"{forecast.get('p_up')}) — advisory only, trade not blocked")
    # G2 committee agreement
    agree = bulls if direction == "long" else bears if direction == "short" else 0
    g2 = (direction is not None and abs(wscore) >= GATES["committee_min_score"]
          and agree >= GATES["committee_min_agree"])
    gates["G2_committee"] = g2
    if not g2:
        failed.append(f"G2 committee weak (score {wscore:+.2f}, agree {agree}/"
                      f"{n_scoring}, need |score|>={GATES['committee_min_score']} "
                      f"and >={GATES['committee_min_agree']} agree)")
    # G3 structure, G4 sweep, G5 volatility, G6 trend — only if a direction exists
    if direction:
        ok, why = _structure_ok(candles, direction)
        gates["G3_structure"] = ok
        if not ok:
            failed.append(f"G3 structure: {why} — not in value zone")
        swept = _opposing_sweep(candles, direction)
        gates["G4_no_opposing_sweep"] = not swept
        if swept:
            failed.append("G4 opposing liquidity sweep in last 5 bars")
        closes = [c["c"] for c in candles]
        seg = candles[-24:]
        rng = (max(c["h"] for c in seg) - min(c["l"] for c in seg)) / closes[-1] * 100
        g5 = GATES["min_range24h_pct"] <= rng <= GATES["max_range24h_pct"]
        gates["G5_volatility"] = g5
        if not g5:
            failed.append(f"G5 24h range {rng:.1f}% outside "
                          f"[{GATES['min_range24h_pct']}, {GATES['max_range24h_pct']}]%")
        ok6, why6 = _trend_ok(candles, direction)
        gates["G6_trend"] = ok6
        if not ok6:
            failed.append(f"G6 trend misaligned: {why6}")
        # G7 long-momentum — longs only (shorts skip it).
        if direction == "long":
            ok7, why7 = _long_momentum_ok(candles)
            gates["G7_long_momentum"] = ok7
            if not ok7:
                failed.append(f"G7 weak bounce momentum: {why7}")
        else:
            gates["G7_long_momentum"] = True
        # G8 short-momentum — shorts only (longs skip it).
        if direction == "short":
            ok8, why8 = _short_momentum_ok(candles)
            gates["G8_short_momentum"] = ok8
            if not ok8:
                failed.append(f"G8 short into rising price: {why8}")
        else:
            gates["G8_short_momentum"] = True
        # G9 BTC regime — altcoins only (majors exempt). Fails closed
        # when no BTC candles are supplied.
        if symbol.upper() not in MAJORS:
            ok9, why9 = _btc_regime_ok(btc_candles)
            gates["G9_btc_regime"] = ok9
            if not ok9:
                failed.append(f"G9 BTC regime not in downtrend: {why9}")
        else:
            gates["G9_btc_regime"] = True
        # G10 short-RSI floor — shorts only, altcoins only (majors exempt).
        if direction == "short" and symbol.upper() not in MAJORS:
            ok10, why10 = _short_rsi_ok(candles)
            gates["G10_short_rsi"] = ok10
            if not ok10:
                failed.append(f"G10 shorting an oversold coin: {why10}")
        else:
            gates["G10_short_rsi"] = True
        # G11 long-RSI ceiling — longs only (all symbols).
        if direction == "long":
            ok11, why11 = _long_rsi_ok(candles)
            gates["G11_long_rsi"] = ok11
            if not ok11:
                failed.append(f"G11 chasing an overbought bounce: {why11}")
        else:
            gates["G11_long_rsi"] = True
    else:
        gates.update({"G3_structure": False, "G4_no_opposing_sweep": False,
                      "G5_volatility": False, "G6_trend": False})
        failed.append("no direction — committee score is zero")
    passed = len(failed) == 0
    return {
        "symbol": symbol.upper(),
        "signal": ("BUY" if passed and direction == "long"
                   else "SELL" if passed and direction == "short" else "HOLD"),
        "direction": direction if passed else None,
        "weighted_score": round(wscore, 3),
        "bull_votes": bulls, "bear_votes": bears, "n_voters": n_scoring,
        "gates": gates, "failed_gates": failed, "warnings": warnings,
        "votes": votes,
        "llm": {"available": llm.get("available", False),
                "reason": llm.get("reason", "")},
        "kronos": {"p_up": forecast.get("p_up"), "exp_move_pct": forecast.get("exp_move_pct"),
                   "available": forecast.get("available", False)},
    }
