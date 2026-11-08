# TLI Dashboard: Scheduled Routine

The scheduled Claude Code routine follows these steps. Keep this file as the
single source of truth: the schedule's prompt only needs to say "follow
ROUTINE.md", so changes here take effect on the next run without editing the
schedule.

The dashboard (`index.html`) is generated from `data.json` when the page
loads. **Never edit `index.html`.** The routine's only job is to keep
`data.json` accurate and push it.

---

## 1. Find new TLI emails

Search Gmail with `label:TLI -label:tli-processed`, newest first, for emails
received since the last run (`meta.lastUpdated` in `data.json`). Skip anything
already labeled `tli-processed`. Leave older backlog emails alone: a newer email
on the same ticker supersedes them.

## 2. For each new email

1. **Ticker and collections** from the subject, format `$TICKER - Collection - …`
   (e.g. `$AMD - TOP 10 & 20 -`). There can be several collection tags.
2. **Extract from the body:**
   - `category`: one of `Buy Zone`, `Watchlist`, `Bull Case`,
     `At Target / Invalidated` (see the docstring in `update_recommendation.py`)
   - `wave`: the Elliott Wave count
   - `entry`: the entry price or zone
   - `target`: the long-term TLI target, if mentioned
   - `summary`: 2–4 sentences in your own words (no verbatim copying)
   - `levels`: 2–4 `{label, value}` pairs for the Key Levels panel
   - `collections`: list of collection names
   - `company`
3. **Analyst data:** follow `SOURCES.md` §2 (IBKR target first, then public
   consensus). Set `analyst`, `analystTarget` and `analystSource`. Use `"N/A"`
   when there is no coverage.
4. Write one JSON object per ticker to a temp file and run
   `python3 update_recommendation.py --file entry.json`.
5. Label the thread `tli-processed` in Gmail.

## 3. Refresh market data for ALL assets (every run, even with no new emails)

1. **Live price for every asset in `data.json`.** IBKR first: `search_contracts`
   → `get_price_snapshot` (`last`, `prior_close`, `top_status`). If IBKR has no
   data for a ticker, fall back through `SOURCES.md` §1 (Yahoo Finance, Google
   Finance, CoinGecko, Kitco/Investing, …).
2. **Analyst rating and target** for assets whose `analystAsOf` is older than
   7 days, or that are new: IBKR target, then public consensus (`SOURCES.md` §2).
3. Write everything to `market.json` (format in the `update_market_data.py`
   docstring), with the actual source name in `livePriceSource` /
   `analystSource`, then run
   `python3 update_market_data.py --file market.json`.
   This recomputes **TLI appreciation = (TLI LT target − live price) / live price**
   for every asset.
4. If IBKR is unreachable, use public sources for everything and say
   "IBKR unavailable" in the commit message and the notification.

## 4. Commit and push

```
git add data.json
git commit -m "Update recommendations $(date)"
git push origin HEAD:main            # main is what the site deploys from
git push -u origin <working-branch>
```

If pushing to `main` is refused, push to the working branch and merge it into
`main`. If `data.json` did not change, don't commit (no empty commits).

## 5. Report

Send one notification only when something happened: how many emails were
processed, which tickers were added or changed category, and the price/analyst
source mix (e.g. "prices: 150 IBKR, 9 fallback"). Send a notification too if
the run failed (Gmail/IBKR access, push refused). Stay silent on runs where
nothing changed.
