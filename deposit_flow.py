#!/usr/bin/env python3
"""STAGE 0 — DEPOSIT-FLOW SIGNAL (primary signal, user spec 2026-11-24).

  SCOPE: crypto, ALL coins. Universe = CoinGecko top N by market cap
  (config universe_top_n=250). For each coin with deposit data, rank
  by deposits (absolute $). The coin with the MOST deposits is the
  signal coin.

  FORMULA (user spec 2026-11-24, implemented exactly):
      expected_move_pct = sensitivity * deposit_usd / market_cap_usd * 100
  where deposit_usd = that coin's exchange deposits over the lookback
  (default 24h) and market_cap_usd = its CoinGecko market cap.
  Direction: deposits -> price UP (LONG). TP = entry * (1 + expected_move%).
  TP is NOT fixed and has NO 1.5R floor in this scanner (the 1.5R floor
  stays for the old SMC strategy/backtest only). SL = below the sweep
  (risk.sweep_below_sl). Win ratio is the metric that matters.

  WORKED EXAMPLE (real scale, Nov 2026 prices):
    BTC deposits $500M in 24h, BTC mcap ~$1.19T:
      500,000,000 / 1,190,000,000,000 * 100 = 0.042%  (sensitivity 1.0)
    A $500M deposit day moves BTC ~0.04% by this formula — far below
    the 0.8% signal threshold. The ratio only bites on smaller caps:
    a coin with $5M deposits and a $500M mcap -> 1.00%.
    => With sensitivity 1.0 the signal will almost never fire on
    BTC/ETH; it fires on mid/small caps. Sensitivity is the
    calibration knob and it is UNCALIBRATED (no free historical flow
    data exists to fit it). Do not mistake the % for a measured
    forecast.

  DEPOSIT TYPES (combined picture — a type with no data is listed as
  missing, never silently dropped):
    1. btc_exchange_deposits — BTC inflows to exchanges
    2. eth_exchange_deposits — ETH inflows to exchanges
    3. whale_deposits — large (>= $1M) transfers to exchanges, any coin
       (a SUBSET of exchange inflows when from Whale Alert — shown
       separately, never added twice)
    4. stablecoin_mints — USDT/USDC mints (market-wide buying power;
       context only, NOT attributed to any one coin)
    5. other_coin_deposits — per-coin exchange inflows for the rest of
       the universe (coverage depends on provider)

  DIRECTION NOTE: the standard on-chain reading (CryptoQuant etc.) is
  the OPPOSITE — exchange inflows = intent to sell = DOWN pressure.
  This module implements the USER's model (deposits -> UP, long) as
  specified. `inflow_direction` in config flips it to "short" if the
  user wants the standard reading. Only a backtest on real flow
  history can settle which reading wins; no free source provides that
  history, so the question is OPEN.

  DATA-SOURCE HONESTY (checked 2026-11-24, no signup, no key):
  - mempool.space (keyless, TESTED working): /api/mempool/recent
    returns recent txs but NO addresses — exchange attribution is
    impossible from it. Reported as unattributed "large transfers"
    context only; NEVER a trade trigger.
  - Whale Alert API: key required; developer API is PAID (Alerts API
    $29.95/mo, 7-day trial; Enterprise $699/mo). No free API tier.
    30-day history on Alerts API — too short to backtest.
  - CryptoQuant: free Basic plan (10k credits/mo, 30d history) does
    NOT include on-chain data. Exchange flows start at Professional
    (~$99-109/mo, 1y history). Cheapest honest route for live +
    backtest data.
  - Glassnode: no free API tier. API Light on Advanced ($49/mo,
    14 days history, 50 calls/day); full API is a Professional
    add-on (~$584+/mo). Exchange flows are not in the free tier.
  - Arkham Intel API: starts at $100/mo after approval; free trial
    100k credits. Free platform tier shows only the last 100 txs
    per wallet. x402 pay-per-call $0.20/credit.
  - FREE ETH route (free_deposit.py, provider "free_eth", FIRST in
    sources_priority): ETH hot-wallet inflows via Etherscan V2 (free
    key) -> Blockchair (keyless) -> Ethplorer freekey. ETH only; no
    free source covers other coins. Verified live 2026-11-24: ~467.7
    ETH / ~$787k in 24h -> expected move 0.000%, BELOW_THRESHOLD. It
    proves the plumbing with real numbers but will essentially never
    fire an ETH signal (needs $1.64B of deposits for 0.8% at
    sensitivity 1.0).
  => With no source returning data this module returns NO_DATA and the
  scanner holds. That is the correct behaviour, not a bug.
"""
import json
import os
import time
import urllib.request

import free_deposit  # FREE route: ETH hot-wallet inflows (stdlib only)

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "config.json")) as f:
    _FULL_CFG = json.load(f)
CFG = _FULL_CFG.get("deposit_flow", {})
EQUITY_USD = _FULL_CFG.get("paper_equity_usd", 100)
RISK_PCT = _FULL_CFG.get("risk_pct", 1.0)

MIN_FLOW_USD = CFG.get("min_flow_usd", 1_000_000)
WHALE_USD = 1_000_000
LOOKBACK_H = CFG.get("lookback_hours", 24)
MIN_MOVE = CFG.get("min_expected_move_pct", 0.8)
SENSITIVITY = CFG.get("sensitivity", 1.0)
MAX_MOVE = CFG.get("max_expected_move_pct", 5.0)
INFLOW_DIRECTION = CFG.get("inflow_direction", "long")  # "long" (user spec) or "short"
UNIVERSE_N = CFG.get("universe_top_n", 250)
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "WBTC", "STETH"}


def _http_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or
                                 {"User-Agent": "ai-hedge-fund/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _universe():
    """CoinGecko top N by market cap: [{symbol, mcap}]. Cached 15 min
    by the data layer. Returns [] on failure (then no ratio is computable)."""
    try:
        from data import coingecko_markets
        rows = coingecko_markets(top=UNIVERSE_N, min_mcap=0)
        return [{"symbol": r["symbol"], "mcap": r["mcap"]} for r in rows
                if r.get("symbol") and r.get("mcap")]
    except Exception as e:
        print(f"[deposit-flow] universe fetch failed: {str(e)[:100]}")
        return []


def _okx_price_usd(symbol):
    try:
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        d = _http_json(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT")
        return float((d.get("data") or [{}])[0].get("last") or 0)
    except Exception:
        return 0.0


def mempool_large_transfers(min_usd=None):
    """Keyless mempool.space check (TESTED 2026-11-24: works).

    Returns large unconfirmed transfers. NO exchange attribution: the
    /recent endpoint carries no addresses, so these are NOT deposits.
    Context only — never a trade trigger.
    """
    min_usd = min_usd or MIN_FLOW_USD
    try:
        txs = _http_json("https://mempool.space/api/mempool/recent")
        price = _okx_price_usd("BTCUSDT")
        out = []
        for t in txs or []:
            usd = float(t.get("value") or 0) / 1e8 * price
            if usd >= min_usd:
                out.append({"txid": t.get("txid"), "value_usd": round(usd, 0)})
        return {"available": True, "n_large": len(out), "txs": out[:10],
                "note": "unattributed — no exchange info in mempool data"}
    except Exception as e:
        return {"available": False, "reason": f"mempool.space failed: {str(e)[:100]}"}


def _whale_alert_flows():
    """Whale Alert REST (api.whale-alert.io/v1/transactions).

    UNTESTED without a key (developer API is paid: Alerts $29.95/mo).
    Activates only when WHALE_ALERT_API_KEY is set. Response rows carry
    from/to owner_type; only rows with an exchange on the receiving
    side count as deposits. Whale rows (>= $1M to an exchange) are a
    SUBSET of the same inflow — never added twice. Mints are
    transaction_type == "mint" for USDT/USDC (market-wide context).
    """
    key = os.environ.get("WHALE_ALERT_API_KEY", "")
    if not key:
        return {"available": False, "reason": "WHALE_ALERT_API_KEY not set"}
    try:
        end = int(time.time())
        start = end - LOOKBACK_H * 3600
        per_coin = {}
        mints_usd = 0.0
        seen = 0
        cursor = None
        for _ in range(3):  # up to 300 txs per check
            url = ("https://api.whale-alert.io/v1/transactions?api_key=%s"
                   "&min_value=%d&start=%d&end=%d&limit=100"
                   % (key, 100000, start, end))
            if cursor:
                url += f"&cursor={cursor}"
            d = _http_json(url)
            txs = d.get("transactions") or []
            if not txs:
                break
            seen += len(txs)
            for t in txs:
                sym = (t.get("symbol") or "").upper()
                usd = float(t.get("amount_usd") or 0)
                if (t.get("transaction_type") == "mint"
                        and sym in ("USDT", "USDC")):
                    mints_usd += usd
                    continue
                to_ex = (t.get("to") or {}).get("owner_type") == "exchange"
                if not to_ex:
                    continue
                slot = per_coin.setdefault(sym, {"inflow_usd": 0.0, "whale_usd": 0.0})
                slot["inflow_usd"] += usd
                if usd >= WHALE_USD:
                    slot["whale_usd"] += usd
            cursor = d.get("cursor")
            if not cursor:
                break
        return {"available": True, "source": "whale_alert",
                "per_coin": per_coin, "mints_usd": mints_usd, "tx_count": seen}
    except Exception as e:
        return {"available": False, "reason": f"Whale Alert failed: {str(e)[:120]}"}


def _cryptoquant_flows():
    """CryptoQuant exchange flows (api.cryptoquant.com).

    UNTESTED without a key. On-chain data needs the Professional plan
    (~$99-109/mo). Activates only when CRYPTOQUANT_API_KEY is set.
    Covers BTC + ETH only in this client; other coins are reported as
    uncovered by this provider. No whale split, no mints from this API.
    """
    key = os.environ.get("CRYPTOQUANT_API_KEY", "")
    if not key:
        return {"available": False, "reason": "CRYPTOQUANT_API_KEY not set"}
    try:
        headers = {"Authorization": f"Bearer {key}"}
        per_coin = {}
        for asset in ("btc", "eth"):
            d = _http_json(
                f"https://api.cryptoquant.com/v1/{asset}/exchange-flows/"
                f"inflow?window=day&limit=2", headers=headers)
            rows = (d.get("result") or {}).get("data") or []
            if not rows:
                continue
            latest = rows[-1]
            price = _okx_price_usd(asset.upper() + "USDT")
            per_coin[asset.upper()] = {
                "inflow_usd": float(latest.get("value") or 0) * price,
                "whale_usd": 0.0}
        if not per_coin:
            return {"available": False, "reason": "CryptoQuant returned no flow rows"}
        return {"available": True, "source": "cryptoquant",
                "per_coin": per_coin, "mints_usd": 0.0, "tx_count": None}
    except Exception as e:
        return {"available": False, "reason": f"CryptoQuant failed: {str(e)[:120]}"}


def _free_eth_flows():
    """FREE ETH route (free_deposit.py): Etherscan V2 (free key,
    ETHERSCAN_API_KEY) -> Blockchair (keyless) -> Ethplorer freekey.
    Adapts free_deposit.check_eth() to the flows shape check()
    expects. CFG values are passed as arguments — free_deposit.py
    reads no config file. Returns available=False when the free
    route has no data, so the next provider in sources_priority
    is tried. ETH only; no free source covers other coins."""
    try:
        res = free_deposit.check_eth(
            lookback_hours=LOOKBACK_H,
            sensitivity=SENSITIVITY,
            min_move=MIN_MOVE,
            max_move=MAX_MOVE,
            direction=INFLOW_DIRECTION,
            equity_usd=EQUITY_USD,
            risk_pct=RISK_PCT)
    except Exception as e:
        return {"available": False,
                "reason": f"free ETH route failed: {str(e)[:120]}"}
    if res.get("status") == "NO_DATA" or res.get("deposit_usd") is None:
        return {"available": False,
                "reason": res.get("reason", "free ETH route returned no data")}
    return {"available": True,
            "source": "free:" + str(res.get("source", "eth")),
            "per_coin": {"ETH": {"inflow_usd": float(res["deposit_usd"]),
                                 "whale_usd": 0.0}},
            "mints_usd": 0.0, "tx_count": None}


PROVIDERS = {
    "free_eth": _free_eth_flows,
    "whale_alert": _whale_alert_flows,
    "cryptoquant": _cryptoquant_flows,
}


def _type_statuses(flows, universe):
    """Per-type status table — a missing type is listed, never dropped."""
    def amt(sym):
        if not flows:
            return None
        return flows["per_coin"].get(sym, {}).get("inflow_usd")
    btc = amt("BTC")
    eth = amt("ETH")
    whale = None
    if flows:
        whale = sum(v.get("whale_usd") or 0 for v in flows["per_coin"].values())
    others = None
    covered = 0
    if flows:
        covered = sum(1 for sym in flows["per_coin"]
                      if sym not in ("BTC", "ETH") and sym not in STABLES)
        others = sum(v.get("inflow_usd") or 0
                     for s, v in flows["per_coin"].items()
                     if s not in ("BTC", "ETH") and s not in STABLES)
    mints = flows["mints_usd"] if flows else None
    free_src = bool(flows) and str(flows.get("source", "")).startswith("free:")
    no_data = ("free route covers ETH only" if free_src
               else "no provider key configured")
    return {
        "btc_exchange_deposits": {
            "status": "OK" if btc else "NO_DATA",
            "amount_usd": round(btc) if btc else None,
            "reason": "" if btc else no_data},
        "eth_exchange_deposits": {
            "status": "OK" if eth else "NO_DATA",
            "amount_usd": round(eth) if eth else None,
            "reason": "" if eth else no_data},
        "whale_deposits": {
            "status": "OK" if whale else "NO_DATA",
            "amount_usd": round(whale) if whale else None,
            "reason": ("subset of exchange inflows (Whale Alert only)"
                       if whale else ("free route has no whale split"
                                      if free_src
                                      else "no provider key configured"))},
        "stablecoin_mints": {
            "status": "OK" if mints else "NO_DATA",
            "amount_usd": round(mints) if mints else None,
            "reason": ("market-wide context, not per-coin"
                       if mints else ("not covered by the free route yet"
                                      if free_src
                                      else "no provider key configured"))},
        "other_coin_deposits": {
            "status": "OK" if others else "NO_DATA",
            "amount_usd": round(others) if others else None,
            "coins_covered": covered,
            "reason": ("" if others else
                       ("free route covers ETH only (no free source for "
                        "other coins)" if free_src else
                        "no provider key configured (Whale Alert covers 100+ "
                        "assets; CryptoQuant covers BTC/ETH only"))},
    }


def check(cfg=None):
    """Primary deposit-flow check over ALL coins.

    Returns a dict with: signal, status, symbol/direction/
    expected_move_pct (when a signal fires), the full per-coin ranking
    (top 20 by deposits), the per-type status table, and the universe.
    Without a configured source: NO_DATA, no trade — the correct
    behaviour, not a bug.
    """
    cfg = cfg or CFG
    print("[deposit-flow] model UNCALIBRATED (sensitivity %.2f has no "
          "historical calibration — see deposit_flow.py)" % SENSITIVITY)
    universe = _universe()
    flows = None
    for name in cfg.get("sources_priority", ["whale_alert", "cryptoquant"]):
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        res = fn()
        if res.get("available"):
            flows = res
            break
        print(f"[deposit-flow] {name}: {res.get('reason')}")
    types = _type_statuses(flows, universe)
    ctx = mempool_large_transfers()
    base = {
        "types": types,
        "universe": {"top_n": UNIVERSE_N, "coins": len(universe),
                     "source": "coingecko"},
        "mempool_large_transfers": ctx.get("n_large") if ctx.get("available") else None,
        "ranking": [],
    }
    if flows is None:
        return {**base, "signal": False, "status": "NO_DATA", "source": "none",
                "reason": ("No attributed exchange-flow source returned data — "
                           "the free ETH route failed and no paid key is "
                           "configured (WHALE_ALERT_API_KEY $29.95/mo or "
                           "CRYPTOQUANT_API_KEY Professional ~$99-109/mo).")}
    if not universe:
        return {**base, "signal": False, "status": "NO_DATA",
                "source": flows["source"],
                "reason": "Universe (CoinGecko market caps) unavailable — "
                          "cannot compute deposit/market-cap ratios."}
    mcap_by_sym = {r["symbol"]: r["mcap"] for r in universe}
    ranking = []
    for sym, f in flows["per_coin"].items():
        if sym in STABLES:
            continue
        dep = f.get("inflow_usd") or 0
        if dep <= 0:
            continue
        mcap = mcap_by_sym.get(sym)
        if not mcap:
            continue
        move = min(SENSITIVITY * dep / mcap * 100, MAX_MOVE)
        ranking.append({
            "symbol": sym, "deposit_usd": round(dep),
            "market_cap_usd": round(mcap),
            "expected_move_pct": round(move, 3),
            "whale_usd": round(f.get("whale_usd") or 0)})
    ranking.sort(key=lambda r: r["deposit_usd"], reverse=True)
    base["ranking"] = ranking[:20]
    base["source"] = flows["source"]
    if not ranking:
        return {**base, "signal": False, "status": "NO_DATA",
                "reason": "Provider returned flows, but no universe coin "
                          "has usable deposit + market-cap data."}
    top = ranking[0]
    if top["expected_move_pct"] < MIN_MOVE:
        return {**base, "signal": False, "status": "BELOW_THRESHOLD",
                "top": top,
                "reason": (f"top coin {top['symbol']}: deposits "
                           f"${top['deposit_usd']:,} vs mcap "
                           f"${top['market_cap_usd']:,} -> expected move "
                           f"{top['expected_move_pct']}% < min {MIN_MOVE}%")}
    return {**base, "signal": True, "status": "SIGNAL",
            "symbol": top["symbol"] + "USDT",
            "direction": INFLOW_DIRECTION,
            "expected_move_pct": top["expected_move_pct"],
            "top": top,
            "reason": (f"{top['symbol']} has the most deposits: "
                       f"${top['deposit_usd']:,} vs mcap "
                       f"${top['market_cap_usd']:,} -> expected move "
                       f"{top['expected_move_pct']}% {INFLOW_DIRECTION} "
                       f"({flows['source']}; UNCALIBRATED model)")}


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
