#!/usr/bin/env python3
"""Hourly ZEC-USD PnL logger skeleton.

Safe by default: public Gemini/Coinbase mid only, local file append, no orders/cancels.
Optional Gemini private reads require MAVERICK_ENABLE_PRIVATE_READS=1 plus read-only API env vars.
Never prints API keys/secrets.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean, median
from typing import Any

GEMINI_REST_URL = "https://api.gemini.com"
GEMINI_BOOK_URL = "https://api.gemini.com/v1/book/zecusd?limit_bids=1&limit_asks=1"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/ZEC-USD/ticker"


def fetch_json(url: str) -> dict | list:
    request = urllib.request.Request(url, headers={"User-Agent": "maverick-zec-pnl/1.0"})
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


def private_reads_enabled() -> bool:
    return os.environ.get("MAVERICK_ENABLE_PRIVATE_READS") == "1"


def gemini_private_post(path: str, payload_extra: dict[str, Any] | None = None) -> dict | list:
    api_key = os.environ.get("GEMINI_API_KEY")
    api_secret = os.environ.get("GEMINI_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("GEMINI_API_KEY/GEMINI_API_SECRET are required for private Gemini reads")
    payload: dict[str, Any] = {"request": path, "nonce": str(int(time.time() * 1_000_000))}
    payload.update(payload_extra or {})
    encoded_payload = base64.b64encode(json.dumps(payload).encode("utf-8"))
    signature = hmac.new(api_secret.encode("utf-8"), encoded_payload, hashlib.sha384).hexdigest()
    request = urllib.request.Request(
        GEMINI_REST_URL + path,
        data=b"",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "X-GEMINI-APIKEY": api_key,
            "X-GEMINI-PAYLOAD": encoded_payload.decode("utf-8"),
            "X-GEMINI-SIGNATURE": signature,
            "Cache-Control": "no-cache",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def read_markout_summary(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    values: list[Decimal] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            value = row.get("markout_30s_bp") or row.get("gemini_markout_30s_bp") or row.get("coinbase_markout_30s_bp")
            if value:
                values.append(as_decimal(value))
    if not values:
        return {}
    return {
        "markout_30s_count": str(len(values)),
        "markout_30s_avg_bp": f"{mean(values):.2f}",
        "markout_30s_median_bp": f"{median(values):.2f}",
        "markout_30s_worst_bp": f"{min(values):.2f}",
    }


def summarize_private(g_mid: Decimal) -> dict[str, str]:
    summary: dict[str, str] = {}
    if not private_reads_enabled():
        summary["private_reads"] = "disabled"
        return summary
    try:
        balances = gemini_private_post("/v1/balances")
        orders = gemini_private_post("/v1/orders")
        trades = gemini_private_post("/v1/mytrades", {"symbol": "zecusd", "limit_trades": 50})
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return {"private_reads": "error", "private_read_error": type(exc).__name__}

    usd = Decimal("0")
    zec = Decimal("0")
    if isinstance(balances, list):
        for entry in balances:
            currency = str(entry.get("currency", "")).upper()
            if currency == "USD":
                usd = as_decimal(entry.get("amount"))
            elif currency == "ZEC":
                zec = as_decimal(entry.get("amount"))
    portfolio_value = usd + zec * g_mid
    summary.update({
        "private_reads": "enabled",
        "gemini_usd": str(usd),
        "gemini_zec": str(zec),
        "portfolio_value_usd": str(portfolio_value),
        "inventory_pct_zec": str((zec * g_mid / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")),
        "open_orders": str(len(orders) if isinstance(orders, list) else "unknown"),
        "recent_trades_checked": str(len(trades) if isinstance(trades, list) else "unknown"),
    })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("logs/zec_hourly_pnl.jsonl"))
    parser.add_argument("--markouts-csv", type=Path)
    args = parser.parse_args()

    g_mid = gemini_mid()
    c_mid = coinbase_mid()
    row: dict[str, str] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gemini_mid": str(g_mid),
        "coinbase_mid": str(c_mid),
        "gemini_coinbase_basis_bp": str((g_mid - c_mid) / c_mid * Decimal("10000")),
        "read": "healthy",
        "fees_confirmed": "pending_fill_export",
        "reconciliation_delta": "pending_private_read",
    }
    row.update(summarize_private(g_mid))
    row.update(read_markout_summary(args.markouts_csv))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
