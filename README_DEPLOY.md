# AI Hedge Fund — Board (deploy package)

Paper-trading-only crypto + gold scanner with a web dashboard.
**No real money, no broker keys, no live orders, no Telegram.**

## What it is

| Part | What it does |
|---|---|
| `board.py` | Flask dashboard + background scan loop (every 4h). Shows live scan status, deposit-flow readings, signals, open/closed paper trades, ledger stats, gold signals. Includes the **/signals page**: per-coin deposit-flow ranking with progress bars for expected upside % and deposit flow, sorted by expected upside, signal (entry/TP/SL) for coins that pass, auto-refresh 120s. |
| `fund.py` | Crypto pipeline: close-check → deposit-flow signal → SMC+Kronos committee confirmation → 1%-risk paper trade. **TP from the deposit calc (varies per trade, NO 1.5R floor for deposit trades)**; **SL below the sweep** (user decision 2026-11-24). The 1.5R floor stays for the old SMC strategy/backtest only. |
| `deposit_flow.py` | PRIMARY signal, crypto ALL coins (CoinGecko top 250 by mcap): per-coin exchange deposits → expected move % = sensitivity × deposit_usd / market_cap_usd × 100 → LONG on the coin with the most deposits. Multi-type: BTC/ETH/whale/stablecoin-mint/other-coin deposits, each listed with status (missing types shown, never dropped). **Currently NO_DATA until a paid key is set** (see below) — the scanner holds meanwhile. |
| `gold_strategy.py` | Gold (XAUUSD via PAXGUSDT) on the mechanical SMC playbook (4H BOS → premium/discount → 4H POI → 1H align → 15m sweep/shift/pullback). Own strategy — deposit-flow does NOT apply to gold. **Independent-search candidate only** (best backtested 42.9% win, n=7, 2y GC=F 1h); the user's playlist strategy is pending — see `gold_bot.py`. |
| `gold_bot.py` + `gold_config.json` | **SEPARATE Gold bot** (own entry point, own config, own ledger `data/gold_paper_trades.jsonl`). Currently PENDING — strategy comes from the user's YouTuber playlist, link not yet received (2026-11-24); the runner exits cleanly and takes NO trades until then. |
| `ledger.py` | Paper ledger (`data/paper_trades.jsonl`, or `$LEDGER_PATH` on the Render disk). |
| `git_persist.py` | Fallback only: pushes the ledger back to git when there is no disk and `GITHUB_PAT` is set. |

## Deposit & risk (user-set, do not change silently)

- Deposit **$100**, risk **1% per trade ($1)**, TP floor **1.5R — for the old SMC strategy only**.
- **Deposit-flow formula (user spec 2026-11-24):** `expected_move_pct = sensitivity × deposit_usd / market_cap_usd × 100`, computed for the coin with the MOST deposits (absolute $) in the CoinGecko top-250 universe. **TP = entry × (1 + expected_move%)** — varies per trade, NO 1.5R floor for deposit trades. Worked example: BTC $500M deposits / $1.19T mcap = 0.042% (sensitivity 1.0) — below the 0.8% min; a $5M-deposit coin on a $500M mcap = 1.00%. Direction: deposits → UP (LONG); `inflow_direction` in config flips it to "short" (the standard on-chain reading is the opposite — inflows = sell pressure — and only a backtest on real flow history can settle which wins; no free source provides that history).
- **SL = below the sweep** (user decision 2026-11-24; settles the 2026-10-17 SL-at-sweep vs SL-below-OB question — user-stated comparison: SL-at-sweep 92% win, +3.5% on BTC 4H/15m vs 58% / -4.3% below-OB). Risk sizing: risk $ = entry-to-sweep-SL distance, 1% of the $100 deposit. If no recent sweep exists, the structural swing/ATR SL stands (recorded on the trade as `sl_rule`).
- The deposit-flow sensitivity is **UNCALIBRATED** (no historical flow data exists free). Treat the expected-move % as a hypothesis, not a measured forecast. **Win rate is the metric for the deposit scanner — do NOT promise 100%.**

## Deposit-flow data — the honest status (checked 2026-11-24)

No free source provides attributed bulk-deposit (exchange-flow) data:

| Source | Status |
|---|---|
| mempool.space (keyless, tested) | Works, but returns no addresses → no exchange attribution. Context only, never a trade trigger. |
| Whale Alert API | Paid: Alerts API $29.95/mo (7-day trial); Enterprise $699/mo. No free API tier. 30-day history — too short to backtest. |
| CryptoQuant | Free Basic has NO on-chain data. Exchange flows start at Professional ~$99–109/mo (1y history). Cheapest honest route for live + backtest. |
| Glassnode | No free API tier. API Light $49/mo (14 days history); full API ~$584+/mo. |
| Arkham Intel API | Starts at $100/mo after approval; trial 100k credits. |

Set `WHALE_ALERT_API_KEY` or `CRYPTOQUANT_API_KEY` as a Render env var to activate the signal. Until then the board runs, closes open trades, and holds.

## Gold backtest — run it yourself

```bash
python gold_strategy.py   # prints all 4 variants; writes data/gold_backtest.json
```

The 80% win-rate target was **not reached** by any variant in the measured run (see `data/gold_backtest.json` for the real numbers with sample sizes). Do not claim 80%+ without a backtest behind it.

## Gold bot — separate, PENDING playlist

The Gold strategy gets its **own separate bot** (`gold_bot.py`, `gold_config.json`, ledger `data/gold_paper_trades.jsonl`) — not the crypto deposit-flow bot. Its strategy comes from a YouTuber playlist the user says he shared; **the link has not arrived yet** (requested 2026-11-24 23:27 +06). Until it arrives: watch ALL videos, learn the strategy exactly as taught, backtest it on XAUUSD (win rate, total R, max DD, trade count, with sample size), and only then build the bot on it. The user also says he found a gold strategy himself — its rules (entry/SL/TP/timeframe) are also pending; backtest those the same way when they arrive. If either set of rules is vague or incomplete, list exactly what is missing instead of guessing. Do NOT assume the playlist is the Brad Goh Market Mechanics playlist unless the link says so. Until then `gold_bot.py` exits cleanly and takes no trades; the board's gold section stays display-only.

## Monthly expectation — the honest math (user target: >$4,000/month; DO NOT promise it)

- At $100 deposit and 1% risk, each trade risks **$1**. Even at a 100% win rate with 1.5R TP, each win is $1.50 — $4,000/month would need ~2,667 winning trades a month. The bot will never take that many trades.
- Measured SMC backtest (this window, 60d): 10-coin **+15.2R / 60 days = ~+7.6R/month → ~+$7.60/month** at $1 risk; majors +7.62R / 60d → ~+$3.81/month. The pooled 20-coin figure relayed earlier (+12.61R / 60d) implies ~+$6.30/month. The deposit-flow backtest cannot be run (no free historical flow data), so its R/month is **unknown**.
- Deposit needed for $4,000/month at the measured ~7.6R/month: $4,000 ÷ 7.6 ≈ $526 risk per trade → at 1% risk, a deposit of **~$53,000**. At the theoretical best (12R/month, 8 trades × 1.5R, 100% win): ~$33,000 deposit. **$4,000/month cannot be promised on $100.**

## Deploy (Render Premium, ~$7.25/mo)

1. Push this directory to a **new GitHub repo** (suggested: `ryhantrade/ai-hedge-fund-bot`).
2. In Render: New → Blueprint → point at the repo (uses `render.yaml`), or create the web service manually with the same settings: Starter plan, disk 1 GB at `/data`, env vars from `render.yaml`.
3. The board comes up at the Render URL; the first scan runs ~10s after boot, then every 4h.

## Local test

```bash
~/workspace/kronos/TradingAgents/.venv/bin/python fund.py --backtest   # crypto SMC backtest
~/workspace/kronos/TradingAgents/.venv/bin/python gold_strategy.py     # gold backtest
~/workspace/kronos/TradingAgents/.venv/bin/python fund.py --symbol BTCUSDT --no-llm --paths 5
~/workspace/kronos/TradingAgents/.venv/bin/python board.py            # board on :10000
```

## Repo contents & sizes

- Total ~31 MB. Largest files: `models/mini/model.safetensors` (16 MB) and `models/mini-tok/model.safetensors` (16 MB) — both under GitHub's 100 MB file limit.
- No secrets, no `.env`, no credentials anywhere. Torch (~800 MB) is installed at build time, not shipped.
- `repos/` (1.3 GB of reference clones from the original project) is deliberately NOT included.
