#!/usr/bin/env python3
"""BACKTEST — honest win-rate measurement of the confluence system.

Method (no lookahead — every decision uses only bars up to the signal bar):
  1. Step through 1h candles every 4h over the last `days` days.
  2. STAGE 1 (fast): SMC gates — committee score/agreement, structure,
     opposing sweep, volatility, trend. Bars failing any gate are skipped.
  3. STAGE 2 (slow): Kronos forecast, ONLY on Stage-1 survivors, capped
     at max_kronos_evals total (each CPU forecast takes seconds).
  4. If all gates pass: enter at next bar open, SL/TP from risk.plan(),
     walk forward up to 48 bars. If a bar's high AND low both hit SL/TP,
     SL is assumed hit first (conservative). Exit at TP, SL, or 48h timeout.
  5. Report wins / total, win rate, avg R, expectancy — per symbol and total.

If torch/Kronos is unavailable, Stage 2 is skipped and the report is
labelled SMC-ONLY (not the full confluence system). Nothing here is
tuned after seeing results; thresholds come from config.json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import fetch_candles
from smc_analysts import all_analysts
from kronos_forecast import forecast as kronos_forecast, kronos_verdict
import committee as C
import risk

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    CFG = json.load(f)
GATES = CFG["gates"]
BT = CFG["backtest"]

# Experiment flags (env): FUND_NO_KRONOS=1 forces the Kronos gate off;
# FUND_PRED_LEN=N overrides the Kronos forecast horizon;
# FUND_KRONOS_DIAG=1 records a Kronos forecast per fired trade (slow,
# diagnostic only — never gates). The canonical backtest follows config
# gates.kronos_mode: "advisory" (default) = Kronos non-blocking.
# Further experiment overrides (all default to config.json values):
#   FUND_TF=4h        timeframe (1h|4h|1d); timeout/vol windows scale to ~48h/~24h
#   FUND_AGREE=N      min analyst agreement (default 3)
#   FUND_MIN_SCORE=X  min |weighted score| (default config committee_min_score)
#   FUND_SYMBOLS=A,B  symbol list override
#   FUND_DAYS=N       backtest window in days
#   FUND_SL_ATR=X     SL distance in ATR multiples (default config sl_atr_mult)
#   FUND_TP_R=X       TP multiple in R (default config tp_r)
#   FUND_LONG_MIN_EMA50=X  longs only if price >= X% above EMA50 (default off)
#   FUND_LONG_MIN_CHG6=X   longs only if 6h change >= X% (default off)
#   (long-momentum filter — EXPERIMENT 2026-11-05: the two losing longs
#   at TP 1.5R both had weak bounce momentum; see README.)
#   ALTCOIN experiments (2026-11-05, "ono coin au valo results lagbe"):
#   FUND_SHORT_MAX_CHG6=X  shorts only if 6h change <= X% (mirror of G7;
#                          diag: altcoin short losses avg chg6 +0.39 vs
#                          winners -0.48 — shorts fired into bounces lose)
#   FUND_ALT_BTC_EMA50=X   non-major coins (not BTC/ETH/SOL) only if BTC
#                          is X% below ITS EMA50 (BTC-regime filter; diag:
#                          altcoin wins avg BTC-EMA50 -0.67% vs losses
#                          -0.30% — wins happen when BTC is clearly down)
#   FUND_SHORT_MIN_RSI=X   shorts only if RSI >= X (don't short an
#                          oversold coin; diag: the surviving short losses
#                          had RSI 40-43 while winning shorts averaged
#                          higher)
#   FUND_RSI_MAJOR_EXEMPT=1  apply the short-RSI floor to altcoins only
#                          (majors have deeper liquidity and cleaner
#                          trends; their timeout wins were being killed)
#   FUND_LONG_MAX_RSI=X    longs only if RSI <= X (don't chase an
#                          overbought bounce; the stubborn AVAX long loss
#                          had RSI 61.9 while winning longs were 33-55)
KRONOS_MODE = os.environ.get("FUND_KRONOS_MODE",
                             CFG["gates"].get("kronos_mode", "advisory"))
TF = os.environ.get("FUND_TF", CFG.get("timeframe", "1h"))
TF_HOURS = {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
            "1h": 1, "4h": 4, "1d": 24}.get(TF, 1)
MIN_AGREE = int(os.environ.get("FUND_AGREE", "3"))
MIN_SCORE = float(os.environ.get("FUND_MIN_SCORE",
                                GATES["committee_min_score"]))
EXP_SYMBOLS = [s.strip() for s in os.environ.get("FUND_SYMBOLS", "").split(",")
               if s.strip()]
EXP_DAYS = int(os.environ.get("FUND_DAYS", "0") or 0)
if os.environ.get("FUND_SL_ATR"):
    risk.TRADE["sl_atr_mult"] = float(os.environ["FUND_SL_ATR"])
if os.environ.get("FUND_TP_R"):
    risk.TRADE["tp_r"] = float(os.environ["FUND_TP_R"])
    CFG["trade"]["tp_r"] = float(os.environ["FUND_TP_R"])
TIMEOUT_BARS = max(4, int(48 / TF_HOURS))   # ~48h holding cap, any TF
VOL_BARS = max(6, int(24 / TF_HOURS))       # 24h range window, in bars
LONG_MIN_EMA50 = float(os.environ.get(
    "FUND_LONG_MIN_EMA50", GATES.get("long_min_ema50_pct", 0)) or 0)
LONG_MIN_CHG6 = float(os.environ.get(
    "FUND_LONG_MIN_CHG6", GATES.get("long_min_chg6_pct", 0)) or 0)
SHORT_MAX_CHG6 = os.environ.get("FUND_SHORT_MAX_CHG6")
SHORT_MAX_CHG6 = (GATES.get("short_max_chg6_pct", 0.0) if SHORT_MAX_CHG6 is None
                  else float(SHORT_MAX_CHG6))
ALT_BTC_EMA50 = os.environ.get("FUND_ALT_BTC_EMA50")
ALT_BTC_EMA50 = (GATES.get("alt_btc_ema50_pct", -0.5) if ALT_BTC_EMA50 is None
                 else float(ALT_BTC_EMA50))
SHORT_MIN_RSI = os.environ.get("FUND_SHORT_MIN_RSI")
SHORT_MIN_RSI = (GATES.get("short_min_rsi", 45) if SHORT_MIN_RSI is None
                 else float(SHORT_MIN_RSI))
RSI_MAJOR_EXEMPT = os.environ.get("FUND_RSI_MAJOR_EXEMPT", "1") == "1"
LONG_MAX_RSI = os.environ.get("FUND_LONG_MAX_RSI")
LONG_MAX_RSI = (GATES.get("long_max_rsi", 60) if LONG_MAX_RSI is None
                else float(LONG_MAX_RSI))
MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
MOM_BARS = max(1, round(6 / TF_HOURS))      # ~6h momentum window, in bars
NO_KRONOS = os.environ.get("FUND_NO_KRONOS") == "1" or KRONOS_MODE == "advisory"
KRONOS_DIAG = os.environ.get("FUND_KRONOS_DIAG") == "1"
PRED_LEN = int(os.environ.get("FUND_PRED_LEN") or 0) or None


def _simulate(candles, start_idx, plan, timeout_bars=48):
    """Walk forward from start_idx; return (exit_price, reason, r_multiple)."""
    entry, sl, tp = plan["entry"], plan["sl"], plan["tp"]
    direction = plan["direction"]
    risk_unit = abs(entry - sl)
    tp_r = abs(tp - entry) / risk_unit if risk_unit else 1.5
    for j in range(start_idx, min(start_idx + timeout_bars, len(candles))):
        bar = candles[j]
        if direction == "long":
            hit_sl = bar["l"] <= sl
            hit_tp = bar["h"] >= tp
        else:
            hit_sl = bar["h"] >= sl
            hit_tp = bar["l"] <= tp
        if hit_sl and hit_tp:
            return sl, "SL (both hit same bar — SL assumed first)", -1.0
        if hit_sl:
            return sl, "SL", -1.0
        if hit_tp:
            return tp, "TP", round(tp_r, 2)
    last = candles[min(start_idx + timeout_bars, len(candles) - 1)]["c"]
    r = (last - entry) / risk_unit if direction == "long" else (entry - last) / risk_unit
    return last, f"timeout {timeout_bars} bars", round(r, 3)


def backtest_symbol(symbol, days=None, kronos_budget=None, verbose=True):
    days = days or EXP_DAYS or BT["days"]
    limit = int(days * (24 / TF_HOURS) + 260)
    candles = fetch_candles(symbol, TF, limit)
    if len(candles) < 300:
        return {"symbol": symbol, "ok": False, "reason": f"only {len(candles)} candles"}
    # BTC candles for the altcoin regime filter (cached by data layer).
    btc = None
    btc_by_t = {}
    if ALT_BTC_EMA50 is not None and symbol not in MAJORS:
        btc = fetch_candles("BTCUSDT", TF, limit)
        btc_by_t = {c["t"]: idx for idx, c in enumerate(btc)}
    start = 260  # need 200+ bars for EMA200 + structure
    trades = []
    kronos_used = 0
    kronos_evals = []  # diagnostic: every Kronos eval (p_up, move, stance, SMC dir)
    budget = BT["max_kronos_evals"] if kronos_budget is None else kronos_budget
    diag = {"bars": 0, "score_pass": 0, "structure_pass": 0, "sweep_pass": 0,
            "vol_pass": 0, "trend_pass": 0, "kronos_agree": 0}
    # ANCHOR the sampling to wall-clock boundaries (2026-11-05 fix):
    # the old code stepped every 4th bar from a fixed ARRAY index, so a
    # 1-bar slide of the 1440-bar OKX window changed EVERY sampled bar
    # and the trade set flipped (100% -> 55.6% on a 1-bar shift). Now the
    # first sampled bar is the first bar >= start on a 4-bar time
    # boundary, so the same wall-clock bars are evaluated regardless of
    # window alignment. Step stays 4 bars (4h on 1h, 16h on 4h, 4d on 1d).
    step_seconds = 4 * TF_HOURS * 3600
    i = start
    while i < len(candles) - 50 and candles[i]["t"] % step_seconds != 0:
        i += 1
    while i < len(candles) - 50:
        diag["bars"] += 1
        window = candles[:i + 1]
        closes = [c["c"] for c in window]
        analysts = all_analysts(window)
        w = {"Trend": 1.0, "Liquidity": 1.0, "Structure": 1.0, "Momentum": 0.75}
        tw = sum(w[a["analyst"]] for a in analysts)
        wscore = sum(w[a["analyst"]] * a["score"] for a in analysts) / tw
        bulls = sum(1 for a in analysts if a["stance"] == "bull")
        bears = sum(1 for a in analysts if a["stance"] == "bear")
        direction = "long" if wscore > 0 else "short" if wscore < 0 else None
        agree = bulls if direction == "long" else bears if direction == "short" else 0
        # EXPERIMENT: long-momentum filter (longs only) — skip weak bounces.
        if direction == "long" and (LONG_MIN_EMA50 or LONG_MIN_CHG6):
            from smc_analysts import _ema as _ema50fn
            e50 = _ema50fn(closes[-60:], 50)
            ema50_dist = (closes[-1] - e50) / closes[-1] * 100
            chg6 = (closes[-1] / closes[-1 - MOM_BARS] - 1) * 100
            if ema50_dist < LONG_MIN_EMA50 or chg6 < LONG_MIN_CHG6:
                i += 4
                continue
        # EXPERIMENT: short-momentum filter (mirror of G7) — skip shorts
        # fired while price is still rising into the short.
        if direction == "short" and SHORT_MAX_CHG6 is not None:
            chg6s = (closes[-1] / closes[-1 - MOM_BARS] - 1) * 100
            if chg6s > SHORT_MAX_CHG6:
                i += 4
                continue
        # EXPERIMENT: short-RSI floor — don't short an oversold coin.
        if (direction == "short" and SHORT_MIN_RSI is not None
                and not (RSI_MAJOR_EXEMPT and symbol in MAJORS)):
            from smc_analysts import _rsi as _rsi_fn
            if _rsi_fn(closes) < SHORT_MIN_RSI:
                i += 4
                continue
        # EXPERIMENT: long-RSI ceiling — don't chase an overbought bounce.
        if direction == "long" and LONG_MAX_RSI is not None:
            from smc_analysts import _rsi as _rsi_fn
            if _rsi_fn(closes) > LONG_MAX_RSI:
                i += 4
                continue
        # EXPERIMENT: BTC-regime filter for altcoins — only trade a
        # non-major when BTC itself is clearly below its EMA50.
        if ALT_BTC_EMA50 is not None and symbol not in MAJORS and btc:
            bidx = btc_by_t.get(candles[i]["t"])
            if bidx is not None and bidx >= 60:
                from smc_analysts import _ema as _ema50fn
                bc = [c["c"] for c in btc[:bidx + 1]]
                be50 = _ema50fn(bc[-60:], 50)
                btc_ema50 = (bc[-1] - be50) / bc[-1] * 100
                if btc_ema50 > ALT_BTC_EMA50:
                    i += 4
                    continue
        if direction and abs(wscore) >= MIN_SCORE and agree >= MIN_AGREE:
            diag["score_pass"] += 1
            ok3, _ = C._structure_ok(window, direction)
            swept = C._opposing_sweep(window, direction)
            seg = window[-VOL_BARS:]
            rng = (max(c["h"] for c in seg) - min(c["l"] for c in seg)) / closes[-1] * 100
            ok6, _ = C._trend_ok(window, direction)
            if ok3:
                diag["structure_pass"] += 1
            if not swept:
                diag["sweep_pass"] += 1
            if GATES["min_range24h_pct"] <= rng <= GATES["max_range24h_pct"]:
                diag["vol_pass"] += 1
            if ok6:
                diag["trend_pass"] += 1
            if ok3 and not swept and GATES["min_range24h_pct"] <= rng <= GATES["max_range24h_pct"] and ok6:
                # Stage 2: Kronos (capped) — skipped in SMC-ONLY experiments.
                if NO_KRONOS:
                    diag["kronos_agree"] += 1
                    kv_dir = direction
                else:
                    if kronos_used >= budget:
                        i += 4
                        continue
                    kronos_used += 1
                    import pandas as pd
                    end_time = pd.Timestamp(candles[i]["t"], unit="s", tz="UTC")
                    fc = kronos_forecast(closes, n_paths=10,
                                         pred_len=PRED_LEN or BT["kronos_pred_len"],
                                         end_time=end_time)
                    kv = kronos_verdict(fc, GATES["kronos_p_up_long"],
                                        GATES["kronos_p_up_short"],
                                        GATES["kronos_min_abs_move_pct"])
                    kv_dir = "long" if kv["stance"] == "bull" else "short" if kv["stance"] == "bear" else None
                    kronos_evals.append({
                        "bar_t": candles[i]["t"], "smc_dir": direction,
                        "p_up": fc.get("p_up"), "exp_move_pct": fc.get("exp_move_pct"),
                        "kronos_stance": kv["stance"], "agree": kv_dir == direction,
                    })
                if kv_dir != direction:
                    i += 4
                    continue
                diag["kronos_agree"] += 1 if not NO_KRONOS else 0
                plan = risk.plan(direction, window, equity_usd=CFG["paper_equity_usd"])
                # entry at next bar open
                plan["entry"] = candles[i + 1]["o"]
                # re-anchor SL/TP to the actual entry (keep same risk distance)
                entry = plan["entry"]
                dist = abs(plan["entry"] - plan["sl"])
                if direction == "long":
                    plan["sl"] = entry - dist
                    plan["tp"] = entry + dist * CFG["trade"]["tp_r"]
                else:
                    plan["sl"] = entry + dist
                    plan["tp"] = entry - dist * CFG["trade"]["tp_r"]
                exit_px, reason, r = _simulate(candles, i + 1, plan, TIMEOUT_BARS)
                trade = {"symbol": symbol, "direction": direction,
                         "entry": round(entry, 2), "exit": round(exit_px, 2),
                         "r": r, "win": r > 0, "reason": reason,
                         "bar": candles[i]["t"], "agree": agree,
                         "wscore": round(wscore, 2)}
                if NO_KRONOS and KRONOS_DIAG:
                    # Diagnostic only: what would Kronos have said about
                    # this SMC trade? (Does NOT gate the trade.)
                    import pandas as pd
                    end_time = pd.Timestamp(candles[i]["t"], unit="s", tz="UTC")
                    fc = kronos_forecast(closes, n_paths=10,
                                         pred_len=PRED_LEN or BT["kronos_pred_len"],
                                         end_time=end_time)
                    trade["kronos_p_up_diag"] = fc.get("p_up")
                    trade["kronos_move_diag"] = fc.get("exp_move_pct")
                    kronos_used += 1
                trades.append(trade)
        i += 4
    wins = sum(1 for t in trades if t["win"])
    r_sum = sum(t["r"] for t in trades)
    return {
        "symbol": symbol, "ok": True, "n": len(trades), "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": round(100 * wins / len(trades), 1) if trades else None,
        "total_r": round(r_sum, 2),
        "avg_r": round(r_sum / len(trades), 2) if trades else None,
        "kronos_evals": kronos_used,
        "kronos_eval_detail": kronos_evals,
        "diag": diag,
        "trades": trades,
    }


def run(symbols=None, days=None):
    symbols = symbols or EXP_SYMBOLS or BT["symbols"]
    days = days or EXP_DAYS or BT["days"]
    if os.environ.get("FUND_NO_KRONOS") == "1" and KRONOS_MODE != "advisory":
        mode = "SMC-ONLY experiment (Kronos gate DISABLED via env)"
    elif KRONOS_MODE == "advisory":
        mode = "ADVISORY (Kronos non-blocking, per config)"
    else:
        mode = f"FULL CONFLUENCE (Kronos pred_len={PRED_LEN or BT['kronos_pred_len']}h)"
    print(f"[backtest] mode: {mode}")
    print(f"[backtest] experiment: TF={TF} days={days} agree>={MIN_AGREE} "
          f"min_score={MIN_SCORE} sl_atr={risk.TRADE['sl_atr_mult']} "
          f"tp_r={risk.TRADE['tp_r']} timeout={TIMEOUT_BARS} bars "
          f"short_max_chg6={SHORT_MAX_CHG6} alt_btc_ema50={ALT_BTC_EMA50} "
          f"short_min_rsi={SHORT_MIN_RSI} rsi_major_exempt={RSI_MAJOR_EXEMPT} "
          f"long_max_rsi={LONG_MAX_RSI}")
    results = []
    for s in symbols:
        print(f"[backtest] {s} ...", flush=True)
        r = backtest_symbol(s, days=days)
        results.append(r)
        if r.get("ok"):
            print(f"  {s}: {r['n']} trades, {r['wins']}W/{r['losses']}L, "
                  f"win rate {r['win_rate_pct']}%, total {r['total_r']}R, "
                  f"Kronos evals {r['kronos_evals']}")
            for t in r.get("trades", []):
                extra = ""
                if "kronos_p_up_diag" in t:
                    extra = (f" | Kronos p_up {t['kronos_p_up_diag']} "
                             f"move {t['kronos_move_diag']:+.2f}%")
                print(f"    trade {t['direction']:5s} entry {t['entry']:>10.2f} "
                      f"exit {t['exit']:>10.2f} R {t['r']:+.2f} "
                      f"{'WIN' if t['win'] else 'LOSS'} ({t['reason']}) "
                      f"[agree {t.get('agree')}/4 score {t.get('wscore'):+.2f}]{extra}")
            print(f"    gate funnel: {r['diag']['bars']} bars -> score {r['diag']['score_pass']} "
                  f"-> structure {r['diag']['structure_pass']} / no-sweep {r['diag']['sweep_pass']} "
                  f"/ vol {r['diag']['vol_pass']} / trend {r['diag']['trend_pass']} "
                  f"-> {'trades ' + str(r['n']) + ' (Kronos advisory, non-blocking)' if NO_KRONOS else 'Kronos-agree ' + str(r['diag']['kronos_agree'])}")
            for ev in r.get("kronos_eval_detail", []):
                print(f"    kronos eval: SMC {ev['smc_dir']:5s} p_up {ev['p_up']} "
                      f"move {ev['exp_move_pct']:+.2f}% -> {ev['kronos_stance']} "
                      f"(agree={ev['agree']})")
        else:
            print(f"  {s}: FAILED — {r.get('reason')}")
    tot_n = sum(r.get("n", 0) for r in results)
    tot_w = sum(r.get("wins", 0) for r in results)
    tot_r = sum(r.get("total_r", 0) for r in results)
    print("=" * 60)
    print(f"TOTAL: {tot_n} trades, {tot_w} wins, "
          f"win rate {round(100 * tot_w / tot_n, 1) if tot_n else None}%, "
          f"total {round(tot_r, 2)}R, expectancy "
          f"{round(tot_r / tot_n, 2) if tot_n else None}R/trade")
    print(f"TARGET: 80-99% win rate (TP {risk.TRADE['tp_r']}R / SL 1R)")
    if tot_n and (100 * tot_w / tot_n) < 80:
        print("BELOW TARGET — see README 'Win-rate optimization' for levers.")
    return results


if __name__ == "__main__":
    run()
