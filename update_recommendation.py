#!/usr/bin/env python3
"""
update_recommendation.py

Merges ONE newly-extracted stock recommendation into data.json.
Called by the Cowork scheduled task once per parsed email / ticker.

Usage:
    python3 update_recommendation.py --file entry.json
    # or
    python3 update_recommendation.py --json '{"sym": "AAPL", ...}'

entry.json / --json must contain at minimum:
    sym, company, sentiment, wave, entry, target, analyst, analystTarget,
    summary, collections (list), levels (list of {label, value})

The script:
  - looks up any existing record for `sym`
  - if the sentiment changed, moves the old sentiment into `prevSentiment`
    (this is what draws the amber "sentiment changed" highlight on the dashboard)
  - computes `appr` (appreciation potential %) from entry/target if not given
  - derives `sentimentClass`, `waveClass`, `analystClass`, `recClass`, `rec`
  - inserts/updates the asset in data.json and updates data.json's
    meta.lastUpdated timestamp
  - never touches index.html — the dashboard is 100% data-driven

Exit code 0 on success, 1 on validation failure (nothing is written on failure).
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data.json"

SENTIMENT_TO_CLASS = {
    "Buy Now": "badge-buy-now",
    "Trending to Buy": "badge-trending-buy",
    "Trending Negatively": "badge-trending-neg",
    "Appreciating": "badge-appreciating",
}

WAVE_CLASS_HINTS = [
    (re.compile(r"wave\s*[3]", re.I), "wave-impulse"),
    (re.compile(r"wave\s*[124]|abc|correct", re.I), "wave-correct"),
    (re.compile(r"top|5\b", re.I), "wave-top"),
]

ANALYST_TO_CLASS = {
    "Strong Buy": "analyst-strong-buy",
    "Buy": "analyst-buy",
    "Moderate Buy": "analyst-mod-buy",
    "Hold": "analyst-hold",
    "Bullish": "analyst-bullish",
}

YF_SYMBOL_OVERRIDE = {"BTC": "BTC-USD"}
DISPLAY_SYMBOL_OVERRIDE = {"RYCEY": "RR"}

REQUIRED_FIELDS = ["sym", "company", "sentiment", "wave", "entry", "analyst", "summary"]


def parse_price(s):
    """Pull the first dollar-ish number out of a free-text price string."""
    if not s:
        return None
    m = re.search(r"\$?([\d,]+(?:\.\d+)?)", s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def compute_appr(entry, target):
    e, t = parse_price(entry), parse_price(target)
    if e is None or t is None or e == 0:
        return None
    return f"{'+' if t >= e else ''}{round((t - e) / e * 100)}%"


def wave_class_for(wave_text):
    for pattern, cls in WAVE_CLASS_HINTS:
        if pattern.search(wave_text or ""):
            return cls
    return "wave-correct"


def rec_for(sentiment, analyst):
    is_buy_now = sentiment == "Buy Now"
    analyst_ok = analyst in ("Strong Buy", "Buy", "Bullish")
    if is_buy_now and analyst_ok:
        return "rec-buy", "BUY"
    return "rec-wait", "WAIT"


def load_data():
    if not DATA_PATH.exists():
        return {
            "meta": {
                "title": "The Long Investor — Asset Recommendations Dashboard",
                "subtitle": "Elliott Wave analysis from TLI emails (last 30 days) · Assets & collections parsed from email subjects ($TICKER - COLLECTION) · Click any ticker for full TLI summary",
                "analysisDate": datetime.now().strftime("%B %-d, %Y"),
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            },
            "collections": {},
            "collectionStyles": {
                "Top 10": "background:#052e2b;color:#5eead4;border:1px solid #0d9488",
                "Top 20": "background:#1c3a5e;color:#93c5fd;border:1px solid #2563eb",
                "Watchlist": "background:#2a1a00;color:#fbbf24;border:1px solid #b45309",
                "Safe Haven": "background:#071f12;color:#4ade80;border:1px solid #166534",
            },
            "assets": [],
        }
    return json.loads(DATA_PATH.read_text())


def save_data(data):
    data["meta"]["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def merge_entry(data, entry):
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        print(f"ERROR: entry is missing required fields: {missing}", file=sys.stderr)
        sys.exit(1)

    sym = entry["sym"].upper().strip()
    entry["sym"] = sym

    existing = next((a for a in data["assets"] if a["sym"] == sym), None)
    prev_sentiment = existing["sentiment"] if existing else None

    entry.setdefault("target", existing.get("target") if existing else None)
    entry.setdefault("appr", compute_appr(entry.get("entry"), entry.get("target")))
    entry.setdefault("analystTarget", existing.get("analystTarget") if existing else None)
    entry.setdefault("levels", existing.get("levels", []) if existing else [])
    entry.setdefault("collections", existing.get("collections", []) if existing else [])

    entry["sentimentClass"] = SENTIMENT_TO_CLASS.get(entry["sentiment"], "badge-trending-buy")
    entry["waveClass"] = wave_class_for(entry["wave"])
    entry["analystClass"] = ANALYST_TO_CLASS.get(entry["analyst"], "analyst-none")
    entry["recClass"], entry["rec"] = rec_for(entry["sentiment"], entry["analyst"])
    entry["prevSentiment"] = prev_sentiment if (prev_sentiment and prev_sentiment != entry["sentiment"]) else None
    entry["yfSymbol"] = YF_SYMBOL_OVERRIDE.get(sym, sym)
    entry["displaySym"] = DISPLAY_SYMBOL_OVERRIDE.get(sym, sym)

    # update the collections index too, so new collections mentioned in the
    # email (e.g. "$AAPL - Top 10") show up as clickable badges immediately
    for col in entry["collections"]:
        data["collections"].setdefault(col, [])
        if sym not in data["collections"][col]:
            data["collections"][col].append(sym)
        data["collectionStyles"].setdefault(
            col, "background:#1e2433;color:#94a3b8;border:1px solid #2d3748"
        )

    if existing:
        data["assets"][data["assets"].index(existing)] = entry
    else:
        data["assets"].append(entry)

    return entry


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="path to a JSON file containing one entry")
    src.add_argument("--json", help="a JSON string containing one entry")
    args = ap.parse_args()

    entry = json.loads(Path(args.file).read_text()) if args.file else json.loads(args.json)
    data = load_data()
    merged = merge_entry(data, entry)
    save_data(data)
    print(f"OK: {merged['sym']} → sentiment={merged['sentiment']}"
          + (f" (was {merged['prevSentiment']})" if merged["prevSentiment"] else "")
          + f", rec={merged['rec']}")


if __name__ == "__main__":
    main()
