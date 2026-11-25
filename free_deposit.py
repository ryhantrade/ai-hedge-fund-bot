#!/usr/bin/env python3
"""FREE DEPOSIT-FLOW DATA (standalone module, user spec 2026-11-24).

  The deposit-flow scanner needs attributed exchange deposits. Paid
  providers (Whale Alert $29.95/mo, CryptoQuant Pro ~$99-109/mo) are
  the fallback ONLY — the user will not pay. This module implements
  the FREE route, starting with ONE coin (ETH) as a test:

  * ETH — Etherscan API V2 (FREE API key, signup, 5 calls/sec,
    100,000 calls/day) is the PRIMARY source. It polls the public
    hot-wallet addresses of the major exchanges (txlist) and sums
    24h ETH inflows. Keyless calls are REFUSED ("Missing/Invalid
    API Key") — the key must exist in env var ETHERSCAN_API_KEY.
  * ETH — Blockchair (KEYLESS, no signup) is the fallback that works
    TODAY: /dashboards/address returns recent calls with sender /
    recipient / value. Same inflow sum, no key needed.
  * ETH — Ethplorer "freekey" (KEYLESS demo key) is fallback #2.
  * BTC — mempool.space (KEYLESS): /mempool/recent carries NO
    addresses, so NO exchange attribution is possible. Large
    transfers are CONTEXT ONLY, never a trade trigger. (Re-verified
    live 2026-11-24.)
  * SOL — Solscan Pro API v2.0 needs a FREE API key (Level 1, signup
    at solscan.io/apis). Public API is DEAD (404); keyless pro-api
    returns 401 "Token is missing". No SOL exchange wallets are
    verified yet, so SOL is NO_DATA until wallets + key exist.

  WALLET LIST — HONEST WARNING (checked 2026-11-24): the famous
  numbered hot wallets from 2021-2024 lists are mostly DORMANT or
  dust-only now. Exchanges rotate wallets. Four wallets were
  verified by Etherscan page title; the rest carry labels from
  Etherscan's official landmarks file / a public gist, with activity
  checked live via Blockchair on 2026-11-24 (see the "last_seen"
  field per wallet). Two published addresses were WRONG and are
  EXCLUDED (see EXCLUDED_ADDRESSES). A 24h inflow sum over a stale
  list UNDERCOUNTS real deposits — the per-wallet table makes that
  visible instead of silent. Refresh the list from current labels
  (Etherscan label cloud / Arkham free website) when inflows look
  dead.

  LIMITS (honest): txlist covers NORMAL transactions only — deposits
  arriving via internal transactions are missed. Dust/airdrop spam
  (0.0000x ETH) is included in the sum but is ~$0, so it cannot move
  the total. Deposits to FRESH per-user deposit addresses are NOT
  captured — only the hot wallets listed here. The formula and the
  0.8% signal threshold are the same as deposit_flow.py (UNCALIBRATED
  sensitivity 1.0 — the % is the formula's output, not a measured
  forecast).

  This module is STANDALONE (stdlib only, no package imports) so it
  cannot race with other edits. It reads NO config file: defaults
  match config.json (2026-11-24); deposit_flow.py passes its CFG
  values at integration time. Returns the SAME shape as
  deposit_flow.check() so the wiring is a drop-in.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

# ------------------------------------------------------------------
# Defaults (mirror config.json 2026-11-24; overridable via params)
# ------------------------------------------------------------------
LOOKBACK_H = 24
SENSITIVITY = 1.0
MIN_MOVE = 0.8          # min expected_move_pct for a signal
MAX_MOVE = 5.0          # expected_move_pct is capped here
INFLOW_DIRECTION = "long"
EQUITY_USD = 100.0
RISK_PCT = 1.0
SL_ATR_MULT = 1.0       # trade.sl_atr_mult
MIN_SL_PCT = 0.5        # trade.min_sl_pct

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
BLOCKCHAIR = "https://api.blockchair.com/ethereum/dashboards/address"
ETHPLORER = "https://api.ethplorer.io/getAddressTransactions"

# ------------------------------------------------------------------
# ETH exchange hot wallets (Ethereum mainnet, chainid 1 ONLY).
# "verified" = Etherscan page title showed the label on 2026-11-24.
# "last_seen" = last incoming tx per Blockchair, checked 2026-11-24.
# ------------------------------------------------------------------
ETH_WALLETS = [
    {"exchange": "Binance", "label": "Binance 14",
     "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
     "verified": True, "last_seen": "2026-11-13 16:13:59",
     "status": "DORMANT 11d"},
    {"exchange": "Binance", "label": "Binance Hot Wallet",
     "address": "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",
     "verified": False, "last_seen": "2026-11-06 15:00:23",
     "status": "DORMANT 18d"},
    {"exchange": "Binance", "label": "Binance 7",
     "address": "0xdFd5293D8e347dFe59E90eFd55b2956a1343963d",
     "verified": False, "last_seen": "2026-11-24 00:38:59",
     "status": "ACTIVE"},
    {"exchange": "Binance", "label": "Binance 8",
     "address": "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F",
     "verified": False, "last_seen": "2026-11-10 07:57:47",
     "status": "DORMANT 14d"},
    {"exchange": "Binance", "label": "Binance 1",
     "address": "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be",
     "verified": False, "last_seen": "2026-11-22 04:41:47",
     "status": "recent"},
    {"exchange": "Binance", "label": "Binance 5",
     "address": "0xfe9e8709d3215310075d67e3ed32a380ccf451c8",
     "verified": False, "last_seen": "2026-11-22 02:43:59",
     "status": "recent"},
    {"exchange": "Binance", "label": "Binance 4",
     "address": "0x0681d8db095565fe8a346fa0277bffde9c0edbbf",
     "verified": False, "last_seen": "2026-11-22 04:47:11",
     "status": "recent"},
    {"exchange": "Binance", "label": "Binance 3",
     "address": "0x564286362092D8e7936f0549571a803B203aAceD",
     "verified": False, "last_seen": "2026-10-27 00:20:47",
     "status": "DORMANT 28d"},
    {"exchange": "Binance", "label": "Binance 2",
     "address": "0xd551234ae421e3bcba99a0da6d736074f22192ff",
     "verified": False, "last_seen": "2026-11-22 02:45:11",
     "status": "recent"},
    {"exchange": "Coinbase", "label": "Coinbase 54",
     "address": "0xEB2629a2734e272Bcc07BDA959863f316F4bD4Cf",
     "verified": True, "last_seen": "2026-08-16 09:54:59",
     "status": "DORMANT 100d"},
    {"exchange": "Coinbase", "label": "Coinbase 10",
     "address": "0xa9D1e08C7793af67e9d92fe308d5697FB81d3E43",
     "verified": False, "last_seen": None,
     "status": "UNKNOWN (activity check timed out 2026-11-24)"},
    {"exchange": "Coinbase", "label": "Coinbase 2",
     "address": "0x503828976D22510aad0201ac7EC88293211D23Da",
     "verified": False, "last_seen": "2026-11-03 08:16:47",
     "status": "DORMANT 21d"},
    {"exchange": "Coinbase", "label": "Coinbase",
     "address": "0x77134cbC06cB00b66F4c7e623D5fdBF6777635EC",
     "verified": False, "last_seen": "2026-11-24 17:41:47",
     "status": "ACTIVE (balance 3753 ETH)"},
    {"exchange": "OKX", "label": "OKX",
     "address": "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",
     "verified": True, "last_seen": "2026-11-22 06:00:35",
     "status": "recent"},
    {"exchange": "Kraken", "label": "Kraken 1",
     "address": "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
     "verified": True, "last_seen": "2026-11-22 06:05:11",
     "status": "recent (tiny flows)"},
    {"exchange": "Kraken", "label": "Kraken 2",
     "address": "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13",
     "verified": False, "last_seen": "2024-01-23 05:47:23",
     "status": "DORMANT ~2y"},
    {"exchange": "Kraken", "label": "Kraken 3",
     "address": "0xe853c56864a2ebe4576a807d26fdc4a0ada51919",
     "verified": False, "last_seen": "2026-02-24 02:50:47",
     "status": "DORMANT 9mo"},
    {"exchange": "Kraken", "label": "Kraken 4",
     "address": "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",
     "verified": False, "last_seen": "2026-11-23 10:17:47",
     "status": "ACTIVE (balance 180.9 ETH)"},
    {"exchange": "Bybit", "label": "Bybit Hot Wallet",
     "address": "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
     "verified": False, "last_seen": "2026-11-24 16:50:23",
     "status": "ACTIVE (balance 13146 ETH)"},
]

# Published addresses PROVEN WRONG on 2026-11-24 (Etherscan: 0 ETH,
# 0 transactions) — kept here so nobody re-adds them.
EXCLUDED_ADDRESSES = [
    {"address": "0x236F233dBf030fD63F9CF8c08Da5e7bd4ed14F55",
     "claimed": "OKX Hot Wallet (Etherscan landmarks file)",
     "fact": "Etherscan shows 0 ETH, no transactions — not a wallet"},
    {"address": "0x28C6c06298d2d1C22918008E1C9AA133A6bB9252",
     "claimed": "Binance 14 (first guess)",
     "fact": "Etherscan shows 0 ETH, no transactions — wrong address; "
             "real Binance 14 is 0x28C6c06298d514Db089934071355E5743bf21d60"},
]

# SOL exchange wallets: NONE verified yet. Fill from Solscan labels
# (100.6M labelled accounts) once SOLSCAN_API_KEY exists. Do NOT guess.
SOL_WALLETS = []

ETHERSCAN_SIGNUP = (
    "FREE Etherscan API key (2 minutes, no payment):\n"
    "  1. Go to https://etherscan.io/register — create a free account\n"
    "     (username + email + password), confirm the email.\n"
    "  2. Log in, go to https://etherscan.io/myapikey\n"
    "  3. Click '+ Add' — a key appears (max 3 keys per account).\n"
    "  4. Set it as env var ETHERSCAN_API_KEY (Render dashboard:\n"
    "     service -> Environment -> add ETHERSCAN_API_KEY).\n"
    "  Free tier: 5 calls/sec, 100,000 calls/day. One key covers all\n"
    "  EVM chains (V2 API, chainid=1 for Ethereum). V1 is DEPRECATED\n"
    "  (Aug 2025) — this module uses V2 only."
)

SOLSCAN_SIGNUP = (
    "FREE Solscan API key (Level 1, no payment):\n"
    "  1. Go to https://solscan.io/apis — create a free account.\n"
    "  2. API Management in your profile -> generate a key.\n"
    "  3. Set it as env var SOLSCAN_API_KEY.\n"
    "  Free (Level 1) key: lower rate limit, core endpoints only\n"
    "  (exact numbers are not published by Solscan). Paid Lite is\n"
    "  $49/mo — NOT needed for this. Public API is dead (404);\n"
    "  pro-api.solscan.io/v2.0 keyless returns 401 'Token is missing'."
)


def _http_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or
                                 {"User-Agent": "ai-hedge-fund/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_text(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or
                                 {"User-Agent": "ai-hedge-fund/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ------------------------------------------------------------------
# Price / market cap (keyless, verified live 2026-11-24)
# ------------------------------------------------------------------
def eth_price_usd():
    try:
        d = _http_json("https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT")
        return float((d.get("data") or [{}])[0].get("last") or 0)
    except Exception:
        pass
    try:
        d = _http_json("https://api.coingecko.com/api/v3/simple/price"
                       "?ids=ethereum&vs_currencies=usd")
        return float(d["ethereum"]["usd"])
    except Exception:
        return 0.0


def eth_market_cap_usd():
    try:
        d = _http_json("https://api.coingecko.com/api/v3/coins/markets"
                       "?vs_currency=usd&ids=ethereum")
        return float((d or [{}])[0].get("market_cap") or 0)
    except Exception:
        return 0.0


def _okx_candles_1h(inst_id="ETH-USDT", limit=100):
    """OKX 1h candles, oldest -> newest, as {t,o,h,l,c} dicts."""
    d = _http_json("https://www.okx.com/api/v5/market/candles"
                   f"?instId={inst_id}&bar=1H&limit={limit}")
    rows = (d.get("data") or [])
    out = []
    for r in rows:
        out.append({"t": int(r[0]) // 1000, "o": float(r[1]),
                    "h": float(r[2]), "l": float(r[3]), "c": float(r[4])})
    out.sort(key=lambda c: c["t"])
    return out


def _atr(candles, n=14):
    seg = candles[-n:]
    return sum(c["h"] - c["l"] for c in seg) / len(seg)


def _sweep_sl(direction, candles, entry):
    """SL below the sweep — mirrors risk.sweep_below_sl exactly.
    Returns (sl, swept_level, found, rule)."""
    if len(candles) < 13:
        return None, None, False, "no candles"
    a = _atr(candles)
    buf = max(0.15 * a, 0.0004 * entry)
    for i in range(len(candles) - 1, max(0, len(candles) - 24), -1):
        prev = candles[max(0, i - 12):i]
        if not prev:
            continue
        if direction == "long":
            prev_low = min(c["l"] for c in prev)
            if candles[i]["l"] < prev_low and candles[i]["c"] > prev_low:
                sl = candles[i]["l"] - buf
                if sl < entry:
                    return round(sl, 4), round(candles[i]["l"], 4), True, "sweep"
                return None, None, False, "sweep on wrong side of entry"
        else:
            prev_high = max(c["h"] for c in prev)
            if candles[i]["h"] > prev_high and candles[i]["c"] < prev_high:
                sl = candles[i]["h"] + buf
                if sl > entry:
                    return round(sl, 4), round(candles[i]["h"], 4), True, "sweep"
                return None, None, False, "sweep on wrong side of entry"
    return None, None, False, "no recent sweep"


def eth_plan(direction, expected_move_pct, equity_usd=EQUITY_USD,
             risk_pct=RISK_PCT):
    """Entry/TP/SL for the ETH deposit signal. TP comes from the
    deposit calculation ONLY (no 1.5R floor — user spec). SL below
    the sweep, structural fallback when no sweep exists."""
    candles = _okx_candles_1h()
    if not candles:
        return None
    entry = candles[-1]["c"]
    move = min(float(expected_move_pct), MAX_MOVE)
    tp = round(entry * (1 + move / 100.0) if direction == "long"
               else entry * (1 - move / 100.0), 4)
    sl, swept, found, rule = _sweep_sl(direction, candles, entry)
    if not found:
        a = _atr(candles)
        if direction == "long":
            swing = min(c["l"] for c in candles[-20:])
            sl = min(entry - a * SL_ATR_MULT, swing - a * 0.2)
            min_dist = entry * MIN_SL_PCT / 100
            if abs(entry - sl) < min_dist:
                sl = entry - min_dist
        else:
            swing = max(c["h"] for c in candles[-20:])
            sl = max(entry + a * SL_ATR_MULT, swing + a * 0.2)
            min_dist = entry * MIN_SL_PCT / 100
            if abs(entry - sl) < min_dist:
                sl = entry + min_dist
        sl = round(sl, 4)
        rule = "structural (no recent sweep)"
    risk_unit = abs(entry - sl)
    if risk_unit <= 0:
        return None
    risk_usd = round(equity_usd * risk_pct / 100, 2)
    qty = round(risk_usd / risk_unit, 6)
    return {
        "direction": direction, "entry": round(entry, 4),
        "tp": tp, "sl": sl, "sl_rule": rule,
        "swept_level": swept, "risk_per_unit": round(risk_unit, 4),
        "risk_usd": risk_usd, "risk_pct": risk_pct,
        "qty": qty, "notional_usd": round(qty * entry, 2),
        "rr": round(abs(tp - entry) / risk_unit, 2),
        "expected_move_pct": round(move, 3),
    }


# ------------------------------------------------------------------
# ETH inflow providers
# ------------------------------------------------------------------
def etherscan_eth_inflow(hours=LOOKBACK_H, max_pages=3, sleep_s=0.25):
    """PRIMARY. Etherscan API V2 txlist per hot wallet.
    Needs ETHERSCAN_API_KEY. Returns per-wallet + total inflow."""
    key = os.environ.get("ETHERSCAN_API_KEY", "")
    if not key:
        return {"available": False, "source": "etherscan",
                "reason": "ETHERSCAN_API_KEY not set",
                "signup": ETHERSCAN_SIGNUP}
    cutoff = int(time.time()) - hours * 3600
    per_wallet = []
    total_eth = 0.0
    for w in ETH_WALLETS:
        addr = w["address"].lower()
        inflow_wei = 0
        n_txs = 0
        truncated = False
        err = ""
        try:
            for page in range(1, max_pages + 1):
                url = (f"{ETHERSCAN_V2}?chainid=1&module=account"
                       f"&action=txlist&address={w['address']}"
                       "&startblock=0&endblock=99999999"
                       f"&page={page}&offset=10000&sort=desc&apikey={key}")
                d = _http_json(url)
                if str(d.get("status")) != "1":
                    err = str(d.get("result") or d.get("message"))[:120]
                    break
                rows = d.get("result") or []
                if not rows:
                    break
                oldest = None
                for t in rows:
                    ts = int(t.get("timeStamp") or 0)
                    oldest = ts if oldest is None else min(oldest, ts)
                    if ts < cutoff:
                        continue
                    if (t.get("to") or "").lower() != addr:
                        continue
                    if str(t.get("isError")) not in ("0", ""):
                        continue
                    inflow_wei += int(t.get("value") or 0)
                    n_txs += 1
                if oldest is not None and oldest < cutoff:
                    break
                if len(rows) < 10000:
                    break
                if page == max_pages:
                    truncated = True
                time.sleep(sleep_s)
        except Exception as e:
            err = f"request failed: {str(e)[:100]}"
        eth = inflow_wei / 1e18
        total_eth += eth
        per_wallet.append({
            "exchange": w["exchange"], "label": w["label"],
            "address": w["address"], "inflow_eth": round(eth, 6),
            "n_txs": n_txs, "truncated": truncated,
            "error": err, "wallet_status": w["status"]})
    return {"available": True, "source": "etherscan",
            "total_eth": round(total_eth, 6), "per_wallet": per_wallet}


def blockchair_eth_inflow(hours=LOOKBACK_H, max_pages=5, sleep_s=0.4):
    """KEYLESS fallback. Blockchair address dashboards: recent calls
    with sender/recipient/value. One call per wallet (multi-address
    dashboards returns HTTP 400 — verified 2026-11-24). Anonymous
    rate limits are NOT published — if it 429s, use Etherscan."""
    cutoff = int(time.time()) - hours * 3600
    per_wallet = []
    total_eth = 0.0
    for w in ETH_WALLETS:
        addr = w["address"].lower()
        inflow_eth = 0.0
        n_txs = 0
        truncated = False
        err = ""
        try:
            for page in range(max_pages):
                url = (f"{BLOCKCHAIR}/{w['address']}"
                       f"?limit=100&offset={page * 100}")
                d = None
                last_exc = None
                for attempt in range(2):  # 1 retry: Blockchair sometimes
                    try:                   # answers HTTP 430 on a page
                        d = _http_json(url)
                        break
                    except Exception as e:
                        last_exc = e
                        time.sleep(2)
                if d is None:
                    raise last_exc
                node = (d.get("data") or {}).get(addr) or {}
                calls = node.get("calls") or []
                if not calls:
                    break
                oldest = None
                for c in calls:
                    try:
                        ts = int(datetime.strptime(
                            c.get("time", ""), "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=timezone.utc).timestamp())
                    except Exception:
                        continue
                    oldest = ts if oldest is None else min(oldest, ts)
                    if ts < cutoff:
                        continue
                    if (c.get("recipient") or "").lower() != addr:
                        continue
                    if not c.get("transferred"):
                        continue
                    inflow_eth += float(c.get("value") or 0) / 1e18
                    n_txs += 1
                if oldest is not None and oldest < cutoff:
                    break
                if len(calls) < 100:
                    break
                if page == max_pages - 1:
                    truncated = True
                time.sleep(sleep_s)
        except Exception as e:
            err = f"request failed: {str(e)[:100]}"
        total_eth += inflow_eth
        per_wallet.append({
            "exchange": w["exchange"], "label": w["label"],
            "address": w["address"], "inflow_eth": round(inflow_eth, 6),
            "n_txs": n_txs, "truncated": truncated,
            "error": err, "wallet_status": w["status"]})
    return {"available": True, "source": "blockchair",
            "total_eth": round(total_eth, 6), "per_wallet": per_wallet}


def ethplorer_eth_inflow(hours=LOOKBACK_H):
    """KEYLESS fallback #2. Ethplorer demo key 'freekey', one page
    (no pagination documented — may undercount busy wallets)."""
    cutoff = int(time.time()) - hours * 3600
    per_wallet = []
    total_eth = 0.0
    for w in ETH_WALLETS:
        addr = w["address"].lower()
        inflow_eth = 0.0
        n_txs = 0
        truncated = False
        err = ""
        try:
            rows = _http_json(f"{ETHPLORER}/{w['address']}"
                              "?apiKey=freekey&limit=100")
            if isinstance(rows, dict):
                err = str(rows.get("error", {}).get("message", rows))[:120]
                rows = []
            oldest = None
            for t in rows or []:
                ts = int(t.get("timestamp") or 0)
                oldest = ts if oldest is None else min(oldest, ts)
                if ts < cutoff:
                    continue
                if (t.get("to") or "").lower() != addr:
                    continue
                if not t.get("success", True):
                    continue
                inflow_eth += float(t.get("value") or 0)
                n_txs += 1
            if oldest is not None and oldest >= cutoff and len(rows or []) >= 100:
                truncated = True
        except Exception as e:
            err = f"request failed: {str(e)[:100]}"
        total_eth += inflow_eth
        per_wallet.append({
            "exchange": w["exchange"], "label": w["label"],
            "address": w["address"], "inflow_eth": round(inflow_eth, 6),
            "n_txs": n_txs, "truncated": truncated,
            "error": err, "wallet_status": w["status"]})
        time.sleep(0.3)
    return {"available": True, "source": "ethplorer",
            "total_eth": round(total_eth, 6), "per_wallet": per_wallet}


# ------------------------------------------------------------------
# BTC (mempool — context only) and SOL (needs key + wallets)
# ------------------------------------------------------------------
def mempool_large_transfers(min_usd=1_000_000):
    """Keyless mempool.space. Returns large unconfirmed transfers.
    NO addresses in the data — NO exchange attribution possible.
    Context only; NEVER a trade trigger. Re-verified 2026-11-24."""
    try:
        txs = _http_json("https://mempool.space/api/mempool/recent")
        btc_price = 0.0
        try:
            d = _http_json("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")
            btc_price = float((d.get("data") or [{}])[0].get("last") or 0)
        except Exception:
            pass
        out = []
        for t in txs or []:
            usd = float(t.get("value") or 0) / 1e8 * btc_price
            if usd >= min_usd:
                out.append({"txid": t.get("txid"), "value_usd": round(usd, 0)})
        return {"available": True, "n_large": len(out), "txs": out[:10],
                "note": "unattributed — no exchange info in mempool data"}
    except Exception as e:
        return {"available": False,
                "reason": f"mempool.space failed: {str(e)[:100]}"}


def solscan_sol_inflow(hours=LOOKBACK_H):
    """SOL exchange deposits via Solscan Pro API v2.0. Needs a FREE
    Level 1 key (SOLSCAN_API_KEY) AND verified SOL wallets — the
    wallet list is EMPTY until then, so this returns NO_DATA with
    the exact steps. Do not guess wallet addresses."""
    key = os.environ.get("SOLSCAN_API_KEY", "")
    if not key:
        return {"available": False, "source": "solscan",
                "reason": "SOLSCAN_API_KEY not set",
                "signup": SOLSCAN_SIGNUP}
    if not SOL_WALLETS:
        return {"available": False, "source": "solscan",
                "reason": "no verified SOL exchange wallets configured — "
                          "pull them from Solscan labels, add to "
                          "SOL_WALLETS in free_deposit.py, then re-run"}
    # NOTE: implemented for when wallets exist. Endpoint:
    # GET https://pro-api.solscan.io/v2.0/account/transfer
    #   ?address={addr}&page=1&page_size=100, header: token: {key}
    # Rows carry `from`/`to` (base58) and `amount` (lamports) +
    # `block_time` (unix). Sum rows with to == wallet, block_time in
    # window. UNTESTED live (no key, no wallets) — verify before use.
    return {"available": False, "source": "solscan",
            "reason": "SOL provider coded but untested live (no key/wallets)"}


# ------------------------------------------------------------------
# Main checks — same shape as deposit_flow.check()
# ------------------------------------------------------------------
def check_eth(lookback_hours=LOOKBACK_H, sensitivity=SENSITIVITY,
              min_move=MIN_MOVE, max_move=MAX_MOVE,
              direction=INFLOW_DIRECTION, equity_usd=EQUITY_USD,
              risk_pct=RISK_PCT):
    """ETH deposit-flow check over the hot-wallet list.
    Provider order: Etherscan (if key) -> Blockchair -> Ethplorer."""
    provider = None
    if os.environ.get("ETHERSCAN_API_KEY"):
        provider = etherscan_eth_inflow(hours=lookback_hours)
        if not provider.get("available"):
            provider = None
    if provider is None:
        try:
            provider = blockchair_eth_inflow(hours=lookback_hours)
        except Exception as e:
            provider = {"available": False, "source": "blockchair",
                        "reason": f"Blockchair failed: {str(e)[:100]}"}
    if not provider.get("available"):
        try:
            provider = ethplorer_eth_inflow(hours=lookback_hours)
        except Exception as e:
            provider = {"available": False, "source": "ethplorer",
                        "reason": f"Ethplorer failed: {str(e)[:100]}"}
    if not provider.get("available"):
        return {"signal": False, "status": "NO_DATA", "symbol": "ETHUSDT",
                "source": provider.get("source", "none"),
                "reason": provider.get("reason", "no ETH provider worked"),
                "per_wallet": [], "ranking": []}
    price = eth_price_usd()
    mcap = eth_market_cap_usd()
    dep_usd = round(provider["total_eth"] * price, 2) if price else 0.0
    base = {
        "source": provider["source"],
        "deposit_eth": provider["total_eth"],
        "deposit_usd": dep_usd,
        "eth_price_usd": price,
        "market_cap_usd": round(mcap) if mcap else None,
        "per_wallet": provider["per_wallet"],
        "lookback_hours": lookback_hours,
    }
    if not price or not mcap:
        return {**base, "signal": False, "status": "NO_DATA",
                "symbol": "ETHUSDT",
                "reason": "ETH price or market cap unavailable — "
                          "cannot compute deposit/market-cap ratio.",
                "ranking": []}
    move = min(sensitivity * dep_usd / mcap * 100, max_move)
    row = {"symbol": "ETH", "deposit_usd": dep_usd,
           "market_cap_usd": round(mcap),
           "expected_move_pct": round(move, 3), "whale_usd": 0}
    plan = eth_plan(direction, move, equity_usd, risk_pct)
    base["ranking"] = [row]
    base["plan"] = plan
    if move < min_move:
        return {**base, "signal": False, "status": "BELOW_THRESHOLD",
                "symbol": "ETHUSDT", "expected_move_pct": round(move, 3),
                "reason": (f"ETH deposits ${dep_usd:,.2f} vs mcap "
                           f"${mcap:,.0f} -> expected move {move:.3f}% "
                           f"< min {min_move}% — no trade")}
    return {**base, "signal": True, "status": "SIGNAL",
            "symbol": "ETHUSDT", "direction": direction,
            "expected_move_pct": round(move, 3),
            "reason": (f"ETH has the most deposits (free route): "
                       f"${dep_usd:,.2f} vs mcap ${mcap:,.0f} -> "
                       f"expected move {move:.3f}% {direction} "
                       f"({provider['source']}; UNCALIBRATED model)")}


def check_free(lookback_hours=LOOKBACK_H, sensitivity=SENSITIVITY,
               min_move=MIN_MOVE, max_move=MAX_MOVE):
    """Combined free-route check, same top-level shape as
    deposit_flow.check(). ETH is the only coin with free attributed
    data right now; BTC is context-only; SOL needs a key + wallets."""
    eth = check_eth(lookback_hours, sensitivity, min_move, max_move)
    ctx = mempool_large_transfers()
    sol = solscan_sol_inflow(hours=lookback_hours)
    types = {
        "eth_exchange_deposits": {
            "status": "OK" if eth.get("deposit_usd") else "NO_DATA",
            "amount_usd": eth.get("deposit_usd"),
            "source": eth.get("source"),
            "reason": ("" if eth.get("deposit_usd") else eth.get("reason", ""))},
        "btc_exchange_deposits": {
            "status": "NO_DATA", "amount_usd": None,
            "reason": "mempool.space has no addresses — exchange "
                      "attribution impossible free"},
        "whale_deposits": {
            "status": "NO_DATA", "amount_usd": None,
            "reason": "no free whale feed with exchange attribution"},
        "stablecoin_mints": {
            "status": "NO_DATA", "amount_usd": None,
            "reason": "not covered by the free route yet"},
        "other_coin_deposits": {
            "status": "NO_DATA", "amount_usd": None, "coins_covered": 0,
            "reason": "free route covers ETH only so far"},
        "sol_exchange_deposits": {
            "status": "NO_DATA", "amount_usd": None,
            "reason": sol.get("reason", "")},
    }
    out = {
        "signal": eth.get("signal", False),
        "status": eth.get("status", "NO_DATA"),
        "symbol": eth.get("symbol"),
        "direction": eth.get("direction"),
        "expected_move_pct": eth.get("expected_move_pct"),
        "source": "free:" + str(eth.get("source")),
        "reason": eth.get("reason", ""),
        "ranking": eth.get("ranking", []),
        "types": types,
        "per_wallet": eth.get("per_wallet", []),
        "plan": eth.get("plan"),
        "eth_price_usd": eth.get("eth_price_usd"),
        "market_cap_usd": eth.get("market_cap_usd"),
        "mempool_large_transfers": ctx.get("n_large")
            if ctx.get("available") else None,
        "sol": {"status": "NO_DATA", "reason": sol.get("reason", "")},
        "universe": {"top_n": 1, "coins": 1, "source": "free-route (ETH only)"},
    }
    return out


if __name__ == "__main__":
    print(json.dumps(check_free(), indent=2))
