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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GEMINI_REST_URL = "https://api.gemini.com"
CANCEL_ALL_PATH = "/v1/order/cancel/all"
DEFAULT_STALE_SECONDS = 90


def heartbeat_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def gemini_private_post(path: str, payload_extra: dict[str, Any] | None = None) -> dict[str, Any]:
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
                print("STALE heartbeat: live cancel enabled; sending Gemini cancel-all request")
                try:
                    result = gemini_private_post(CANCEL_ALL_PATH)
                    print(json.dumps({"cancel_all_result": result}, sort_keys=True))
                except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                    print(f"cancel_all_error={type(exc).__name__}: {exc}", file=sys.stderr)
                    return 2
            else:
                print("STALE heartbeat: DRY_RUN only. No cancel sent. Set --cancel and MAVERICK_GEMINI_ENABLE_LIVE_CANCEL=1 to enable.")

        if args.once:
            return 1 if stale else 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
