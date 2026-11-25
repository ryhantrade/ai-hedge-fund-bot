#!/usr/bin/env python3
"""STAGE 3b — KRONOS FORECAST (local foundation model, no API key).

Lazy-loads Kronos-mini (~/workspace/kronos/models/mini + mini-tok) on CPU.
Samples N forecast paths for the next `pred_len` hourly candles and reports:
  p_up         — share of paths ending above the last close
  exp_move_pct — mean end-of-path move (%)
  median/p10/p90 paths (for the report)

If torch is not installed, forecast() returns {"available": False} and the
committee treats Kronos as abstaining — the pipeline still runs, but the
strict gates will refuse any trade that needs a Kronos confirmation.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

# Deploy package: prefer ./kronos + ./models shipped in the repo, fall back
# to the dev paths on this VM. Env overrides (KRONOS_CODE / KRONOS_MODELS) win.
_PKG = os.path.dirname(os.path.abspath(__file__))
KRONOS_CODE = os.environ.get("KRONOS_CODE") or (
    os.path.join(_PKG, "kronos") if os.path.isdir(os.path.join(_PKG, "kronos"))
    else os.path.expanduser("~/workspace/kronos/Kronos"))
MODELS = os.environ.get("KRONOS_MODELS") or (
    os.path.join(_PKG, "models") if os.path.isdir(os.path.join(_PKG, "models"))
    else os.path.expanduser("~/workspace/kronos/models"))
# Model selection (env KRONOS_MODEL=mini|small). Small (d_model 512, 8 layers)
# is the bigger model; its tokenizer lives in models/tokenizer, mini's in
# models/mini-tok. Default mini.
KRONOS_MODEL = os.environ.get("KRONOS_MODEL", "mini").lower()
MODEL_DIR = "small" if KRONOS_MODEL == "small" else "mini"
TOK_DIR = "tokenizer" if KRONOS_MODEL == "small" else "mini-tok"
# The Kronos package lives at <KRONOS_CODE>/model/ — put it on sys.path
# at import time (before any `from model import ...` runs).
if KRONOS_CODE not in sys.path:
    sys.path.insert(0, KRONOS_CODE)
_predictor = None


def available():
    return os.path.isdir(os.path.join(MODELS, MODEL_DIR)) and \
        os.path.isdir(KRONOS_CODE)


def _get_predictor():
    global _predictor
    if _predictor is None:
        import torch
        try:
            torch.set_num_threads(2)
            torch.set_num_interop_threads(2)
        except Exception:
            pass
        if KRONOS_CODE not in sys.path:
            sys.path.insert(0, KRONOS_CODE)
        from model import Kronos, KronosTokenizer, KronosPredictor
        tok = KronosTokenizer.from_pretrained(os.path.join(MODELS, TOK_DIR))
        mdl = Kronos.from_pretrained(os.path.join(MODELS, MODEL_DIR))
        mdl.eval()
        _predictor = KronosPredictor(mdl, tok, max_context=512)
    return _predictor


def forecast(closes, n_paths=10, pred_len=12, end_time=None):
    """closes: list of floats (hourly closes, oldest first).
    end_time: pd.Timestamp of the last close (defaults to now — correct for
    live use; the backtest MUST pass the historical bar time, because
    Kronos uses calendar features from the timestamps)."""
    t0 = time.time()
    if not available():
        return {"available": False,
                "reason": f"Kronos weights not found at ~/workspace/kronos/models/{MODEL_DIR}"}
    try:
        import torch  # noqa: F401  (checked: torch must import)
    except ImportError:
        return {"available": False,
                "reason": "torch not installed — pip install torch --index-url "
                          "https://download.pytorch.org/whl/cpu"}
    try:
        from model.kronos import calc_time_stamps
        predictor = _get_predictor()
        n = len(closes)
        lookback = min(512, n)
        seg = closes[n - lookback:]
        now = end_time if end_time is not None else pd.Timestamp.utcnow().floor("h")
        x_ts = pd.Series(pd.date_range(now - pd.Timedelta(hours=lookback - 1),
                                        periods=lookback, freq="h"))
        y_ts = pd.Series(pd.date_range(now + pd.Timedelta(hours=1),
                                        periods=pred_len, freq="h"))
        o = np.asarray(seg, dtype=np.float32)
        rng = np.maximum(np.abs(np.diff(o, prepend=o[0])), o * 0.0005)
        df = pd.DataFrame({
            "open": o, "high": o + rng * 0.6, "low": o - rng * 0.6,
            "close": o, "volume": np.zeros_like(o), "amount": np.zeros_like(o),
        })
        x = df[["open", "high", "low", "close", "volume", "amount"]].values.astype(np.float32)
        mu, sd = x.mean(0), x.std(0)
        xn = np.clip((x - mu) / (sd + 1e-5), -5, 5)[np.newaxis, :]
        x_stamp = calc_time_stamps(x_ts).values.astype(np.float32)[np.newaxis, :]
        y_stamp = calc_time_stamps(y_ts).values.astype(np.float32)[np.newaxis, :]
        paths = []
        for _ in range(n_paths):
            p = predictor.generate(xn, x_stamp, y_stamp, pred_len,
                                   T=1.0, top_k=0, top_p=0.9,
                                   sample_count=1, verbose=False)
            p = p.squeeze(0) * (sd + 1e-5) + mu
            paths.append([float(v) for v in p[:, 3]])
        paths = np.array(paths)
        last = float(o[-1])
        ends = paths[:, -1]
        return {
            "available": True,
            "p_up": round(float((ends > last).mean()), 3),
            "exp_move_pct": round(float((ends.mean() - last) / last * 100), 2),
            "median": [round(float(v), 4) for v in np.median(paths, axis=0)],
            "p10": [round(float(v), 4) for v in np.percentile(paths, 10, axis=0)],
            "p90": [round(float(v), 4) for v in np.percentile(paths, 90, axis=0)],
            "last_close": last,
            "n_paths": n_paths, "pred_len": pred_len,
            "took_sec": round(time.time() - t0, 1),
        }
    except Exception as e:
        return {"available": False, "reason": f"Kronos error: {str(e)[:200]}"}


def kronos_verdict(fc, p_up_long=0.68, p_up_short=0.32, min_move=0.8):
    """Kronos vote: bull / bear / neutral, with the strict thresholds."""
    if not fc.get("available"):
        return {"analyst": "Kronos AI", "stance": "neutral", "score": 0.0,
                "confidence": 0.3, "reasons": [fc.get("reason", "forecast unavailable")]}
    p_up, mv = fc["p_up"], fc["exp_move_pct"]
    reasons = [f"{int(p_up * 100)}% of {fc['n_paths']} paths end higher",
               f"expected {fc['pred_len']}h move {mv:+.2f}% (median path)"]
    if p_up >= p_up_long and abs(mv) >= min_move and mv > 0:
        stance, score = "bull", min(1.0, (p_up - 0.5) * 2)
    elif p_up <= p_up_short and abs(mv) >= min_move and mv < 0:
        stance, score = "bear", -min(1.0, (0.5 - p_up) * 2)
    else:
        stance, score = "neutral", 0.0
        reasons.append("forecast not decisive enough for a high-probability entry")
    conf = round(abs(score) * 0.5 + 0.4, 2) if stance != "neutral" else 0.4
    return {"analyst": "Kronos AI", "stance": stance, "score": round(score, 2),
            "confidence": conf, "reasons": reasons}
