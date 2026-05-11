#!/usr/bin/env python3
"""Gemini ZEC-USD deadman cancel watchdog skeleton.

Safe by default:
- Does not place orders.
- Does not cancel unless BOTH --cancel and MAVERICK_GEMINI_ENABLE_LIVE_CANCEL=1 are set.
- Does not print API keys/secrets.

Intended use after a supervised live smoke starts writing a heartbeat file:
  python scripts/maverick/gemini_zec_deadman_watchdog.py --heartbeat-file logs/zec_mm.heartbeat
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GEMINI_REST_URL = "https://api.gemini.com"
CANCEL_ALL_PATH = "/v1/order/cancel/all"
OPEN_ORDERS_PATH = "/v1/orders"
CANCEL_ORDER_PATH = "/v1/order/cancel"
DEFAULT_STALE_SECONDS = 90
SYMBOL = "zecusd"


def heartbeat_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def gemini_private_post(path: str, payload_extra: dict[str, Any] | None = None) -> Any:
    api_key = os.environ.get("GEMINI_API_KEY")
    api_secret = os.environ.get("GEMINI_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("GEMINI_API_KEY/GEMINI_API_SECRET are required for private Gemini calls")

    payload: dict[str, Any] = {
        "request": path,
        "nonce": str(int(time.time() * 1_000_000)),
    }
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


def open_zec_orders() -> list[dict[str, Any]]:
    orders = gemini_private_post(OPEN_ORDERS_PATH)
    if not isinstance(orders, list):
        raise RuntimeError(f"Unexpected Gemini open-orders response: {type(orders).__name__}")
    return [order for order in orders if str(order.get("symbol", "")).lower() == SYMBOL]


def sanitize_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "side": order.get("side"),
        "price": order.get("price"),
        "remaining_amount": order.get("remaining_amount"),
        "order_id_suffix": str(order.get("order_id", ""))[-8:],
    }


def cancel_zec_orders_individually(reason: str) -> dict[str, Any]:
    initial_orders = open_zec_orders()
    cancel_results: list[dict[str, Any]] = []
    for order in initial_orders:
        order_id = order.get("order_id")
        if not order_id:
            cancel_results.append({"reason": reason, "error": "missing_order_id", "order": sanitize_order(order)})
            continue
        try:
            result = gemini_private_post(CANCEL_ORDER_PATH, {"order_id": order_id})
            cancel_results.append({
                "reason": reason,
                "side": order.get("side"),
                "remaining_amount": order.get("remaining_amount"),
                "order_id_suffix": str(order_id)[-8:],
                "is_cancelled": result.get("is_cancelled") if isinstance(result, dict) else None,
            })
        except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            cancel_results.append({
                "reason": reason,
                "side": order.get("side"),
                "remaining_amount": order.get("remaining_amount"),
                "order_id_suffix": str(order_id)[-8:],
                "error": type(exc).__name__,
            })
    final_orders = open_zec_orders()
    return {
        "initial_open_orders": len(initial_orders),
        "cancel_results": cancel_results,
        "final_open_orders": len(final_orders),
        "final_orders": [sanitize_order(order) for order in final_orders],
        "ok": len(final_orders) == 0,
    }


def cancel_all_or_reconcile_zec() -> dict[str, Any]:
    """Cancel all Gemini orders, falling back to ZEC-USD reconciliation.

    Gemini can return HTTP 400 for account/order-state edge cases. A failed
    cancel-all is safe if a subsequent read-only reconcile shows no ZEC-USD
    orders remain; otherwise cancel the remaining ZEC-USD orders one by one.
    """
    try:
        cancel_all_result = gemini_private_post(CANCEL_ALL_PATH)
        fallback = cancel_zec_orders_individually("cancel_all_post_reconcile")
        return {
            "cancel_all_result": cancel_all_result,
            "fallback_reconcile": fallback,
            "ok": fallback["ok"],
        }
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        fallback = cancel_zec_orders_individually("cancel_all_failed_fallback")
        return {
            "cancel_all_error": type(exc).__name__,
            "fallback_reconcile": fallback,
            "ok": fallback["ok"],
        }


def live_cancel_enabled(args: argparse.Namespace) -> bool:
    return args.cancel and os.environ.get("MAVERICK_GEMINI_ENABLE_LIVE_CANCEL") == "1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heartbeat-file", default="logs/zec_mm.heartbeat", help="File Hummingbot/supervisor touches periodically")
    parser.add_argument("--stale-seconds", type=float, default=DEFAULT_STALE_SECONDS)
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--cancel", action="store_true", help="Allow live cancel only with MAVERICK_GEMINI_ENABLE_LIVE_CANCEL=1")
    args = parser.parse_args()

    heartbeat_file = Path(args.heartbeat_file)
    while True:
        age = heartbeat_age_seconds(heartbeat_file)
        stale = age is None or age > args.stale_seconds
        status = "missing" if age is None else f"{age:.1f}s old"
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} heartbeat={status} stale={stale}")

        if stale:
            if live_cancel_enabled(args):
                print("STALE heartbeat: live cancel enabled; sending Gemini cancel-all request with ZEC fallback reconcile")
                result = cancel_all_or_reconcile_zec()
                print(json.dumps({"deadman_cancel_result": result}, sort_keys=True))
                return 0 if result.get("ok") else 2
            else:
                print("STALE heartbeat: DRY_RUN only. No cancel sent. Set --cancel and MAVERICK_GEMINI_ENABLE_LIVE_CANCEL=1 to enable.")

        if args.once:
            return 1 if stale else 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
