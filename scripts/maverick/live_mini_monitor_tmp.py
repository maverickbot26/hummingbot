#!/usr/bin/env python3
"""Temporary supervised Gemini ZEC mini-pilot monitor.

No order placement. Cancels session orders if the Hummingbot PID disappears or order exposure
exceeds tiny smoke bounds. Secrets are loaded from macOS Keychain and never printed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal

REST = "https://api.gemini.com"
SYMBOL = "zecusd"
MAX_OPEN_ORDERS = 2
MAX_TOTAL_REMAINING_ZEC = Decimal("0.012")
MAX_SINGLE_ORDER_ZEC = Decimal("0.007")
CHECK_SECONDS = 15
RUN_SECONDS = 45 * 60


def keychain(service: str) -> str:
    return subprocess.check_output([
        "security", "find-generic-password", "-a", "maverick", "-s", service, "-w"
    ], text=True).strip()


API_KEY = keychain("hummingbot-gemini-api-key")
API_SECRET = keychain("hummingbot-gemini-api-secret").encode()
_nonce = time.time()


def private_post(path: str, extra: dict | None = None):
    global _nonce
    _nonce = max(_nonce + 0.000001, time.time())
    payload = {"request": path, "nonce": f"{_nonce:.6f}"}
    payload.update(extra or {})
    b64 = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(API_SECRET, b64, hashlib.sha384).hexdigest()
    req = urllib.request.Request(
        REST + path,
        data=b"",
        method="POST",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "X-GEMINI-APIKEY": API_KEY,
            "X-GEMINI-PAYLOAD": b64.decode(),
            "X-GEMINI-SIGNATURE": sig,
            "Cache-Control": "no-cache",
        },
    )
    return json.load(urllib.request.urlopen(req, timeout=10))


def cancel_session(reason: str):
    print(json.dumps({"event": "cancel_session", "reason": reason, "ts": time.time()}), flush=True)
    try:
        print(json.dumps({"cancel_result": private_post("/v1/order/cancel/session")}), flush=True)
    except Exception as exc:
        print(json.dumps({"cancel_error": type(exc).__name__, "message": str(exc)[:200]}), flush=True)


def pid_alive(pid: int) -> bool:
    return subprocess.run(["kill", "-0", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: live_smoke_monitor_tmp.py <hummingbot_pid>", file=sys.stderr)
        return 2
    pid = int(sys.argv[1])
    started_ms = int(time.time() * 1000)
    deadline = time.time() + RUN_SECONDS
    while time.time() < deadline:
        if not pid_alive(pid):
            cancel_session("hummingbot_pid_not_alive")
            return 1
        orders = [o for o in private_post("/v1/orders") if str(o.get("symbol", "")).lower() == SYMBOL]
        total_remaining = sum(Decimal(str(o.get("remaining_amount", "0"))) for o in orders)
        max_remaining = max([Decimal(str(o.get("remaining_amount", "0"))) for o in orders] or [Decimal("0")])
        trades = private_post("/v1/mytrades", {"symbol": SYMBOL, "limit_trades": 20})
        recent_trades = [t for t in trades if int(t.get("timestampms", 0)) >= started_ms]
        status = {
            "event": "status",
            "ts": time.time(),
            "open_orders": len(orders),
            "total_remaining_zec": str(total_remaining),
            "recent_fills": len(recent_trades),
            "orders": [
                {"side": o.get("side"), "price": o.get("price"), "remaining": o.get("remaining_amount")}
                for o in orders
            ],
        }
        print(json.dumps(status), flush=True)
        if len(orders) > MAX_OPEN_ORDERS:
            cancel_session("too_many_open_orders")
            return 1
        if total_remaining > MAX_TOTAL_REMAINING_ZEC:
            cancel_session("total_remaining_exceeds_smoke_bound")
            return 1
        if max_remaining > MAX_SINGLE_ORDER_ZEC:
            cancel_session("single_order_exceeds_smoke_bound")
            return 1
        time.sleep(CHECK_SECONDS)
    print(json.dumps({"event": "monitor_complete", "ts": time.time()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
