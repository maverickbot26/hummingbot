#!/usr/bin/env python3
"""Public-data volatility pause watcher for Gemini ZEC-USD vs Coinbase ZEC-USD.

Safe by default: public market-data only; writes/removes a local pause flag; no orders.
Triggers from the approved plan:
- Gemini mid moves >3% in 5 minutes.
- Gemini mid deviates >50 bp from Coinbase mid for >60 seconds.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

GEMINI_BOOK_URL = "https://api.gemini.com/v1/book/zecusd?limit_bids=1&limit_asks=1"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/ZEC-USD/ticker"


@dataclass
class MidSnapshot:
    ts: float
    gemini_mid: Decimal
    coinbase_mid: Decimal


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "maverick-zec-watch/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def gemini_mid() -> Decimal:
    data = fetch_json(GEMINI_BOOK_URL)
    best_bid = Decimal(data["bids"][0]["price"])
    best_ask = Decimal(data["asks"][0]["price"])
    return (best_bid + best_ask) / Decimal("2")


def coinbase_mid() -> Decimal:
    data = fetch_json(COINBASE_TICKER_URL)
    bid = Decimal(data["bid"])
    ask = Decimal(data["ask"])
    if bid > 0 and ask > 0:
        return (bid + ask) / Decimal("2")
    return Decimal(data["price"])


def pct_change_bp(new: Decimal, old: Decimal) -> Decimal:
    return ((new - old) / old) * Decimal("10000")


def write_flag(path: Path, reason: str, snapshot: MidSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "paused": True,
        "reason": reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snapshot.ts)),
        "gemini_mid": str(snapshot.gemini_mid),
        "coinbase_mid": str(snapshot.coinbase_mid),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def clear_flag(path: Path) -> None:
    if path.exists():
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag-file", default="logs/zec_mm_pause.flag")
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--move-window-seconds", type=float, default=300)
    parser.add_argument("--move-threshold-bp", type=Decimal, default=Decimal("300"))
    parser.add_argument("--basis-threshold-bp", type=Decimal, default=Decimal("50"))
    parser.add_argument("--basis-duration-seconds", type=float, default=60)
    args = parser.parse_args()

    history: deque[MidSnapshot] = deque()
    basis_first_bad: float | None = None
    flag_file = Path(args.flag_file)

    while True:
        now = time.time()
        snapshot = MidSnapshot(now, gemini_mid(), coinbase_mid())
        history.append(snapshot)
        while history and now - history[0].ts > args.move_window_seconds:
            history.popleft()

        reasons: list[str] = []
        if history:
            move_bp = abs(pct_change_bp(snapshot.gemini_mid, history[0].gemini_mid))
            if move_bp > args.move_threshold_bp:
                reasons.append(f"Gemini 5m move {move_bp:.1f} bp > {args.move_threshold_bp} bp")

        basis_bp = abs(pct_change_bp(snapshot.gemini_mid, snapshot.coinbase_mid))
        if basis_bp > args.basis_threshold_bp:
            basis_first_bad = basis_first_bad or now
            if now - basis_first_bad >= args.basis_duration_seconds:
                reasons.append(f"Gemini/Coinbase basis {basis_bp:.1f} bp > {args.basis_threshold_bp} bp")
        else:
            basis_first_bad = None

        if reasons:
            reason = "; ".join(reasons)
            write_flag(flag_file, reason, snapshot)
            print(f"PAUSE {reason}")
        else:
            clear_flag(flag_file)
            print(f"OK gemini_mid={snapshot.gemini_mid} coinbase_mid={snapshot.coinbase_mid} basis_bp={basis_bp:.1f}")

        if args.once:
            return 1 if reasons else 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
