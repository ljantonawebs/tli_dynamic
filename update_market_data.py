#!/usr/bin/env python3
"""
update_market_data.py

Merges live prices and analyst consensus data into data.json, recording the
SOURCE of every value so the dashboard data can be audited. Run it after the
email entries have been merged with update_recommendation.py.

Usage:
    python3 update_market_data.py --file market.json
    python3 update_market_data.py --recompute      # only recompute appr

market.json is a JSON list (or {"items": [...]}) of objects:
    {
      "sym": "AAPL",
      "livePrice": 342.65,                  # number, in the asset's quote currency
      "livePriceSource": "IBKR",            # see SOURCES.md for allowed names
      "livePriceAsOf": "2026-11-08T12:00Z", # optional, defaults to now
      "analyst": "Buy",                     # Strong Buy|Buy|Moderate Buy|Hold|Bullish|N/A
      "analystTarget": "$318.85",           # string, or "N/A"
      "analystSource": "IBKR (target) + MarketBeat (rating)",
      "analystAsOf": "2026-11-08"           # optional
    }
Any field may be omitted; omitted fields keep their current value.

What the script does:
  - writes the given fields onto the matching asset (unknown syms are reported
    and skipped — new assets only come from TLI emails)
  - normalises ratings to the dashboard's allowed set (anything else -> "N/A")
  - recomputes `appr` = TLI appreciation from the CURRENT LIVE PRICE to the TLI
    long-term target. Falls back to entry->target only when no live price exists
  - re-derives analystClass / rec / recClass
  - stores a per-run summary of sources used in meta.marketData
  - never touches index.html
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_recommendation as ur  # noqa: E402

ALLOWED_RATINGS = {"Strong Buy", "Buy", "Moderate Buy", "Hold", "Bullish", "N/A"}
RATING_ALIASES = {
    "strong buy": "Strong Buy",
    "outperform": "Buy",
    "overweight": "Buy",
    "buy": "Buy",
    "moderate buy": "Moderate Buy",
    "accumulate": "Moderate Buy",
    "hold": "Hold",
    "neutral": "Hold",
    "market perform": "Hold",
    "equal weight": "Hold",
    # the dashboard has no sell bucket: closest allowed value is Hold
    "moderate sell": "Hold",
    "underperform": "Hold",
    "underweight": "Hold",
    "sell": "Hold",
    "strong sell": "Hold",
    "bullish": "Bullish",
    "n/a": "N/A",
    "na": "N/A",
    "none": "N/A",
    "no coverage": "N/A",
    "": "N/A",
}

MARKET_FIELDS = [
    "livePrice", "livePriceSource", "livePriceAsOf",
    "analyst", "analystTarget", "analystSource", "analystAsOf",
]

_PRICE_RE = re.compile(r"[$€£]\s?([\d,]+(?:\.\d+)?)\s*([kK])?")
_LEADING_NUM_RE = re.compile(r"^\s*~?\s*(\d[\d,]*(?:\.\d+)?)(?![\d.,])\s*([kK])?(?!\s*(?:fib|day|week|wma|dma|ma\b|%|x\b))", re.I)


def parse_target(s):
    """Price from a free-text TLI target. Requires a currency sign, or a number
    at the very start of the string, so text like 'Resistance at 200 Day MA'
    does not turn into a $200 target."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = _PRICE_RE.search(s) or _LEADING_NUM_RE.search(s)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):
        v *= 1000
    return v or None


def normalise_rating(r):
    if r is None:
        return "N/A"
    r = str(r).strip()
    if r in ALLOWED_RATINGS:
        return r
    return RATING_ALIASES.get(r.lower(), "N/A")


def compute_live_appr(asset):
    target = parse_target(asset.get("target"))
    price = asset.get("livePrice")
    if isinstance(price, str):
        price = parse_target(price)
    if target and price:
        ratio = target / price
        # e.g. GLD/PALL ETF price vs a TLI target quoted in spot $/oz: the two
        # are in different units, so a % would be meaningless
        if ratio > 15 or ratio < 1 / 15:
            return None, "unit-mismatch"
        pct = round((target - price) / price * 100)
        return f"{'+' if pct >= 0 else ''}{pct}%", "live"
    legacy = ur.compute_appr(asset.get("entry"), asset.get("target"))
    return legacy, ("entry" if legacy else None)


def apply_derived(asset):
    asset["analyst"] = normalise_rating(asset.get("analyst"))
    if not asset.get("analystTarget") or str(asset["analystTarget"]).strip() in ("None", "—", "-"):
        asset["analystTarget"] = "N/A"
    asset["appr"], asset["apprBasis"] = compute_live_appr(asset)
    asset["analystClass"] = ur.ANALYST_TO_CLASS.get(asset["analyst"], "analyst-none")
    asset["recClass"], asset["rec"] = ur.rec_for(asset.get("category"), asset["analyst"])


def load_items(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw["items"] if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        print("ERROR: market file must be a list or {'items': [...]}", file=sys.stderr)
        sys.exit(1)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="JSON file with market data items")
    ap.add_argument("--recompute", action="store_true",
                    help="only recompute appr/derived fields for every asset")
    args = ap.parse_args()
    if not args.file and not args.recompute:
        ap.error("pass --file or --recompute")

    data = ur.load_data()
    by_sym = {a["sym"]: a for a in data["assets"]}
    now = datetime.now(timezone.utc).isoformat()
    updated, unknown = [], []

    if args.file:
        for it in load_items(args.file):
            sym = str(it.get("sym", "")).upper().strip()
            asset = by_sym.get(sym)
            if not asset:
                unknown.append(sym)
                continue
            for f in MARKET_FIELDS:
                if f in it and it[f] is not None:
                    asset[f] = it[f]
            if "livePrice" in it and "livePriceAsOf" not in it:
                asset["livePriceAsOf"] = now
            if "analyst" in it and "analystAsOf" not in it:
                asset["analystAsOf"] = now[:10]
            updated.append(sym)

    for a in data["assets"]:
        apply_derived(a)

    def src_bucket(s):
        s = (s or "none").strip()
        return s.split(" ")[0] if s.upper().startswith("IBKR") else s

    data["meta"]["marketData"] = {
        "updatedAt": now,
        "assetsUpdatedThisRun": len(updated),
        "priceSources": dict(Counter(src_bucket(a.get("livePriceSource")) for a in data["assets"])),
        "analystSources": dict(Counter((a.get("analystSource") or "none") for a in data["assets"]).most_common(12)),
        "apprBasis": "live price -> TLI long-term target (entry -> target only when no live price)",
        "register": "SOURCES.md",
    }
    ur.save_data(data)
    print(f"OK: updated {len(updated)} assets"
          + (f"; unknown syms skipped: {', '.join(unknown)}" if unknown else ""))
    missing = [a["sym"] for a in data["assets"] if not a.get("livePrice")]
    if missing:
        print(f"WARNING: no live price for {len(missing)}: {', '.join(missing)}")


if __name__ == "__main__":
    main()
