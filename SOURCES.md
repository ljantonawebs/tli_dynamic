# Data Source Register

Where every number on the dashboard comes from, in priority order. The routine
always tries **IBKR first**. If IBKR returns nothing for a field (no contract,
empty snapshot, no coverage, connector down), it moves down the list for that
field only, and writes the name of the source it actually used into
`data.json`. Each asset carries the fields below, and `meta.marketData` holds a
per-run summary.

| Field in `data.json` | Meaning |
|---|---|
| `livePrice` / `livePriceSource` / `livePriceAsOf` | Last price used to compute TLI appreciation |
| `analyst` / `analystTarget` / `analystSource` / `analystAsOf` | Consensus rating, average 12-month target, where they came from |
| `appr` / `apprBasis` | TLI appreciation %. `live` = (TLI LT target − live price) / live price; `entry` = fallback from TLI entry when no live price exists |

The dashboard also refreshes prices in the browser every 15 minutes from Yahoo
Finance (built into `index.html`). That display is separate from `livePrice`,
which is the routine's snapshot.

---

## 1. Live price

| # | Source | How to get it | Notes |
|---|---|---|---|
| 1 | **IBKR connector** | `search_contracts` (exact `symbol`, primary listing) → `get_price_snapshot` with `last`, `prior_close`, `top_status` | Use `last.price`, or `prior-close` when `last` is null. Status `FROZEN` (market closed) is fine: it is the last close. Crypto: `search_contracts "BTC"` (PAXOS/ZEROHASH). Futures (cocoa, palladium) need the contract's native `exchange`. |
| 2 | Yahoo Finance | `https://finance.yahoo.com/quote/<SYM>` (web search / web fetch) | Same symbol map as `yfSymbol` (e.g. BTC → BTC-USD, NESN → NESN.SW, AML → AML.L, AF → AF.PA, VDY → VDY.TO) |
| 3 | Google Finance | `https://www.google.com/finance/quote/<SYM>:<EXCHANGE>` | Good for non-US listings |
| 4 | CoinGecko / CoinMarketCap | `https://www.coingecko.com/en/coins/<id>` | Crypto only (bitcoin, ethereum, hyperliquid, solana) |
| 5 | Kitco / Investing.com | `https://www.kitco.com`, `https://www.investing.com/commodities/` | Spot silver, palladium, gold, cocoa, copper |
| 6 | Nasdaq / MarketWatch | `https://www.nasdaq.com/market-activity/stocks/<sym>`, `https://www.marketwatch.com/investing/stock/<sym>` | Last resort for US equities |

**Currency rule:** quote the price in the same currency as the TLI target.
AML is in GBp (pence) on the LSE, NESN in CHF, AF in EUR and VDY in CAD.

## 2. Analyst rating and price target

| # | Source | What it gives | How to get it |
|---|---|---|---|
| 1 | **IBKR connector** (target only) | `price_targets.avg/high/low` | `search_earnings_insights` (contract_id, `type: preview`) → `get_earnings_insight` on the newest preview. IBKR does **not** publish a Buy/Hold consensus rating, so the rating always comes from #2 or below. |
| 2 | MarketBeat | Consensus rating (Strong Buy … Sell) and average target | `https://www.marketbeat.com/stocks/<EXCH>/<SYM>/forecast/` |
| 3 | StockAnalysis | Consensus rating and average target | `https://stockanalysis.com/stocks/<sym>/forecast/` |
| 4 | TipRanks | Consensus (Strong Buy / Moderate Buy / Hold …) and average target | `https://www.tipranks.com/stocks/<sym>/forecast` |
| 5 | Yahoo Finance | `recommendationKey`, mean target | `https://finance.yahoo.com/quote/<SYM>/analysis` |
| 6 | Nasdaq / Zacks / MarketScreener | Consensus and target | Non-US listings: MarketScreener |

**When IBKR has a target and the public source has a different one:** use the
IBKR target and record both sources, e.g. `"IBKR (target) + MarketBeat (rating)"`.
An IBKR preview more than about 4 months old counts as stale, so prefer the
public average.

**Rating mapping:** the dashboard only allows `Strong Buy`, `Buy`,
`Moderate Buy`, `Hold`, `Bullish` and `N/A`.
- Outperform / Overweight → Buy
- Neutral / Market Perform / Equal Weight → Hold
- Any Sell rating → Hold (there is no sell bucket)

`update_market_data.py` applies this mapping automatically.

**No coverage:** crypto, commodities, most ETFs and micro-caps with no analysts
get `analyst: "N/A"`, `analystTarget: "N/A"` and `analystSource: "none"`. Never
guess a rating.

## 3. TLI data

TLI sentiment, wave, entry, target, levels and summary come only from the TLI
emails in Gmail (label `TLI`, processed ones get `tli-processed`).

## If IBKR is down

Use rows 2+ for every field and write the actual source names into
`livePriceSource` / `analystSource`. Mention "IBKR unavailable" in the commit
message and in the run notification, so a whole run on fallback data can be
spotted.
