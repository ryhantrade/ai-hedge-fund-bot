# FREE DEPOSIT-DATA ROUTE — notes (2026-11-24)

Standalone module: [free_deposit.py](sandbox://workspace/ai-hedge-fund-deploy/free_deposit.py)
Live test: [test_free_deposit.py](sandbox://workspace/ai-hedge-fund-deploy/test_free_deposit.py)
(`python3 test_free_deposit.py`; no key needed — uses the keyless fallback.)

## Verdicts (all verified live 2026-11-24, not from docs alone)

| Provider / type | Verdict |
|---|---|
| **ETH via Etherscan V2** | **PRIMARY — works, needs FREE key.** V1 is DEPRECATED (Aug 2025); keyless V1 returns "deprecated V1 endpoint"; keyless V2 returns "Missing/Invalid API Key". Free tier: 5 calls/sec, 100,000 calls/day, max 3 keys/account. One key covers all EVM chains (chainid=1 = Ethereum). |
| **ETH via Blockchair** | **FALLBACK — works keyless TODAY.** `/dashboards/address` returns recent calls with sender/recipient/value. No signup. Slow (~4–5 s/call on first run; full test 115 s first run, 38 s second run). See the HTTP 430 caveat below. |
| **ETH via Ethplorer "freekey"** | **FALLBACK #2 — works keyless.** `getAddressTransactions?apiKey=freekey` returned real txs. Demo-key limits not published; no pagination documented. |
| **BTC via mempool.space** | **IMPOSSIBLE free.** `/mempool/recent` returns txid/fee/vsize/value ONLY — no addresses. No exchange attribution is possible from it. Large transfers are context only, never a trade trigger. Re-verified 2026-11-24. |
| **SOL via Solscan** | **Needs FREE key + wallets.** Public API dead (404); keyless pro-api returns 401 "Token is missing". Free Level 1 key exists (lower rate limit, core endpoints; exact numbers not published). No SOL exchange wallets verified yet — list is empty on purpose. |
| **CoinMetrics Community API** | **OUT.** Keyless (community-api.coinmetrics.io/v4, 10 req/6s) but exchange-flow metrics (FlowInExchUSD etc.) are PAID-only — live check returns "not supported". (Parent's finding, not re-tested.) |
| **Dune free tier** | **OUT.** Since 10 Sep 2026 free accounts are VIEW-ONLY — running queries / API needs Analyst $65/mo (annual) or $75/mo (monthly). 14-day trial exists. |
| **Arkham free** | **OUT for automation.** Since Oct 2026 free accounts show only the last 100 txs per address/entity; full history is Enterprise $1,500/mo. Intel API is paid (Alpha ~$19.99/mo per one source; prices conflict across sources). USEFUL MANUALLY: Arkham's free website is the best place to find CURRENT exchange hot wallets when refreshing the list. |
| **Whale Alert / CryptoQuant** | Paid fallback only ($29.95/mo / ~$99–109/mo). User will not pay. Do NOT buy. |

## Exact signup steps (user creates the keys — I do not)

### Etherscan — free key (2 minutes, no payment)
1. Go to https://etherscan.io/register — create a free account (username + email + password), confirm the email.
2. Log in, go to https://etherscan.io/myapikey
3. Click **"+ Add"** — a key appears (max 3 keys per account; active within a few minutes).
4. Set it as env var `ETHERSCAN_API_KEY` (on Render: service → Environment → add `ETHERSCAN_API_KEY`).

### Solscan — free Level 1 key (no payment)
1. Go to https://solscan.io/apis — create a free account.
2. API Management in your profile → generate a key.
3. Set it as env var `SOLSCAN_API_KEY`.
4. Paid Lite ($49/mo) is NOT needed. Then add verified SOL wallets to `SOL_WALLETS` in free_deposit.py (pull them from Solscan labels — 100.6M labelled accounts).

## ETH wallet list — verification table (checked 2026-11-24)

"Verified" = Etherscan page title showed the label. Others carry labels
from Etherscan's official landmarks file / a public gist, with activity
checked live via Blockchair. "Last recv" = last incoming tx.

| Exchange | Label | Last recv | Status |
|---|---|---|---|
| Binance | Binance 14 | 2026-11-13 | DORMANT 11d (title verified) |
| Binance | Binance Hot Wallet | 2026-11-06 | DORMANT 18d |
| Binance | Binance 7 | 2026-11-24 00:38 | ACTIVE |
| Binance | Binance 8 | 2026-11-10 | DORMANT 14d |
| Binance | Binance 1 | 2026-11-22 | recent |
| Binance | Binance 5 | 2026-11-22 | recent |
| Binance | Binance 4 | 2026-11-22 | recent |
| Binance | Binance 3 | 2026-10-27 | DORMANT 28d |
| Binance | Binance 2 | 2026-11-22 | recent |
| Coinbase | Coinbase 54 | 2026-08-16 | DORMANT 100d (title verified) |
| Coinbase | Coinbase 10 | — | UNKNOWN (check timed out) |
| Coinbase | Coinbase 2 | 2026-11-03 | DORMANT 21d |
| Coinbase | Coinbase | 2026-11-24 17:41 | ACTIVE (balance 3,753 ETH) |
| OKX | OKX | 2026-11-22 | recent (title verified) |
| Kraken | Kraken 1 | 2026-11-22 | recent, tiny flows (title verified) |
| Kraken | Kraken 2 | 2024-01-23 | DORMANT ~2y |
| Kraken | Kraken 3 | 2026-02-24 | DORMANT 9mo |
| Kraken | Kraken 4 | 2026-11-23 | ACTIVE (balance 180.9 ETH) |
| Bybit | Bybit Hot Wallet | 2026-11-24 16:50 | ACTIVE (balance 13,146 ETH) |

**Excluded — proven wrong, do not re-add:**
- `0x236F233dBf030fD63F9CF8c08Da5e7bd4ed14F55` — landmarks file said
  "OKX Hot Wallet"; Etherscan shows 0 ETH, 0 transactions. Not a wallet.
- `0x28C6c06298d2d1C22918008E1C9AA133A6bB9252` — first "Binance 14"
  guess; Etherscan shows 0 ETH, 0 transactions. Real Binance 14 is
  `0x28C6c06298d514Db089934071355E5743bf21d60`.

**Dormancy warning:** the famous numbered hot wallets from 2021–2024
lists are mostly dormant or dust-only in Nov 2026 — exchanges rotate
wallets, and real deposits now also flow through fresh per-user deposit
addresses (not captured here). A 24h sum over a stale list UNDERCOUNTS
real deposits. The per-wallet table makes that visible, never silent.
Refresh the list from current labels (Etherscan label cloud / Arkham
free website) when inflows look dead.

## ETH test — real numbers (test_free_deposit.py, run twice 2026-11-24)

Provider: Blockchair (keyless; no Etherscan key exists yet).
ETH price $1,682.77 → $1,681.39 (OKX, live); market cap $205.5B (CoinGecko, live).

- **Total 24h deposits: 467.736881 ETH = $786,448–$787,094** (same ETH
  amount both runs — reproducible; $ differs only with the live price).
- **Almost all of it from ONE wallet:** Coinbase
  `0x77134cbC…` — 467.735181 ETH in 20 txs. Every other wallet: 0.000000
  ETH except Binance 7 (0.0017 ETH, 1 tx). Dust is included in the sums
  but is ~$0 and cannot move the total.
- **Expected move: 0.000%** (787k / 205.5B × 100, sensitivity 1.0).
  Status **BELOW_THRESHOLD** — no trade. Correct per the formula.
- **Would-be plan (printed, no trade):** entry $1,682.42, TP $1,682.43,
  SL **$1,661.40 (sweep, swept level $1,664.10)**, risk $1.00 (1% of
  $100), qty 0.047577 ETH, RR 0.0R. The sweep SL works — it found a
  real sweep below $1,664.10.
- BTC mempool: 0 large (≥$1M) transfers at test time. SOL: NO_DATA.

**The threshold math nobody can dodge:** at sensitivity 1.0, ETH
(mcap $205B) needs **$1.64 BILLION of deposits in 24h** to reach the
0.8% signal threshold. Measured: $0.79M — 2,000× short. Even a 100×
undercount would still be 0.04%. So the free ETH route PROVES the
plumbing with real numbers, but it will essentially NEVER produce an
ETH signal. Signals need either (a) small-cap coins — which no free
source covers — or (b) a calibrated sensitivity — which needs paid
historical flow data. Report that honestly; do not raise sensitivity
to force a signal.

## Caveats found while testing

- **Blockchair HTTP 430 on pagination:** 3 wallets (Coinbase 10,
  Coinbase, Bybit Hot) return HTTP 430 on page 2+ (offset > 0) even
  after 1 retry — their sums are first-100-calls only. Possibly an
  anonymous-tier pagination limit. Etherscan (with key) has no such
  limit (10,000 txs/page). The module records the error per wallet.
- **Blockchair is slow:** ~4–5 s/call from this VM; full test took
  115 s (first run) / 38 s (second run). Fine for a 4h scan cycle.
- **txlist covers NORMAL transactions only.** Deposits arriving via
  internal transactions are missed by Etherscan; Blockchair `calls`
  include internal calls (its `transferred` flag filters to value
  transfers).
- **CoinGecko keyless** works (no key) but is rate-limited (~10–30
  calls/min); the module calls it once per check.

## Integration points (for the later wiring step — NOT done now)

Do this AFTER the SMC-fallback agent finishes editing
fund.py / deposit_flow.py / board.py (it is editing those files now).

1. In `deposit_flow.py`: add a provider `free_eth` to `PROVIDERS`
   that calls `free_deposit.check_eth()` and adapts it to the flows
   shape the rest of `check()` expects:
   `{"available": True, "source": "free:<provider>",
   "per_coin": {"ETH": {"inflow_usd": <deposit_usd>, "whale_usd": 0}},
   "mints_usd": 0.0, "tx_count": None}`.
   Then put `"free_eth"` FIRST in `config.json` →
   `deposit_flow.sources_priority` (before `whale_alert`,
   `cryptoquant`) so the free route is tried before any paid source.
   Alternatively, call `free_deposit.check_free()` directly from
   `fund.py` as a separate mode — its top-level shape already matches
   `deposit_flow.check()`.
2. `free_deposit.py` reads NO config file: pass the CFG values
   (`sensitivity`, `min_expected_move_pct`, `max_expected_move_pct`,
   `inflow_direction`, `paper_equity_usd`, `risk_pct`) as arguments
   to `check_eth()` / `check_free()` at integration time.
3. Env vars on Render: `ETHERSCAN_API_KEY` (after the user creates
   it), later `SOLSCAN_API_KEY`.
4. Board `/signals`: no shape change needed — the ranking rows are
   identical to `deposit_flow.check()` rows; the board just needs to
   read the free provider. That wiring is a later step, not this one.
5. SOL: after the key exists, add verified SOL wallets to
   `SOL_WALLETS` and finish `solscan_sol_inflow()` (endpoint and
   mapping are sketched in the code; it is UNTESTED live).
