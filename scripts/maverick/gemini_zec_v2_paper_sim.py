#!/usr/bin/env python3
"""Dry-run the Gemini ZEC V2 tiny PMM controller without placing live orders."""

import argparse
import asyncio
import fcntl
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

from controllers.market_making.gemini_zec_pmm_tiny import GeminiZECTinyPMMConfig, GeminiZECTinyPMMController
from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.data_types import PositionSummary


class StaticMarketDataProvider:
    def __init__(self, mid_price: Decimal):
        self.mid_price = mid_price
        self.initialized_rate_sources = []

    def initialize_rate_sources(self, connector_pairs):
        self.initialized_rate_sources.extend(connector_pairs)

    def get_price_by_type(self, connector_name, trading_pair, price_type):
        return self.mid_price

    def time(self):
        return time.time()


def decimal_or_none(value: Optional[str]) -> Optional[Decimal]:
    return Decimal(value) if value is not None else None


async def run(args):
    provider = StaticMarketDataProvider(mid_price=Decimal(args.mid))
    config = GeminiZECTinyPMMConfig(
        id=args.controller_id,
        order_amount_base=Decimal(args.order_amount_base),
        max_order_size_base=Decimal(args.max_order_size_base),
        target_base_amount=decimal_or_none(args.target_base_amount),
        max_inventory_deviation_base=Decimal(args.max_inventory_deviation_base),
        external_mid_reference=decimal_or_none(args.external_mid),
        max_external_mid_deviation_pct=Decimal(args.max_external_mid_deviation_pct),
        max_mid_move_pct=Decimal(args.max_mid_move_pct),
        manual_pause=args.manual_pause,
    )
    controller = GeminiZECTinyPMMController(config=config, market_data_provider=provider, actions_queue=asyncio.Queue())
    if args.base_position is not None:
        controller.positions_held = [PositionSummary(
            connector_name=config.connector_name,
            trading_pair=config.trading_pair,
            volume_traded_quote=Decimal("0"),
            side=TradeType.BUY,
            amount=Decimal(args.base_position),
            breakeven_price=Decimal(args.mid),
            unrealized_pnl_quote=Decimal("0"),
            realized_pnl_quote=Decimal("0"),
            cum_fees_quote=Decimal("0"),
        )]

    await controller.update_processed_data()
    actions = controller.determine_executor_actions()
    quote_snapshot = controller.quote_snapshot_from_actions(actions)
    metrics = controller.get_custom_info()
    return {
        "mode": "paper_sim_dry_run_no_live_orders",
        "controller_id": config.id,
        "metrics": metrics,
        "quote_snapshot": quote_snapshot,
        "action_count": len(actions),
        "matched_v1_tiny_shape": (
            set(quote_snapshot.keys()) == {"buy_0", "sell_0"}
            and all(item["execution_strategy"] == "LIMIT_MAKER" for item in quote_snapshot.values())
            and all(item["amount"] == str(Decimal(args.max_order_size_base)) for item in quote_snapshot.values())
            and not metrics["paused"]
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mid", default="562.335")
    parser.add_argument("--external-mid")
    parser.add_argument("--order-amount-base", default="0.002")
    parser.add_argument("--max-order-size-base", default="0.002")
    parser.add_argument("--target-base-amount")
    parser.add_argument("--base-position")
    parser.add_argument("--max-inventory-deviation-base", default="0.010")
    parser.add_argument("--max-external-mid-deviation-pct", default="0.01")
    parser.add_argument("--max-mid-move-pct", default="0.02")
    parser.add_argument("--manual-pause", action="store_true")
    parser.add_argument("--controller-id", default="gemini_zec_v2_tiny_paper")
    parser.add_argument("--lock-file", default="logs/gemini_zec_v2_paper_sim.lock")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    Path(args.lock_file).parent.mkdir(parents=True, exist_ok=True)
    with open(args.lock_file, "w") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(f"duplicate_process_guard_active: {args.lock_file}") from exc

        result = asyncio.run(run(args))
        payload = json.dumps(result, indent=2, sort_keys=True)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n")
        print(payload)


if __name__ == "__main__":
    main()
