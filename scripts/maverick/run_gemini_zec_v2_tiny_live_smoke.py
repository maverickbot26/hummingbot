#!/usr/bin/env python3
"""Supervised Gemini ZEC-USD V2 tiny live smoke.

Starts the local Hummingbot V2 controller stack through the no-MQTT headless
wrapper, arms a heartbeat-based cancel-all deadman, monitors Gemini live orders
and public Gemini/Coinbase mids, enforces Phase 3 tiny bounds, and cancels all
ZEC-USD orders at shutdown.

Secrets are loaded from macOS Keychain and never printed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.maverick.run_zec_tiny_supervised_pilot import (
    GeminiPrivate,
    balances,
    cancel_zec_orders,
    coinbase_mid,
    dec,
    gemini_mid,
    keychain,
    open_zec_orders,
    pct_bp,
    portfolio_snapshot,
    sanitize_orders,
    scan_new_log_errors,
    summarize_trades,
    zec_trades_since,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs"
HEARTBEAT_FILE = LOG_DIR / "zec_v2_mm.heartbeat"
LOCK_FILE = LOG_DIR / "gemini_zec_v2_tiny_live_smoke.lock"
SYMBOL = "zecusd"

MAX_OPEN_ORDERS = 2
MAX_SINGLE_ORDER_ZEC = Decimal("0.0025")
MAX_TOTAL_REMAINING_ZEC = Decimal("0.0055")
MOVE_WINDOW_SECONDS = 300
MOVE_THRESHOLD_BP = Decimal("300")
BASIS_THRESHOLD_BP = Decimal("50")
BASIS_DURATION_SECONDS = 60


@dataclass
class MidSnapshot:
    ts: float
    gemini: Decimal
    coinbase: Decimal


def log_event(payload: dict[str, Any]) -> None:
    def default(obj: Any) -> str:
        if isinstance(obj, Decimal):
            return str(obj)
        return str(obj)

    print(json.dumps(payload, sort_keys=True, default=default), flush=True)


def assert_no_duplicate_process() -> None:
    current_pid = os.getpid()
    parent_pid = os.getppid()
    result = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True, check=True)
    suspicious: list[str] = []
    needles = (
        "bin/hummingbot_quickstart.py",
        "headless_no_mqtt_quickstart.py",
        "v2_with_controllers",
        "run_zec_tiny_supervised_pilot.py",
        "run_gemini_zec_v2_tiny_live_smoke.py",
    )
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1] if len(parts) > 1 else ""
        if pid in {current_pid, parent_pid}:
            continue
        if "pytest" in command or "egrep" in command:
            continue
        if any(needle in command for needle in needles):
            suspicious.append(f"{pid} {command[:220]}")
    if suspicious:
        raise RuntimeError("Refusing to start: possible duplicate Hummingbot/V1/V2 process: " + " | ".join(suspicious))


def assert_order_bounds(orders: list[dict[str, Any]]) -> None:
    total_remaining = sum(dec(o.get("remaining_amount")) for o in orders)
    max_remaining = max([dec(o.get("remaining_amount")) for o in orders] or [Decimal("0")])
    if len(orders) > MAX_OPEN_ORDERS:
        raise RuntimeError(f"too_many_open_orders: {len(orders)}")
    if total_remaining > MAX_TOTAL_REMAINING_ZEC:
        raise RuntimeError(f"total_remaining_exceeds_tiny_bound: {total_remaining}")
    if max_remaining > MAX_SINGLE_ORDER_ZEC:
        raise RuntimeError(f"single_order_exceeds_tiny_bound: {max_remaining}")


def start_deadman(run_id: str) -> tuple[subprocess.Popen[Any], Path]:
    watchdog_log_path = LOG_DIR / f"maverick_zec_v2_deadman_{run_id}.log"
    env = os.environ.copy()
    env.update({
        "GEMINI_API_KEY": keychain("hummingbot-gemini-api-key"),
        "GEMINI_API_SECRET": keychain("hummingbot-gemini-api-secret"),
        "MAVERICK_GEMINI_ENABLE_LIVE_CANCEL": "1",
    })
    log_handle = watchdog_log_path.open("w")
    proc = subprocess.Popen(
        [
            sys.executable,
            "scripts/maverick/gemini_zec_deadman_watchdog.py",
            "--heartbeat-file",
            str(HEARTBEAT_FILE),
            "--stale-seconds",
            "45",
            "--poll-seconds",
            "10",
            "--cancel",
        ],
        cwd=REPO_ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    log_handle.close()
    return proc, watchdog_log_path


def stop_process(proc: subprocess.Popen[Any], gemini: GeminiPrivate, reason: str) -> list[dict[str, Any]]:
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


def stop_deadman(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-config", default="gemini_zec_v2_tiny_live.yml")
    parser.add_argument("--runtime-seconds", type=int, default=12 * 60)
    parser.add_argument("--check-seconds", type=int, default=15)
    parser.add_argument("--dry-run-preflight", action="store_true", help="Validate preconditions and exit before live launch")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    hb_log_path = LOG_DIR / f"maverick_zec_v2_live_smoke_hb_{run_id}.log"
    summary_path = LOG_DIR / f"maverick_zec_v2_live_smoke_summary_{run_id}.json"

    with LOCK_FILE.open("w") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(f"duplicate_process_guard_active: {LOCK_FILE}") from exc

        assert_no_duplicate_process()
        gemini = GeminiPrivate()
        pre_orders = open_zec_orders(gemini)
        if pre_orders:
            raise RuntimeError(f"Refusing to start: {len(pre_orders)} ZEC-USD orders already open: {sanitize_orders(pre_orders)}")
        assert_order_bounds(pre_orders)

        start_balances = balances(gemini)
        start_mid = gemini_mid()
        coinbase_start_mid = coinbase_mid()
        if start_balances["USD"] <= Decimal("5") or start_balances["ZEC"] < Decimal("0.004"):
            raise RuntimeError(f"Refusing to start: balances not sane USD={start_balances['USD']} ZEC={start_balances['ZEC']}")
        basis_start_bp = abs(pct_bp(start_mid, coinbase_start_mid))
        if basis_start_bp > BASIS_THRESHOLD_BP:
            raise RuntimeError(f"Refusing to start: Gemini/Coinbase basis too wide {basis_start_bp:.1f}bp")

        if args.dry_run_preflight:
            log_event({
                "event": "dry_run_preflight_ok",
                "balances": start_balances,
                "gemini_mid": start_mid,
                "coinbase_mid": coinbase_start_mid,
                "basis_bp": basis_start_bp,
                "open_orders": len(pre_orders),
                "max_single_order_zec": MAX_SINGLE_ORDER_ZEC,
                "max_total_remaining_zec": MAX_TOTAL_REMAINING_ZEC,
            })
            return 0

        started_ms = int(time.time() * 1000)
        start_snapshot = portfolio_snapshot(gemini)
        history: deque[MidSnapshot] = deque()
        basis_first_bad: float | None = None
        stop_reason = "runtime_complete"
        warnings: list[str] = []
        errors: list[str] = []
        cancel_results: list[dict[str, Any]] = []
        watchdog_proc: subprocess.Popen[Any] | None = None
        watchdog_log_path: Path | None = None

        HEARTBEAT_FILE.write_text(str(time.time()))
        watchdog_proc, watchdog_log_path = start_deadman(run_id)
        hb_log = hb_log_path.open("w")
        cmd = [sys.executable, "scripts/maverick/headless_no_mqtt_quickstart.py", "--v2", args.v2_config]
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=hb_log, stderr=subprocess.STDOUT)
        log_event({
            "event": "v2_live_smoke_started",
            "pid": proc.pid,
            "watchdog_pid": watchdog_proc.pid if watchdog_proc else None,
            "v2_config": args.v2_config,
            "runtime_seconds": args.runtime_seconds,
            "hb_log": str(hb_log_path),
            "watchdog_log": str(watchdog_log_path) if watchdog_log_path else None,
            "start": start_snapshot,
            "bounds": {
                "max_open_orders": MAX_OPEN_ORDERS,
                "max_single_order_zec": MAX_SINGLE_ORDER_ZEC,
                "max_total_remaining_zec": MAX_TOTAL_REMAINING_ZEC,
            },
        })

        deadline = time.time() + args.runtime_seconds
        log_offset = 0
        try:
            while time.time() < deadline:
                HEARTBEAT_FILE.write_text(str(time.time()))
                if watchdog_proc and watchdog_proc.poll() is not None:
                    stop_reason = f"deadman_exited_{watchdog_proc.returncode}"
                    break
                if proc.poll() is not None:
                    stop_reason = f"hummingbot_process_exited_{proc.returncode}"
                    break

                log_offset, new_errors = scan_new_log_errors(hb_log_path, log_offset)
                if new_errors:
                    errors.extend(new_errors)
                    stop_reason = "hummingbot_log_error"
                    break

                orders = open_zec_orders(gemini)
                try:
                    assert_order_bounds(orders)
                except RuntimeError as exc:
                    stop_reason = str(exc).split(":", 1)[0]
                    errors.append(str(exc))
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
                    "total_remaining_zec": sum(dec(o.get("remaining_amount")) for o in orders),
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
            cancel_results = stop_process(proc, gemini, stop_reason)
            hb_log.close()
            HEARTBEAT_FILE.write_text(f"stopped {time.time()}")
            stop_deadman(watchdog_proc)

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
            "mode": "gemini_zec_v2_tiny_live_smoke",
            "v2_config": args.v2_config,
            "started_ms": started_ms,
            "runtime_seconds_requested": args.runtime_seconds,
            "stop_reason": stop_reason,
            "hummingbot_returncode": proc.returncode,
            "watchdog_returncode": watchdog_proc.returncode if watchdog_proc else None,
            "hb_log_path": str(hb_log_path),
            "watchdog_log_path": str(watchdog_log_path) if watchdog_log_path else None,
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
            "shutdown_cancel_results": cancel_results,
            "warnings": warnings,
            "errors": errors[:20],
            "bounds": {
                "max_open_orders": MAX_OPEN_ORDERS,
                "max_single_order_zec": MAX_SINGLE_ORDER_ZEC,
                "max_total_remaining_zec": MAX_TOTAL_REMAINING_ZEC,
            },
        }
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
        log_event({"event": "v2_live_smoke_complete", "summary_path": str(summary_path), "summary": summary})
        return 0 if stop_reason == "runtime_complete" and not final_orders else 1


if __name__ == "__main__":
    raise SystemExit(main())
