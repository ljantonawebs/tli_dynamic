# Data Source Register

Where every number on the dashboard comes from, in priority order. The routine
always tries **IBKR first**. For live prices, if IBKR returns nothing (no
contract, empty snapshot, connector down), it moves down the list for that
field only. Analyst ratings and targets are **IBKR only** (§2), with no
fallback. The routine writes the name of the source it actually used into
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

## 2. Analyst rating and price target: IBKR research only

The **Analyst** and **Analyst Target** columns come **only** from IBKR
research. No public consensus sites (MarketBeat, StockAnalysis, TipRanks,
Yahoo, Zacks, MarketScreener, …) and no figures quoted in TLI emails. If IBKR
has nothing for a field, that field is `"N/A"`. Never guess a value.

### 2a. Analyst Target (`analystTarget`)

`search_contracts` (primary listing) → `search_earnings_insights`
(`contract_ids: [id]`, `type: "preview"`, `size: 1`) → `get_earnings_insight`
on the newest preview → `price_targets.avg`.

- Use the newest preview **that contains `price_targets`**, whatever its age,
  and put its date in the source, e.g. `IBKR (target, preview 2026-11-15)`.
  IBKR often lists a newer placeholder preview with no reporting date and no
  body. Skip those. `search_earnings_insights` sorts by reporting date, so
  ask for a few results (`size: 3–5`, or several contract IDs at once) and
  take the newest one that has targets.
- No preview, or no `price_targets` in it: `analystTarget: "N/A"`.
- Format it in the listing currency (`$167.24`, `€12.89`, `1,250p`).

### 2b. Analyst consensus rating (`analyst`)

IBKR earnings previews don't carry a rating. The IBKR screener does: its
**Average Rating** field runs from 1 (Strong Buy) to 5 (Strong Sell). The
screener returns rankings, not values, so the rating is read by filtering:

1. `preview_screener` with `selector: "STK"`, the asset's exchange location
   (e.g. `STK.NYSE`, `STK.NASDAQ`; from `get_screener_capabilities`), scanner
   `SCAN_avgRating_ASC`, and filters that isolate the stock:
   `priceAbove` / `priceBelow` = live price ±1%, plus `marketCapAbove1e6`
   when needed. Confirm the asset's `contract_id` is in the results.
2. Repeat with an `avgRatingBelow` filter to find the bucket (2–3 calls):
   - `avgRatingBelow 2.0`: if the stock is still there, check `1.5`.
     Below 1.5 → **Strong Buy**, otherwise → **Buy**.
   - Otherwise `avgRatingBelow 2.5`: if the stock is there → **Moderate Buy**.
   - Otherwise `avgRatingBelow 6`: if the stock is there → **Hold** (2.5 and
     up; the dashboard has no sell bucket). If it is gone, it has no rating → `N/A`.
3. Record it as `IBKR (avg rating 2.0–2.5)`. The full source string is
   e.g. `IBKR (rating: avg 2.0–2.5; target: preview 2026-11-15)`.

Rating buckets on IBKR's 1–5 scale:

| IBKR Average Rating | Dashboard `analyst` |
|---|---|
| below 1.5 | Strong Buy |
| 1.5 – 2.0 | Buy |
| 2.0 – 2.5 | Moderate Buy |
| 2.5 and above | Hold |
| not rated | N/A |

Non-US listings: use the matching screener selector or location from
`get_screener_asset_classes`. If IBKR has no screener coverage for the
listing, the rating is `N/A`.

**No IBKR coverage:** crypto, commodities, most ETFs and micro-caps get
`analyst: "N/A"`, `analystTarget: "N/A"`, `analystSource: "none"`.

**IBKR unreachable:** leave the existing analyst fields as they are. Don't
fall back to public sites. Say "IBKR unavailable" in the run report.


## 3. TLI data

TLI sentiment, wave, entry, target, levels and summary come only from the TLI
emails in Gmail (label `TLI`, processed ones get `tli-processed`).

## If IBKR is down

Live prices: use rows 2+ of §1 and write the actual source name into
`livePriceSource`. Analyst fields: keep the existing values, with no public
fallback (§2). Mention "IBKR unavailable" in the commit
message and in the run notification, so a whole run on fallback prices can be
spotted.
