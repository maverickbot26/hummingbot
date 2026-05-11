#!/usr/bin/env python3
"""Supervised Gemini ZEC-USD tiny market-making pilot.

Starts the local Hummingbot headless wrapper, monitors Gemini orders/fills and
public Gemini/Coinbase mids, enforces tiny exposure bounds, stops after a fixed
runtime, and cancels only open ZEC-USD orders at shutdown.

Secrets are loaded from macOS Keychain and never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REST = "https://api.gemini.com"
SYMBOL = "zecusd"
GEMINI_BOOK_URL = "https://api.gemini.com/v1/book/zecusd?limit_bids=1&limit_asks=1"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/ZEC-USD/ticker"

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs"
HEARTBEAT_FILE = LOG_DIR / "zec_mm.heartbeat"

MAX_OPEN_ORDERS = 2
MAX_TOTAL_REMAINING_ZEC = Decimal("0.006")
MAX_SINGLE_ORDER_ZEC = Decimal("0.004")
MOVE_WINDOW_SECONDS = 300
MOVE_THRESHOLD_BP = Decimal("300")
BASIS_THRESHOLD_BP = Decimal("50")
BASIS_DURATION_SECONDS = 60


def dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def keychain(service: str) -> str:
    return subprocess.check_output(
        ["security", "find-generic-password", "-a", "maverick", "-s", service, "-w"],
        text=True,
    ).strip()


class GeminiPrivate:
    def __init__(self) -> None:
        self.api_key = keychain("hummingbot-gemini-api-key")
        self.api_secret = keychain("hummingbot-gemini-api-secret").encode()
        self._nonce = time.time()

    def post(self, path: str, extra: dict[str, Any] | None = None) -> Any:
        self._nonce = max(self._nonce + 0.000001, time.time())
        payload = {"request": path, "nonce": f"{self._nonce:.6f}"}
        payload.update(extra or {})
        b64 = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode())
        sig = hmac.new(self.api_secret, b64, hashlib.sha384).hexdigest()
        request = urllib.request.Request(
            REST + path,
            data=b"",
            method="POST",
            headers={
                "Content-Type": "text/plain",
                "Content-Length": "0",
                "X-GEMINI-APIKEY": self.api_key,
                "X-GEMINI-PAYLOAD": b64.decode(),
                "X-GEMINI-SIGNATURE": sig,
                "Cache-Control": "no-cache",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "maverick-zec-pilot/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def gemini_mid() -> Decimal:
    data = fetch_json(GEMINI_BOOK_URL)
    return (dec(data["bids"][0]["price"]) + dec(data["asks"][0]["price"])) / Decimal("2")


def coinbase_mid() -> Decimal:
    data = fetch_json(COINBASE_TICKER_URL)
    bid = dec(data.get("bid"))
    ask = dec(data.get("ask"))
    return (bid + ask) / Decimal("2") if bid > 0 and ask > 0 else dec(data.get("price"))


def pct_bp(new: Decimal, old: Decimal) -> Decimal:
    if old == 0:
        return Decimal("0")
    return (new - old) / old * Decimal("10000")


def balances(gemini: GeminiPrivate) -> dict[str, Decimal]:
    usd = Decimal("0")
    zec = Decimal("0")
    for entry in gemini.post("/v1/balances"):
        currency = str(entry.get("currency", "")).upper()
        if currency == "USD":
            usd = dec(entry.get("amount"))
        elif currency == "ZEC":
            zec = dec(entry.get("amount"))
    return {"USD": usd, "ZEC": zec}


def open_zec_orders(gemini: GeminiPrivate) -> list[dict[str, Any]]:
    return [o for o in gemini.post("/v1/orders") if str(o.get("symbol", "")).lower() == SYMBOL]


def zec_trades_since(gemini: GeminiPrivate, started_ms: int) -> list[dict[str, Any]]:
    trades = gemini.post("/v1/mytrades", {"symbol": SYMBOL, "limit_trades": 500})
    return [t for t in trades if int(t.get("timestampms", 0)) >= started_ms]


def cancel_zec_orders(gemini: GeminiPrivate, reason: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for order in open_zec_orders(gemini):
        order_id = order.get("order_id")
        if not order_id:
            continue
        try:
            result = gemini.post("/v1/order/cancel", {"order_id": order_id})
            results.append({
                "order_id": str(order_id),
                "side": order.get("side"),
                "remaining_amount": order.get("remaining_amount"),
                "reason": reason,
                "is_cancelled": result.get("is_cancelled"),
            })
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            results.append({"order_id": str(order_id), "reason": reason, "error": type(exc).__name__})
    return results


@dataclass
class MidSnapshot:
    ts: float
    gemini: Decimal
    coinbase: Decimal


def sanitize_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "side": o.get("side"),
            "price": o.get("price"),
            "remaining_amount": o.get("remaining_amount"),
            "order_id_suffix": str(o.get("order_id", ""))[-8:],
        }
        for o in orders
    ]


def log_event(payload: dict[str, Any]) -> None:
    def default(obj: Any) -> str:
        if isinstance(obj, Decimal):
            return str(obj)
        return str(obj)

    print(json.dumps(payload, sort_keys=True, default=default), flush=True)


def portfolio_snapshot(gemini: GeminiPrivate) -> dict[str, Decimal]:
    mid = gemini_mid()
    bals = balances(gemini)
    value = bals["USD"] + bals["ZEC"] * mid
    return {"mid": mid, "USD": bals["USD"], "ZEC": bals["ZEC"], "value": value}


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Decimal | int | str]:
    volume_zec = sum(dec(t.get("amount")) for t in trades)
    notional = sum(dec(t.get("amount")) * dec(t.get("price")) for t in trades)
    fee = sum(dec(t.get("fee_amount")) for t in trades)
    buys = sum(dec(t.get("amount")) for t in trades if str(t.get("type", "")).lower() == "buy")
    sells = sum(dec(t.get("amount")) for t in trades if str(t.get("type", "")).lower() == "sell")
    return {
        "count": len(trades),
        "volume_zec": volume_zec,
        "notional_usd": notional,
        "fee_amount": fee,
        "fee_currency": "USD/ZEC fields summed as reported" if fee else "none",
        "buy_zec": buys,
        "sell_zec": sells,
        "net_zec": buys - sells,
    }


def scan_new_log_errors(log_path: Path, offset: int) -> tuple[int, list[str]]:
    if not log_path.exists():
        return offset, []
    data = log_path.read_text(errors="replace")
    chunk = data[offset:]
    new_offset = len(data)
    needles = (
        "Failed to connect MQTT",
        "HTTP status is 451",
        "Unexpected error while retrieving rates",
        "Traceback (most recent call last)",
        " - ERROR - ",
    )
    hits = [line[-500:] for line in chunk.splitlines() if any(n in line for n in needles)]
    return new_offset, hits[:20]


def stop_child(proc: subprocess.Popen[Any], gemini: GeminiPrivate, reason: str) -> list[dict[str, Any]]:
    cancel_results: list[dict[str, Any]] = []
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        time.sleep(2)
    cancel_results.extend(cancel_zec_orders(gemini, reason))
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    cancel_results.extend(cancel_zec_orders(gemini, f"post_stop_{reason}"))
    return cancel_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="conf_pure_mm_gemini_zecusd_tiny.yml")
    parser.add_argument("--runtime-seconds", type=int, default=45 * 60)
    parser.add_argument("--check-seconds", type=int, default=15)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    hb_log_path = LOG_DIR / f"maverick_zec_pilot_hb_{run_id}.log"
    summary_path = LOG_DIR / f"maverick_zec_pilot_summary_{run_id}.json"

    gemini = GeminiPrivate()
    pre_orders = open_zec_orders(gemini)
    pre_cancel_results: list[dict[str, Any]] = []
    if pre_orders:
        pre_cancel_results = cancel_zec_orders(gemini, "pre_start_stale_zec_order")
        time.sleep(2)
        pre_orders_after = open_zec_orders(gemini)
        if pre_orders_after:
            raise RuntimeError(f"Refusing to start: {len(pre_orders_after)} ZEC-USD orders still open after cancel")

    started_ms = int(time.time() * 1000)
    start_snapshot = portfolio_snapshot(gemini)
    history: deque[MidSnapshot] = deque()
    basis_first_bad: float | None = None
    stop_reason = "runtime_complete"
    warnings: list[str] = []
    errors: list[str] = []

    hb_log = hb_log_path.open("w")
    cmd = [sys.executable, "scripts/maverick/headless_no_mqtt_quickstart.py", "-f", args.config]
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=hb_log, stderr=subprocess.STDOUT)
    log_event({
        "event": "pilot_started",
        "pid": proc.pid,
        "config": args.config,
        "runtime_seconds": args.runtime_seconds,
        "hb_log": str(hb_log_path),
        "pre_cancel_results": pre_cancel_results,
        "start": start_snapshot,
    })

    deadline = time.time() + args.runtime_seconds
    log_offset = 0
    cancel_results: list[dict[str, Any]] = []
    try:
        while time.time() < deadline:
            HEARTBEAT_FILE.write_text(str(time.time()))
            if proc.poll() is not None:
                stop_reason = f"hummingbot_process_exited_{proc.returncode}"
                break

            log_offset, new_errors = scan_new_log_errors(hb_log_path, log_offset)
            if new_errors:
                errors.extend(new_errors)
                stop_reason = "hummingbot_log_error"
                break

            orders = open_zec_orders(gemini)
            total_remaining = sum(dec(o.get("remaining_amount")) for o in orders)
            max_remaining = max([dec(o.get("remaining_amount")) for o in orders] or [Decimal("0")])
            if len(orders) > MAX_OPEN_ORDERS:
                stop_reason = "too_many_open_orders"
                break
            if total_remaining > MAX_TOTAL_REMAINING_ZEC:
                stop_reason = "total_remaining_exceeds_tiny_bound"
                break
            if max_remaining > MAX_SINGLE_ORDER_ZEC:
                stop_reason = "single_order_exceeds_tiny_bound"
                break

            now = time.time()
            g_mid = gemini_mid()
            c_mid = coinbase_mid()
            snapshot = MidSnapshot(now, g_mid, c_mid)
            history.append(snapshot)
            while history and now - history[0].ts > MOVE_WINDOW_SECONDS:
                history.popleft()
            move_bp = abs(pct_bp(g_mid, history[0].gemini)) if history else Decimal("0")
            basis_bp = abs(pct_bp(g_mid, c_mid))
            if move_bp > MOVE_THRESHOLD_BP:
                stop_reason = f"volatility_move_{move_bp:.1f}bp"
                break
            if basis_bp > BASIS_THRESHOLD_BP:
                basis_first_bad = basis_first_bad or now
                if now - basis_first_bad >= BASIS_DURATION_SECONDS:
                    stop_reason = f"gemini_coinbase_basis_{basis_bp:.1f}bp"
                    break
            else:
                basis_first_bad = None

            trades = zec_trades_since(gemini, started_ms)
            log_event({
                "event": "status",
                "remaining_runtime_seconds": max(0, int(deadline - now)),
                "open_orders": len(orders),
                "total_remaining_zec": total_remaining,
                "recent_fills": len(trades),
                "gemini_mid": g_mid,
                "coinbase_mid": c_mid,
                "basis_bp": basis_bp,
                "orders": sanitize_orders(orders),
            })
            time.sleep(args.check_seconds)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        stop_reason = f"monitor_error_{type(exc).__name__}"
        errors.append(str(exc)[:500])
    finally:
        cancel_results = stop_child(proc, gemini, stop_reason)
        hb_log.close()
        HEARTBEAT_FILE.write_text(f"stopped {time.time()}")

    end_snapshot = portfolio_snapshot(gemini)
    final_orders = open_zec_orders(gemini)
    trades = zec_trades_since(gemini, started_ms)
    trade_summary = summarize_trades(trades)
    portfolio_pnl = end_snapshot["value"] - start_snapshot["value"]
    buy_hold_value = start_snapshot["USD"] + start_snapshot["ZEC"] * end_snapshot["mid"]
    buy_hold_pnl = buy_hold_value - start_snapshot["value"]
    mm_alpha = portfolio_pnl - buy_hold_pnl
    summary: dict[str, Any] = {
        "run_id": run_id,
        "config": args.config,
        "started_ms": started_ms,
        "stop_reason": stop_reason,
        "hummingbot_returncode": proc.returncode,
        "hb_log_path": str(hb_log_path),
        "start": start_snapshot,
        "end": end_snapshot,
        "trades": trade_summary,
        "portfolio_pnl_usd": portfolio_pnl,
        "portfolio_pnl_pct": (portfolio_pnl / start_snapshot["value"] * Decimal("100")) if start_snapshot["value"] else Decimal("0"),
        "buy_hold_pnl_usd": buy_hold_pnl,
        "buy_hold_pnl_pct": (buy_hold_pnl / start_snapshot["value"] * Decimal("100")) if start_snapshot["value"] else Decimal("0"),
        "mm_alpha_usd": mm_alpha,
        "final_open_orders": len(final_orders),
        "final_open_buys": sum(1 for o in final_orders if str(o.get("side", "")).lower() == "buy"),
        "final_open_sells": sum(1 for o in final_orders if str(o.get("side", "")).lower() == "sell"),
        "final_orders": sanitize_orders(final_orders),
        "pre_cancel_results": pre_cancel_results,
        "shutdown_cancel_results": cancel_results,
        "warnings": warnings,
        "errors": errors[:20],
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
    log_event({"event": "pilot_complete", "summary_path": str(summary_path), "summary": summary})
    return 0 if stop_reason == "runtime_complete" and not final_orders else 1


if __name__ == "__main__":
    raise SystemExit(main())
