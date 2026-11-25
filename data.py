#!/usr/bin/env python3
"""Market data: public, keyless sources only.

- Candles: ccxt Binance public OHLCV (no API key). Fallback: Kraken public
  REST via stdlib urllib (no key). Results cached on disk (data/cache/).
- CoinGecko: /coins/markets (free, no key) for the scanner + research.
"""
import json
import os
import time
import urllib.request

_BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_BASE, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"

# Binance symbol -> Kraken pair (public OHLC endpoint)
KRAKEN_PAIRS = {
    "BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD",
    "BNBUSDT": "BNBUSD", "XRPUSDT": "XRPUSD", "DOGEUSDT": "XDGUSD",
    "ADAUSDT": "ADAUSD", "LINKUSDT": "LINKUSD", "AVAXUSDT": "AVAXUSD",
    "DOTUSDT": "DOTUSD", "LTCUSDT": "LTCUSD", "NEARUSDT": "NEARUSD",
}


def _http_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "ai-hedge-fund/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cache_get(key, ttl):
    path = os.path.join(CACHE_DIR, key + ".json")
    try:
        with open(path) as f:
            blob = json.load(f)
        if time.time() - blob["ts"] < ttl:
            return blob["data"]
    except Exception:
        pass
    return None


def _cache_put(key, data):
    path = os.path.join(CACHE_DIR, key + ".json")
    try:
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception:
        pass


def _ccxt_exchange(name):
    """Plain ccxt exchange. NOTE: on this VM ccxt's requests-based transport
    cannot get through the egress proxy (SSL WRONG_VERSION_NUMBER), so
    fetch_candles falls back to raw Kraken REST, which works. On a normal
    VPS/server ccxt works directly and is tried first."""
    import ccxt
    return getattr(ccxt, name)({"enableRateLimit": True})


def _okx_candles(symbol, timeframe, limit):
    """OKX public candles (no key). Paginates with `after` to get deep history.
    (OKX's `after` returns candles OLDER than the given timestamp.)
    Returns oldest-first [{t,o,h,l,c,v}]."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    inst = f"{base}-USDT"
    bar = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1H", "4h": "4H", "1d": "1D"}.get(timeframe, "1H")
    all_rows = []
    after = None
    try:
        for _ in range(21):  # 21x300 = 6300 bars max (60d of 15m needs 6020)
            url = (f"https://www.okx.com/api/v5/market/candles?instId={inst}"
                   f"&bar={bar}&limit=300")
            if after:
                url += f"&after={after}"
            data = _http_json(url)
            rows = data.get("data") or []
            if not rows:
                break
            all_rows.extend(rows)
            if len(all_rows) >= limit:
                break
            oldest_ts = int(rows[-1][0])
            if after is not None and oldest_ts >= after:
                break
            after = oldest_ts
            if len(rows) < 300:
                break
    except Exception as e:
        print(f"  [data] OKX failed for {symbol}: {str(e)[:120]}")
        return []
    if not all_rows:
        return []
    seen = {}
    for r in all_rows:
        seen[int(r[0])] = r
    rows = [seen[k] for k in sorted(seen)][-limit:]
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]


def fetch_candles(symbol, timeframe="1h", limit=600):
    """Return list of {t,o,h,l,c,v} dicts (oldest first).

    Tries, in order: ccxt Kraken/Binance (works on a normal VPS; both are
    blocked from this VM's egress), OKX public REST with pagination
    (works here, deep history, no key), Kraken public REST (works here,
    but capped at 720 most-recent bars). All public, no API key.
    """
    symbol = symbol.upper()
    key = f"candles_{symbol}_{timeframe}_{limit}"
    hit = _cache_get(key, 300)
    if hit:
        return hit
    candles = None
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    for ex_name in ("kraken", "binance"):
        try:
            ex = _ccxt_exchange(ex_name)
            pair = f"{base}/USD" if ex_name == "kraken" else f"{base}/USDT"
            raw = ex.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
            candles = [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
                        "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in raw]
            break
        except Exception as e:
            print(f"  [data] ccxt/{ex_name} failed for {symbol}: {str(e)[:110]}")
    if not candles:
        candles = _okx_candles(symbol, timeframe, limit)
    if not candles:
        candles = _kraken_candles(symbol, timeframe, limit)
    if candles:
        _cache_put(key, candles)
    return candles or []


def _kraken_candles(symbol, timeframe, limit):
    pair = KRAKEN_PAIRS.get(symbol)
    if not pair:
        return []
    interval = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                "1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    # Kraken returns max 720 candles per call. To get `limit` bars, page
    # FORWARD from (now - limit*interval): each call returns up to 720
    # candles starting at `since`, so advance `since` past the last bar.
    all_rows = []
    now = int(time.time())
    since = now - limit * interval * 60
    try:
        for _ in range(8):
            url = f"{KRAKEN_OHLC}?pair={pair}&interval={interval}&since={since}"
            data = _http_json(url)
            result = data["result"]
            rows = None
            for k, v in result.items():
                if k != "last":
                    rows = v
                    break
            if not rows:
                break
            all_rows.extend(rows)
            if len(all_rows) >= limit:
                break
            last_ts = int(rows[-1][0])
            if last_ts + interval * 60 >= now - interval * 60:
                break  # reached the present
            since = last_ts + interval * 60
    except Exception as e:
        print(f"  [data] Kraken failed for {symbol}: {str(e)[:120]}")
        return []
    if not all_rows:
        return []
    # de-dup by timestamp, sort, take the last `limit`
    seen = {}
    for r in all_rows:
        seen[int(r[0])] = r
    rows = [seen[k] for k in sorted(seen)][-limit:]
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[6])} for r in rows]


def coingecko_markets(top=250, min_mcap=0):
    """Top coins by market cap from CoinGecko (free endpoint, no key)."""
    key = f"cg_markets_{top}"
    hit = _cache_get(key, 900)
    if hit:
        return hit
    coins = []
    pages = max(1, (top + 249) // 250)
    for page in range(1, pages + 1):
        try:
            params = ("vs_currency=usd&order=market_cap_desc&per_page=250&page=%d"
                      "&sparkline=false&price_change_percentage=1h,24h,7d" % page)
            coins.extend(_http_json(CG_MARKETS + "?" + params))
        except Exception as e:
            print(f"  [data] CoinGecko page {page} failed: {str(e)[:100]}")
            break
        time.sleep(2)
    rows = []
    for c in coins:
        try:
            mcap = float(c.get("market_cap") or 0)
            vol = float(c.get("total_volume") or 0)
            if mcap < min_mcap or vol <= 0:
                continue
            rows.append({
                "id": c.get("id"), "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"), "price": float(c.get("current_price") or 0),
                "mcap": mcap, "vol24h": vol, "vol_mcap": vol / mcap if mcap else 0,
                "pct_1h": float(c.get("price_change_percentage_1h_in_currency") or 0),
                "pct_24h": float(c.get("price_change_percentage_24h_in_currency") or 0),
                "pct_7d": float(c.get("price_change_percentage_7d_in_currency") or 0),
                "ath": float(c.get("ath") or 0),
                "ath_change_pct": float(c.get("ath_change_percentage") or 0),
                "mcap_rank": int(c.get("market_cap_rank") or 0),
            })
        except (TypeError, ValueError):
            continue
    _cache_put(key, rows)
    return rows


def coingecko_coin(coin_id):
    """Detail for one coin (used by research): market data + description snippet."""
    key = f"cg_coin_{coin_id}"
    hit = _cache_get(key, 3600)
    if hit:
        return hit
    try:
        d = _http_json(f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                       "?localization=false&tickers=false&community_data=false"
                       "&developer_data=false&sparkline=false")
        md = d.get("market_data", {})
        out = {
            "id": coin_id,
            "name": d.get("name"),
            "description": (d.get("description", {}).get("en") or "")[:600],
            "categories": d.get("categories", []),
            "homepage": (d.get("links", {}).get("homepage") or [""])[0],
            "mcap_change_24h_pct": md.get("market_cap_change_percentage_24h"),
            "total_supply": md.get("total_supply"),
            "circulating_supply": md.get("circulating_supply"),
            "max_supply": md.get("max_supply"),
        }
        _cache_put(key, out)
        return out
    except Exception as e:
        print(f"  [data] CoinGecko detail failed for {coin_id}: {str(e)[:100]}")
        return {}
