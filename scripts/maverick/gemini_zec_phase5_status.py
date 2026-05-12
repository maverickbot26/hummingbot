#!/usr/bin/env python3
"""Phase 5 production-ops status/check/report for Gemini ZEC-USD V2 tiny MM.

Read-only by default. It summarizes live Gemini portfolio/open orders, latest
V2 live summary, warnings/errors, process/watchdog/heartbeat state, and emits a
simple go/no-go read for continued mini-ops. It never prints API secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.maverick.gemini_zec_basis_policy import BasisPolicyConfig, basis_direction_label
from scripts.maverick.run_gemini_zec_v2_tiny_live_smoke import (
    HEARTBEAT_FILE,
    MAX_OPEN_ORDERS,
    MAX_SINGLE_ORDER_ZEC,
    MAX_TOTAL_REMAINING_ZEC,
)
from scripts.maverick.run_zec_tiny_supervised_pilot import (
    GeminiPrivate,
    coinbase_mid,
    dec,
    open_zec_orders,
    portfolio_snapshot,
    sanitize_orders,
    scan_new_log_errors,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs"
DEFAULT_DASHBOARD_PATH = REPO_ROOT / "docs" / "maverick" / "gemini_zec_phase5_ops_dashboard.md"
DEFAULT_JSON_PATH = LOG_DIR / "gemini_zec_phase5_status_latest.json"
STALE_ORDER_SECONDS = 10 * 60
DASHBOARD_VERSION = 2
BASIS_STATUS_CONFIG = BasisPolicyConfig()


@dataclass(frozen=True)
class ProcessSnapshot:
    hummingbot: list[str]
    watchdog: list[str]
    runner: list[str]
    headless: list[str]
    legacy: list[str]

    @property
    def active_supervised_v2(self) -> bool:
        return bool(self.runner and self.headless and self.watchdog)


def decimal_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def pct(part: Decimal, total: Decimal) -> Decimal:
    return Decimal("0") if total == 0 else part / total * Decimal("100")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def latest_summary_path() -> Path | None:
    summaries = sorted(LOG_DIR.glob("maverick_zec_v2_live_smoke_summary_*.json"), key=lambda path: path.stat().st_mtime)
    return summaries[-1] if summaries else None


def latest_summary() -> tuple[Path | None, dict[str, Any] | None]:
    path = latest_summary_path()
    if path is None:
        return None, None
    return path, read_json(path)


def heartbeat_state(path: Path = HEARTBEAT_FILE) -> dict[str, Any]:
    exists = path.exists()
    age = max(0.0, time.time() - path.stat().st_mtime) if exists else None
    value = path.read_text(errors="replace").strip()[:160] if exists else None
    return {
        "path": str(path.relative_to(REPO_ROOT) if path.is_absolute() and path.exists() else path),
        "exists": exists,
        "age_seconds": round(age, 3) if age is not None else None,
        "stale": True if age is None else age > 90,
        "value": value,
    }


def process_snapshot() -> ProcessSnapshot:
    result = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True, check=True)
    current_pid = os.getpid()
    runner_needles = ("run_gemini_zec_v2_tiny_live_smoke.py",)
    headless_needles = ("headless_no_mqtt_quickstart.py", "v2_with_controllers")
    legacy_needles = ("bin/hummingbot_quickstart.py", "run_zec_tiny_supervised_pilot.py")
    watchdog_needles = ("gemini_zec_deadman_watchdog.py",)
    runner: list[str] = []
    headless: list[str] = []
    legacy: list[str] = []
    watchdog: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid or "pytest" in command or "--dry-run-preflight" in command:
            continue
        rendered = f"{pid} {command[:220]}"
        if any(needle in command for needle in runner_needles):
            runner.append(rendered)
        elif any(needle in command for needle in headless_needles):
            headless.append(rendered)
        elif any(needle in command for needle in legacy_needles):
            legacy.append(rendered)
        if any(needle in command for needle in watchdog_needles):
            watchdog.append(rendered)
    hummingbot = runner + headless + legacy
    return ProcessSnapshot(hummingbot=hummingbot, watchdog=watchdog, runner=runner, headless=headless, legacy=legacy)


def stale_orders(orders: list[dict[str, Any]], now_ms: int | None = None) -> list[dict[str, Any]]:
    now_ms = now_ms or int(time.time() * 1000)
    stale: list[dict[str, Any]] = []
    for order in orders:
        timestamp_ms = int(dec(order.get("timestampms") or order.get("timestamp") or 0))
        if timestamp_ms == 0:
            continue
        age_seconds = max(0, (now_ms - timestamp_ms) / 1000)
        if age_seconds > STALE_ORDER_SECONDS:
            stale.append({
                "side": order.get("side"),
                "price": order.get("price"),
                "remaining_amount": order.get("remaining_amount"),
                "age_seconds": round(age_seconds, 3),
                "order_id_suffix": str(order.get("order_id", ""))[-8:],
            })
    return stale


def latest_log_findings(summary: dict[str, Any] | None) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    if summary:
        warnings.extend(str(item) for item in summary.get("warnings", []) if item)
        errors.extend(str(item) for item in summary.get("errors", []) if item)
        hb_log = summary.get("hb_log_path")
        if hb_log:
            log_path = REPO_ROOT / str(hb_log)
            _, hits = scan_new_log_errors(log_path, 0)
            # The supervisor stops Hummingbot with SIGINT at the planned runtime;
            # the no-MQTT wrapper logs that expected shutdown as a bare traceback
            # ending in KeyboardInterrupt. Keep real tracebacks/errors, but do not
            # mark a clean bounded stop unhealthy just because of that wrapper exit.
            log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
            expected_keyboard_interrupt = "KeyboardInterrupt" in log_text and summary.get("stop_reason") == "runtime_complete"
            for hit in hits:
                if expected_keyboard_interrupt and hit.strip() == "Traceback (most recent call last):":
                    continue
                errors.append(hit)
    recurring_blockers = [
        text for text in warnings + errors
        if "last traded price" in text.lower() or "divisionbyzero" in text.lower() or "division by zero" in text.lower()
    ]
    return {
        "warnings": warnings[:20],
        "errors": errors[:20],
        "recurring_blockers": recurring_blockers[:20],
    }


def order_alerts(
    orders: list[dict[str, Any]],
    stale: list[dict[str, Any]],
    live_supervised: bool = False,
    basis_allowed_sides: list[str] | None = None,
) -> list[str]:
    alerts: list[str] = []
    if len(orders) > MAX_OPEN_ORDERS:
        alerts.append(f"too_many_open_orders:{len(orders)}>{MAX_OPEN_ORDERS}")
    total_remaining = sum(dec(order.get("remaining_amount")) for order in orders)
    max_remaining = max([dec(order.get("remaining_amount")) for order in orders] or [Decimal("0")])
    if total_remaining > MAX_TOTAL_REMAINING_ZEC:
        alerts.append(f"total_remaining_exceeds_bound:{total_remaining}>{MAX_TOTAL_REMAINING_ZEC}")
    if max_remaining > MAX_SINGLE_ORDER_ZEC:
        alerts.append(f"single_order_exceeds_bound:{max_remaining}>{MAX_SINGLE_ORDER_ZEC}")
    if stale and not live_supervised:
        alerts.append(f"stale_orders:{len(stale)}")
    if basis_allowed_sides is not None:
        allowed = {side.lower() for side in basis_allowed_sides}
        invalid_sides = sorted({str(order.get("side", "")).lower() for order in orders if str(order.get("side", "")).lower() not in allowed})
        if invalid_sides:
            alerts.append(f"open_order_side_violates_basis_policy:{','.join(invalid_sides)}")
    return alerts


def latest_basis_policy(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    policy = summary.get("basis_policy") or {}
    if isinstance(policy.get("latest"), dict):
        return policy.get("latest") or {}
    if isinstance(policy.get("preflight"), dict):
        return policy.get("preflight") or {}
    if isinstance(policy, dict):
        return policy
    return {}


def build_status() -> dict[str, Any]:
    gemini = GeminiPrivate()
    snapshot = portfolio_snapshot(gemini)
    orders = open_zec_orders(gemini)
    buys = [order for order in orders if str(order.get("side", "")).lower() == "buy"]
    sells = [order for order in orders if str(order.get("side", "")).lower() == "sell"]
    stale = stale_orders(orders)
    summary_path, summary = latest_summary()
    latest_policy = latest_basis_policy(summary)
    findings = latest_log_findings(summary)
    processes = process_snapshot()
    heartbeat = heartbeat_state()
    coinbase = coinbase_mid()
    gemini = snapshot["mid"]
    basis_bp = Decimal("0") if coinbase == 0 else (gemini - coinbase) / coinbase * Decimal("10000")
    basis_direction = basis_direction_label(basis_bp)
    policy_allowed_sides = latest_policy.get("allowed_sides") if isinstance(latest_policy.get("allowed_sides"), list) else None

    live_supervised = processes.active_supervised_v2 and not heartbeat["stale"]
    alerts: list[str] = []
    alerts.extend(order_alerts(orders, stale, live_supervised=live_supervised, basis_allowed_sides=policy_allowed_sides))
    if live_supervised:
        if len(processes.headless) != 1:
            alerts.append(f"unexpected_live_headless_processes:{len(processes.headless)}")
        if len(processes.runner) > 2:
            alerts.append(f"duplicate_live_runner_processes:{len(processes.runner)}")
        if processes.legacy:
            alerts.append(f"unexpected_legacy_hummingbot_processes:{len(processes.legacy)}")
    else:
        if len(processes.hummingbot) > 1:
            alerts.append(f"duplicate_hummingbot_processes:{len(processes.hummingbot)}")
    if processes.hummingbot and not processes.watchdog:
        alerts.append("hummingbot_running_without_deadman")
    if processes.watchdog and heartbeat["stale"]:
        alerts.append("watchdog_heartbeat_stale")
    if not live_supervised and findings["warnings"]:
        alerts.append("latest_warning_present")
    if not live_supervised and findings["errors"]:
        alerts.append("latest_error_present")
    if not live_supervised and findings["recurring_blockers"]:
        alerts.append("recurring_log_blocker")
    if not live_supervised and summary and summary.get("final_open_orders") not in (0, "0", None):
        alerts.append(f"latest_summary_final_open_orders:{summary.get('final_open_orders')}")
    if not live_supervised and summary and summary.get("stop_reason") not in ("runtime_complete", None):
        alerts.append(f"latest_summary_stop_reason:{summary.get('stop_reason')}")
    if coinbase <= 0:
        alerts.append("basis_policy_stale")
    elif abs(basis_bp) >= BASIS_STATUS_CONFIG.extreme_halt_bp:
        alerts.append(f"basis_policy_halt_extreme:{basis_bp:.2f}bp")
    elif abs(basis_bp) > BASIS_STATUS_CONFIG.normal_bp:
        state = latest_policy.get("state")
        if state == "gemini_rich_sell_only":
            alerts.append(f"basis_directional_sell_only:{basis_bp:.2f}bp")
        elif state == "gemini_cheap_buy_only":
            alerts.append(f"basis_directional_buy_only:{basis_bp:.2f}bp")
        else:
            alerts.append(f"basis_policy_unconfirmed:{basis_bp:.2f}bp")

    read = "Live supervised healthy" if live_supervised and not alerts else ("Healthy" if not alerts and len(orders) == 0 else "stop condition")
    return {
        "schema_version": DASHBOARD_VERSION,
        "generated_at_epoch": int(time.time()),
        "mode": "phase5_read_only_status_no_orders_placed",
        "live_supervised": live_supervised,
        "portfolio": {
            "mid": snapshot["mid"],
            "coinbase_mid": coinbase,
            "basis_bp": basis_bp,
            "basis_direction": basis_direction,
            "basis_policy_state": latest_policy.get("state"),
            "basis_policy_reason": latest_policy.get("reason"),
            "USD": snapshot["USD"],
            "ZEC": snapshot["ZEC"],
            "zec_value_usd": snapshot["ZEC"] * snapshot["mid"],
            "total_value_usd": snapshot["value"],
            "usd_pct": pct(snapshot["USD"], snapshot["value"]),
            "zec_pct": pct(snapshot["ZEC"] * snapshot["mid"], snapshot["value"]),
        },
        "open_orders": {
            "count": len(orders),
            "buys": len(buys),
            "sells": len(sells),
            "total_remaining_zec": sum(dec(order.get("remaining_amount")) for order in orders),
            "stale_problem_orders": stale,
            "orders": sanitize_orders(orders),
        },
        "latest_live_summary": {
            "path": str(summary_path.relative_to(REPO_ROOT)) if summary_path else None,
            "run_id": summary.get("run_id") if summary else None,
            "stop_reason": summary.get("stop_reason") if summary else None,
            "runtime_seconds_requested": summary.get("runtime_seconds_requested") if summary else None,
            "trades": summary.get("trades") if summary else None,
            "portfolio_pnl_usd": summary.get("portfolio_pnl_usd") if summary else None,
            "buy_hold_pnl_usd": summary.get("buy_hold_pnl_usd") if summary else None,
            "mm_alpha_usd": summary.get("mm_alpha_usd") if summary else None,
            "final_open_orders": summary.get("final_open_orders") if summary else None,
            "warnings": summary.get("warnings", []) if summary else [],
            "errors": summary.get("errors", []) if summary else [],
            "basis_policy": latest_policy,
        },
        "warnings_errors": findings,
        "processes": {
            "hummingbot": processes.hummingbot,
            "watchdog": processes.watchdog,
            "runner": processes.runner,
            "headless": processes.headless,
            "legacy": processes.legacy,
        },
        "watchdog": {
            "required_when_live": True,
            "processes": processes.watchdog,
            "heartbeat": heartbeat,
            "status": "armed" if processes.watchdog else ("not_running_ok_no_live_process" if not processes.hummingbot else "missing"),
        },
        "alerts": alerts,
        "go_no_go": "go_for_supervised_mini_ops" if read in ("Healthy", "Live supervised healthy") else "no_go",
        "read": read,
    }


def fmt_money(value: Any, places: int = 2) -> str:
    return f"${dec(value):,.{places}f}"


def fmt_decimal(value: Any, places: int = 6) -> str:
    return f"{dec(value):,.{places}f}"


def dashboard_markdown(status: dict[str, Any]) -> str:
    portfolio = status["portfolio"]
    orders = status["open_orders"]
    latest = status["latest_live_summary"]
    watchdog = status["watchdog"]
    alerts = status["alerts"]
    stale_label = "none" if not orders["stale_problem_orders"] else "yes"
    return "\n".join([
        "# Gemini ZEC V2 Phase 5 Ops Dashboard",
        "",
        f"Generated epoch: `{status['generated_at_epoch']}`",
        f"Read: **{status['read']}** (`{status['go_no_go']}`)",
        f"Live supervised: `{status.get('live_supervised')}`",
        "",
        "## Portfolio",
        f"- Total: **{fmt_money(portfolio['total_value_usd'])}**",
        f"- USD: {fmt_money(portfolio['USD'])} ({fmt_decimal(portfolio['usd_pct'], 2)}%)",
        f"- ZEC: {fmt_decimal(portfolio['ZEC'], 6)} ZEC = {fmt_money(portfolio['zec_value_usd'])} ({fmt_decimal(portfolio['zec_pct'], 2)}%)",
        f"- Gemini mid: {fmt_money(portfolio['mid'])}; Coinbase mid: {fmt_money(portfolio['coinbase_mid'])}; signed basis: {fmt_decimal(portfolio['basis_bp'], 2)} bp ({portfolio['basis_direction']}); policy: `{portfolio.get('basis_policy_state')}`",
        "",
        "## Open Orders",
        f"- Buys: {orders['buys']}",
        f"- Sells: {orders['sells']}",
        f"- Stale/problem orders: {stale_label}",
        f"- Total remaining: {orders['total_remaining_zec']} ZEC",
        "",
        "## Latest Live Summary",
        f"- Path: `{latest['path']}`",
        f"- Stop reason: `{latest['stop_reason']}`",
        f"- Final open orders: {latest['final_open_orders']}",
        f"- Trades: `{json.dumps(latest['trades'], default=decimal_default, sort_keys=True)}`",
        f"- PnL / hold / alpha: {latest['portfolio_pnl_usd']} / {latest['buy_hold_pnl_usd']} / {latest['mm_alpha_usd']}",
        "",
        "## Watchdog / Heartbeat",
        f"- Status: `{watchdog['status']}`",
        f"- Processes: `{watchdog['processes']}`",
        f"- Heartbeat: `{watchdog['heartbeat']}`",
        "",
        "## Alerts",
        *(f"- {alert}" for alert in alerts),
        *(["- none"] if not alerts else []),
        "",
        "## Cron Proposal (not enabled)",
        "```cron",
        "*/5 * * * * cd /Users/maverick/hummingbot && /opt/homebrew/bin/micromamba run -n hummingbot python scripts/maverick/gemini_zec_phase5_status.py --check --write-json logs/gemini_zec_phase5_status_latest.json --write-dashboard docs/maverick/gemini_zec_phase5_ops_dashboard.md >> logs/gemini_zec_phase5_status_cron.log 2>&1",
        "```",
        "",
        "Keep cron disabled until Eric explicitly approves unattended production mini ops.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when any stop-condition alert is present")
    parser.add_argument("--write-dashboard", nargs="?", const=str(DEFAULT_DASHBOARD_PATH), help="Write markdown dashboard")
    parser.add_argument("--write-json", nargs="?", const=str(DEFAULT_JSON_PATH), help="Write JSON status snapshot")
    args = parser.parse_args()

    status = build_status()
    if args.write_json:
        path = Path(args.write_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status, indent=2, sort_keys=True, default=decimal_default) + "\n")
    if args.write_dashboard:
        path = Path(args.write_dashboard)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dashboard_markdown(status))

    if args.format == "json":
        print(json.dumps(status, indent=2, sort_keys=True, default=decimal_default))
    else:
        print(dashboard_markdown(status))

    return 2 if args.check and status["read"] != "Healthy" else 0


if __name__ == "__main__":
    raise SystemExit(main())
