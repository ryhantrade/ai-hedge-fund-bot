"""SMC playbook engine — copied unchanged from ~/workspace/backtest/btc_4h15m_engine.py
(2026-10-18, video-rules 4H/15m). Gold reuses it via gold_strategy.py.
"""
"""
BTC 4H/15m video-rules strategy engine (shared by backtest + live scanner).

4H:
  - Direction: 4H BOS (break of prior 4H swing high/low); else no bias.
  - POI: 4H demand/supply zones (displacement origins).
  - FIB: last 4H BOS range; longs ONLY in discount (<50%), shorts ONLY in premium (>50%).
1H MTF (Ep 11): 1H trend must align with 4H + 1H POI near price.
15m entry (conservative models, Ep 18 - kept as-is per user 'entry model na'):
  - Shift: 15m liquidity sweep -> 15m market shift -> pullback into shift zone.
  - Flip: breakout through POI -> retest of broken zone.
TP (user choice 2026-10-18): extend3 for Shift = max(nearest opposing 4H POI, fixed 1:3R).
Flip keeps fixed 1:1. 1% risk, 0.05%/side fee.

Validated 2026-10-18 (1y Coinbase 15m): 15 trades, 53% win, +0.87R avg, PF 2.86, +10.7%, DD -4.7%.
"""
import numpy as np
import pandas as pd

FEE = 0.0005
EXTEND_RR = 3.0   # extend3: TP = max(4H POI target, 1:3R) for Shift model
FLIP_RR = 1.0     # Flip stays fixed 1:1 (counter-trend, take profit quick)

BACKTEST = {"trades": 15, "win": 0.53, "avgR": 0.87, "ret": 10.7, "dd": -4.7,
            "note": "video-rules 4H/15m, extend3 TP, 1y Coinbase 15m, 1% risk"}


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(span=n, adjust=False).mean().to_numpy()


def swings(Hh, Ll, order):
    hi, lo = [], []
    for i in range(order, len(Hh) - order):
        if Hh[i] == max(Hh[i - order:i + order + 1]): hi.append((i, Hh[i]))
        if Ll[i] == min(Ll[i - order:i + order + 1]): lo.append((i, Ll[i]))
    return hi, lo


def prepare(m15, h4):
    """m15/h4: DataFrames with open/high/low/close, tz-naive UTC index."""
    m15 = m15.copy(); h4 = h4.copy()
    m15.index = pd.to_datetime(m15.index).tz_convert("UTC").tz_localize(None)
    h4.index = pd.to_datetime(h4.index).tz_convert("UTC").tz_localize(None)
    h4 = h4[h4.index >= (m15.index[0] - pd.Timedelta(days=30))]

    O, H, L, C = (m15[c].to_numpy() for c in ["open", "high", "low", "close"])
    O4, H4, L4, C4 = (h4[c].to_numpy() for c in ["open", "high", "low", "close"])
    T, T4 = m15.index, h4.index
    N, N4 = len(C), len(C4)
    A = atr(H, L, C)
    A4 = atr(H4, L4, C4)
    H4hi, H4lo = swings(H4, L4, order=4)
    Hhi, Hlo = swings(H, L, order=4)

    def h4_pos(t):
        return max(0, T4.searchsorted(t, side="right") - 1)

    # ---- 4H trend + BOS range ----
    trend = np.zeros(N4, dtype=int)
    rng_lo = np.full(N4, np.nan)
    rng_hi = np.full(N4, np.nan)
    last_hi = None; last_lo = None; cur_t = 0; cur_lo = np.nan; cur_hi = np.nan
    for j in range(5, N4):
        for (b, px) in H4hi:
            if b < j and (last_hi is None or b > last_hi[0]): last_hi = (b, px)
        for (b, px) in H4lo:
            if b < j and (last_lo is None or b > last_lo[0]): last_lo = (b, px)
        if last_hi and C4[j] > last_hi[1]:
            cur_t = 1
            cur_hi = last_hi[1]
            cur_lo = last_lo[1] if last_lo else L4[max(0, j - 20):j].min()
        elif last_lo and C4[j] < last_lo[1]:
            cur_t = -1
            cur_lo = last_lo[1]
            cur_hi = last_hi[1] if last_hi else H4[max(0, j - 20):j].max()
        trend[j] = cur_t
        rng_lo[j] = cur_lo; rng_hi[j] = cur_hi

    # ---- 4H POIs (demand/supply from displacement origins) ----
    POIS = []  # (j4, zlo, zhi, d)
    for j in range(5, N4 - 1):
        rng = H4[j] - L4[j]
        if rng < 1.0 * A4[j] or A4[j] == 0: continue
        d = 1 if C4[j] > O4[j] else -1
        for b in range(j - 1, max(j - 4, 0), -1):
            up = C4[b] > O4[b]
            if (d == 1 and not up) or (d == -1 and up):
                POIS.append((j, L4[b], H4[b], d))
                break

    # ---- 1H MTF layer (resampled from 15m) ----
    h1 = m15.resample("1h").agg({"open": "first", "high": "max",
                                 "low": "min", "close": "last"}).dropna()
    O1, H1, L1, C1 = (h1[c].to_numpy() for c in ["open", "high", "low", "close"])
    T1 = h1.index
    N1 = len(C1)
    A1 = atr(H1, L1, C1)
    H1hi, H1lo = swings(H1, L1, order=5)
    POIS1 = []  # 1H POIs: (bar, zlo, zhi, d)
    for j1 in range(5, len(h1) - 1):
        rng = H1[j1] - L1[j1]
        if rng < 1.0 * A1[j1] or A1[j1] == 0: continue
        d = 1 if C1[j1] > O1[j1] else -1
        for b in range(j1 - 1, max(j1 - 4, 0), -1):
            up = C1[b] > O1[b]
            if (d == 1 and not up) or (d == -1 and up):
                POIS1.append((b, L1[b], H1[b], d))
                break

    # ---- 1H trend via BOS ----
    trend1 = np.zeros(N1, dtype=int)
    lh1 = None; ll1 = None; cur1 = 0
    for j in range(5, N1):
        for (b, px) in H1hi:
            if b < j and (lh1 is None or b > lh1[0]): lh1 = (b, px)
        for (b, px) in H1lo:
            if b < j and (ll1 is None or b > ll1[0]): ll1 = (b, px)
        if lh1 and C1[j] > lh1[1]: cur1 = 1
        elif ll1 and C1[j] < ll1[1]: cur1 = -1
        trend1[j] = cur1

    def h1_pos(t):
        return max(0, T1.searchsorted(t, side="right") - 1)

    def fib_at(j):
        plo, phi = rng_lo[j], rng_hi[j]
        if np.isnan(plo) or np.isnan(phi) or phi <= plo: return None
        return (plo, phi)

    return dict(O=O, H=H, L=L, C=C, A=A, T=T, N=N,
                O4=O4, H4=H4, L4=L4, C4=C4, A4=A4, T4=T4, N4=N4,
                trend=trend, rng_lo=rng_lo, rng_hi=rng_hi, POIS=POIS,
                O1=O1, H1=H1, L1=L1, C1=C1, A1=A1, T1=T1, N1=N1,
                trend1=trend1, POIS1=POIS1,
                h4_pos=h4_pos, h1_pos=h1_pos, fib_at=fib_at)


def gates_at(ctx, i):
    """Evaluate the gate chain at 15m bar i. Returns dict of gate results."""
    O, H, L, C, A, T = ctx["O"], ctx["H"], ctx["L"], ctx["C"], ctx["A"], ctx["T"]
    A4, T4, N4 = ctx["A4"], ctx["T4"], ctx["N4"]
    trend, POIS = ctx["trend"], ctx["POIS"]
    A1, N1, trend1, POIS1 = ctx["A1"], ctx["N1"], ctx["trend1"], ctx["POIS1"]
    h4_pos, h1_pos, fib_at = ctx["h4_pos"], ctx["h1_pos"], ctx["fib_at"]
    g = {}
    t = T[i]; j = h4_pos(t)
    if j < 10 or j >= N4:
        return None
    d = trend[j]
    g["d"] = d
    g["j4"] = j
    g["trend_ok"] = d != 0
    g["trend_detail"] = ("Bullish" if d == 1 else "Bearish" if d == -1
                         else "No BOS bias") + " on 4H"
    j1 = h1_pos(t)
    g["j1"] = j1
    g["mtf_ok"] = j1 >= 5 and trend1[j1] == d and d != 0
    fb = fib_at(j)
    g["fib"] = fb
    if fb is None:
        g["zone_ok"] = False
    else:
        plo, phi = fb
        eq = (plo + phi) / 2
        px = C[i]
        g["zone_ok"] = (d == 1 and px < eq) or (d == -1 and px > eq)
        g["eq"] = eq
        g["zone_detail"] = ("discount" if px < eq else "premium") + \
            f" (fib 50% @ {eq:,.0f})"
    zone = None
    if g["zone_ok"]:
        for (jb, zlo_, zhi_, pd_) in reversed(POIS):
            if pd_ != d or jb > j or j - jb > 40: continue
            mid = (zlo_ + zhi_) / 2
            if d == 1 and mid >= eq: continue
            if d == -1 and mid <= eq: continue
            if d == 1 and L[i] <= zhi_ + 0.3 * A4[j] and px > zlo_ - 0.5 * A4[j]:
                zone = (zlo_, zhi_); break
            if d == -1 and H[i] >= zlo_ - 0.3 * A4[j] and px < zhi_ + 0.5 * A4[j]:
                zone = (zlo_, zhi_); break
    g["zone"] = zone
    has_mtf_poi = False
    if zone is not None:
        for (b1, z1lo, z1hi, pd1) in reversed(POIS1):
            if pd1 != d or b1 > j1 or j1 - b1 > 40: continue
            if d == 1 and L[i] <= z1hi + 0.3 * A1[j1] and px > z1lo - 0.5 * A1[j1]:
                has_mtf_poi = True; break
            if d == -1 and H[i] >= z1lo - 0.3 * A1[j1] and px < z1hi + 0.5 * A1[j1]:
                has_mtf_poi = True; break
    g["mtfpoi_ok"] = has_mtf_poi
    return g


def find_target(ctx, d, entry, i, sl):
    """TP = nearest opposing 4H POI (Ep 11: 'target is always a higher-timeframe
    POI on the opposite side'), with min 0.5R distance. Skips noise zones."""
    POIS, T4 = ctx["POIS"], ctx["T4"]
    tgt = None
    t = ctx["T"][i]
    j4 = max(0, T4.searchsorted(t, side="right") - 1)
    risk = max(abs(entry - sl), 1e-9)
    if d == 1:
        best = None
        for (b, zlo, zhi, pd_) in POIS:
            if pd_ != -1 or b >= j4 or zlo <= entry: continue
            if (zlo - entry) <= 0: continue
            if best is None or zlo < best: best = zlo
        tgt = best
    else:
        best = None
        for (b, zlo, zhi, pd_) in POIS:
            if pd_ != 1 or b >= j4 or zhi >= entry: continue
            if (entry - zhi) <= 0: continue
            if best is None or zhi > best: best = zhi
        tgt = best
    if tgt is None: return None, 0
    return tgt, abs(tgt - entry) / risk


def scan_signals(ctx):
    """Historical scan over the strategy's state machine.
    Returns (sigs, DIAG, armed) where armed is the state dict at the last bar
    (None when flat)."""
    O, H, L, C, A, T, N = (ctx[k] for k in ("O", "H", "L", "C", "A", "T", "N"))
    DIAG = dict(cand=0, bias=0, mtf=0, zone=0, poi=0, mtfpoi=0,
                sweep=0, shift=0, flip=0, sig=0, norr=0)
    sigs = []
    armed = None
    for i in range(60, N):
        if np.isnan(A[i]) or A[i] == 0: continue
        DIAG["cand"] += 1

        if armed is None and i % 4 == 0:
            g = gates_at(ctx, i)
            if g is None: continue
            d = g["d"]
            if d == 0: continue
            DIAG["bias"] += 1
            if not g["mtf_ok"]:
                DIAG["mtf"] += 1
                continue
            if g["fib"] is None: continue
            if not g["zone_ok"]: continue
            DIAG["zone"] += 1
            if g["zone"] is None: continue
            DIAG["poi"] += 1
            if not g["mtfpoi_ok"]:
                DIAG["mtfpoi"] += 1
                continue
            zone = g["zone"]
            armed = {"d": d, "zlo": zone[0], "zhi": zone[1],
                     "until": i + 96, "state": "wait_sweep"}
            continue

        if armed is None: continue
        a = armed
        if i > a["until"]:
            armed = None; continue
        d = a["d"]

        if a["state"] == "wait_sweep":
            lo = max(0, i - 12)
            broke = (d == 1 and C[i] < a["zlo"] - 0.3 * A[i]) or \
                    (d == -1 and C[i] > a["zhi"] + 0.3 * A[i])
            if broke:
                DIAG["flip"] += 1
                a.update(state="wait_flip_retest", fd=-d,
                         fzl=a["zlo"], fzh=a["zhi"],
                         fext=L[i] if d == 1 else H[i],
                         until2=i + 48)
                continue
            if d == 1:
                ref = L[lo:i].min()
                if L[i] < ref and C[i] > ref:
                    DIAG["sweep"] += 1
                    a.update(state="wait_shift", prot=L[i], sweep_bar=i,
                             until2=i + 32)
            else:
                ref = H[lo:i].max()
                if H[i] > ref and C[i] < ref:
                    DIAG["sweep"] += 1
                    a.update(state="wait_shift", prot=H[i], sweep_bar=i,
                             until2=i + 32)
        elif a["state"] == "wait_flip_retest":
            if i > a["until2"]:
                armed = None; continue
            fd = a["fd"]
            if (fd == -1 and L[i] < a["fext"] - 0.3 * A[i]) or \
               (fd == 1 and H[i] > a["fext"] + 0.3 * A[i]):
                armed = None; continue
            hit = (fd == -1 and H[i] >= a["fzl"] and C[i] <= a["fzh"]) or \
                  (fd == 1 and L[i] <= a["fzh"] and C[i] >= a["fzl"])
            if hit:
                entry = C[i]
                buf = max(0.15 * A[i], 0.0004 * entry)
                # Flip SL: beyond broken zone (now opposite polarity)
                # Short (fd=-1): broken demand -> resistance, SL above fzh
                # Long (fd=1): broken supply -> support, SL below fzl
                sl = a["fzh"] + buf if fd == -1 else a["fzl"] - buf
                if (fd == -1 and sl > entry) or (fd == 1 and sl < entry):
                    tp, rr = find_target(ctx, fd, entry, i, sl)
                    if tp is None:
                        DIAG["norr"] += 1
                        armed = None
                        continue
                    # Flip: fixed 1:1 (counter-trend, take profit quick)
                    risk = abs(entry - sl)
                    tp = entry - risk if fd == -1 else entry + risk
                    rr = FLIP_RR
                    DIAG["sig"] += 1
                    sigs.append({"d": fd, "entry": entry, "sl": sl, "tp": tp,
                                 "i": i, "t": str(T[i])[:16], "model": "flip",
                                 "rr": round(rr, 2)})
                armed = None
        elif a["state"] == "wait_shift":
            if i > a["until2"]:
                armed = None; continue
            if (d == 1 and C[i] < a["prot"]) or (d == -1 and C[i] > a["prot"]):
                armed = None; continue
            lo = max(0, a["sweep_bar"] - 12)
            shifted = False
            if d == 1:
                lvl = H[lo:a["sweep_bar"]+1].max()
                if C[i] > lvl: shifted = True
            else:
                lvl = L[lo:a["sweep_bar"]+1].min()
                if C[i] < lvl: shifted = True
            if shifted:
                DIAG["shift"] += 1
                zb = i
                for b in range(i - 1, max(i - 5, 0), -1):
                    if (d == 1 and C[b] < O[b]) or (d == -1 and C[b] > O[b]):
                        zb = b; break
                a.update(state="wait_pullback", zzl=L[zb], zzh=H[zb],
                         until3=i + 48)
        elif a["state"] == "wait_pullback":
            if i > a["until3"]:
                armed = None; continue
            if (d == 1 and C[i] < a["prot"]) or (d == -1 and C[i] > a["prot"]):
                armed = None; continue
            hit = (d == 1 and L[i] <= a["zzh"] and C[i] >= a["zzl"]) or \
                  (d == -1 and H[i] >= a["zzl"] and C[i] <= a["zzh"])
            if hit:
                entry = C[i]
                buf = max(0.15 * A[i], 0.0004 * entry)
                sl = a["prot"] - buf if d == 1 else a["prot"] + buf
                if (d == 1 and sl < entry) or (d == -1 and sl > entry):
                    tp, rr = find_target(ctx, d, entry, i, sl)
                    # extend3 (user choice 2026-10-18): TP = max(4H POI, fixed 1:3R)
                    # Keeps all signals; lengthens short targets instead of filtering them
                    risk = abs(entry - sl)
                    fixed_tp = entry + d * EXTEND_RR * risk
                    if tp is None:
                        tp, rr = fixed_tp, EXTEND_RR
                    else:
                        poi_dist = abs(tp - entry)
                        if poi_dist < EXTEND_RR * risk:
                            tp, rr = fixed_tp, EXTEND_RR
                    DIAG["sig"] += 1
                    sigs.append({"d": d, "entry": entry, "sl": sl, "tp": tp,
                                 "i": i, "t": str(T[i])[:16], "model": "shift",
                                 "rr": round(rr, 2)})
                armed = None
    return sigs, DIAG, armed


def simulate(ctx, sigs):
    """1% risk walk-forward over historical signals -> trade ledger."""
    O, H, L, C, N = (ctx[k] for k in ("O", "H", "L", "C", "N"))
    eq = 10000.0; peak = eq; maxdd = 0.0; pos = None; ledger = []
    sig_by_bar = {}
    for s in sigs: sig_by_bar.setdefault(s["i"], []).append(s)
    for i in range(N):
        if pos is None:
            for s in sig_by_bar.get(i, []):
                d, entry, sl = s["d"], s["entry"], s["sl"]
                risk_amt = eq * 0.01
                qty = risk_amt / abs(entry - sl)
                if qty * entry > eq * 10: qty = eq * 10 / entry
                eq -= qty * entry * FEE
                pos = dict(s, qty=qty, open_eq=eq)
                break
        else:
            d = pos["d"]
            hs = (L[i] <= pos["sl"]) if d == 1 else (H[i] >= pos["sl"])
            ht = (H[i] >= pos["tp"]) if d == 1 else (L[i] <= pos["tp"])
            px = why = None
            if hs and ht:
                px, why = (pos["sl"], "sl") if abs(O[i]-pos["sl"]) < abs(O[i]-pos["tp"]) else (pos["tp"], "tp")
            elif hs: px, why = pos["sl"], "sl"
            elif ht: px, why = pos["tp"], "tp"
            if px is not None:
                pnl = d * (px - pos["entry"]) * pos["qty"]
                eq = pos["open_eq"] + pnl - pos["qty"] * px * FEE
                r0 = abs(pos["entry"] - pos["sl"]) * pos["qty"]
                ledger.append({"t": pos["t"], "dir": "L" if d == 1 else "S",
                               "entry": round(pos["entry"]), "sl": round(pos["sl"]),
                               "tp": round(pos["tp"]), "why": why,
                               "model": pos.get("model", "?"),
                               "r": round(pnl / (r0 + 1e-9), 2)})
                pos = None
                peak = max(peak, eq); maxdd = min(maxdd, (eq - peak) / peak)
    return ledger


def resolve_signal(ctx, s):
    """Resolve one signal over bars after its entry bar.
    Returns (result, exit_time, r) or ('open', None, None) if still open."""
    O, H, L, C, T, N = (ctx[k] for k in ("O", "H", "L", "C", "T", "N"))
    d, entry, sl, tp = s["d"], s["entry"], s["sl"], s["tp"]
    for j in range(s["i"] + 1, N):
        hs = (L[j] <= sl) if d == 1 else (H[j] >= sl)
        ht = (H[j] >= tp) if d == 1 else (L[j] <= tp)
        px = why = None
        if hs and ht:
            px, why = (sl, "sl") if abs(O[j]-sl) < abs(O[j]-tp) else (tp, "tp")
        elif hs: px, why = sl, "sl"
        elif ht: px, why = tp, "tp"
        if px is not None:
            r = d * (px - entry) / abs(entry - sl)
            return why, str(T[j])[:16], round(r, 2)
    return "open", None, None


def stats(ledger):
    n = len(ledger)
    w = sum(1 for t in ledger if t["r"] > 0)
    ar = sum(t["r"] for t in ledger) / max(1, n)
    gp = sum(t["r"] for t in ledger if t["r"] > 0)
    gl = -sum(t["r"] for t in ledger if t["r"] < 0)
    pf = gp / gl if gl > 0 else 0
    return dict(trades=n, win=w / max(1, n), avgR=ar, pf=pf)
