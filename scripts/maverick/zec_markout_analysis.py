#!/usr/bin/env python3
"""ZEC-USD markout analysis helper.

Reads fill rows from CSV/JSONL and computes markouts where future mids are already present.
Optional --sample-now can wait and capture public Gemini/Coinbase mids at 1s/5s/30s/1m/5m
for a freshly-observed fill price, but the default path never waits and never uses private APIs.

Expected fill columns/keys: timestamp, side, price, size.
Optional mid columns: gemini_mid_1s, gemini_mid_5s, gemini_mid_30s, gemini_mid_1m, gemini_mid_5m
and/or coinbase_mid_<horizon>.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean, median
from typing import Iterable

GEMINI_BOOK_URL = "https://api.gemini.com/v1/book/zecusd?limit_bids=1&limit_asks=1"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/ZEC-USD/ticker"
HORIZONS = [("1s", 1), ("5s", 5), ("30s", 30), ("1m", 60), ("5m", 300)]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "maverick-zec-markout/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def gemini_mid() -> Decimal:
    data = fetch_json(GEMINI_BOOK_URL)
    return (Decimal(data["bids"][0]["price"]) + Decimal(data["asks"][0]["price"])) / Decimal("2")


def coinbase_mid() -> Decimal:
    data = fetch_json(COINBASE_TICKER_URL)
    bid = Decimal(data["bid"])
    ask = Decimal(data["ask"])
    return (bid + ask) / Decimal("2") if bid > 0 and ask > 0 else Decimal(data["price"])


def dec(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def load_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def markout_bp(side: str, fill_price: Decimal, future_mid: Decimal) -> Decimal:
    # Positive means favorable after the fill: buy then mid rises, sell then mid falls.
    sign = Decimal("1") if side.lower().startswith("b") else Decimal("-1")
    return sign * (future_mid - fill_price) / fill_price * Decimal("10000")


def summarize(values: list[Decimal]) -> dict[str, str] | None:
    if not values:
        return None
    return {
        "count": str(len(values)),
        "avg_bp": f"{mean(values):.2f}",
        "median_bp": f"{median(values):.2f}",
        "worst_bp": f"{min(values):.2f}",
        "best_bp": f"{max(values):.2f}",
    }


def compute_from_rows(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    buckets: dict[str, list[Decimal]] = {label: [] for label, _ in HORIZONS}
    for row in rows:
        side = row.get("side", "")
        price = dec(row.get("price"))
        if not side or price is None or price <= 0:
            continue
        for label, _ in HORIZONS:
            for venue in ("gemini", "coinbase"):
                future_mid = dec(row.get(f"{venue}_mid_{label}"))
                if future_mid is not None and future_mid > 0:
                    buckets[label].append(markout_bp(side, price, future_mid))
                    break
    return {label: summary for label, vals in buckets.items() if (summary := summarize(vals)) is not None}


def sample_now(fill_side: str, fill_price: Decimal) -> None:
    start = time.time()
    print(json.dumps({"t0_gemini_mid": str(gemini_mid()), "t0_coinbase_mid": str(coinbase_mid())}))
    for label, seconds in HORIZONS:
        sleep_for = start + seconds - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        g_mid = gemini_mid()
        c_mid = coinbase_mid()
        print(json.dumps({
            "horizon": label,
            "gemini_mid": str(g_mid),
            "coinbase_mid": str(c_mid),
            "gemini_markout_bp": str(markout_bp(fill_side, fill_price, g_mid)),
            "coinbase_markout_bp": str(markout_bp(fill_side, fill_price, c_mid)),
        }))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fills", type=Path, help="CSV or JSONL fill export/log")
    parser.add_argument("--sample-now", action="store_true", help="Wait and sample future mids for one fill")
    parser.add_argument("--side", choices=["buy", "sell"], help="Required with --sample-now")
    parser.add_argument("--price", type=Decimal, help="Required with --sample-now")
    args = parser.parse_args()

    if args.sample_now:
        if not args.side or args.price is None:
            parser.error("--sample-now requires --side and --price")
        sample_now(args.side, args.price)
        return 0

    if not args.fills or not args.fills.exists():
        print(json.dumps({"status": "no_fills_file", "hint": "pass --fills path/to/fills.csv or .jsonl"}))
        return 0

    print(json.dumps(compute_from_rows(load_rows(args.fills)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
