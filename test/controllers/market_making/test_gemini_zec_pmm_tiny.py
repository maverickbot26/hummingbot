import asyncio
from decimal import Decimal
from unittest import TestCase

from controllers.market_making.gemini_zec_pmm_tiny import GeminiZECTinyPMMConfig, GeminiZECTinyPMMController
from hummingbot.core.data_type.common import PriceType, TradeType
from hummingbot.strategy_v2.executors.data_types import PositionSummary
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class StaticMarketDataProvider:
    def __init__(self, price: Decimal):
        self.price = price
        self.initialized_rate_sources = []

    def initialize_rate_sources(self, connector_pairs):
        self.initialized_rate_sources.extend(connector_pairs)

    def get_price_by_type(self, connector_name, trading_pair, price_type: PriceType):
        return self.price

    def time(self):
        return 1_778_501_269.003


class GeminiZECTinyPMMControllerTests(TestCase):

    def make_controller(self, price=Decimal("562.335"), **config_kwargs):
        config_kwargs.setdefault("id", "gemini_zec_test")
        config = GeminiZECTinyPMMConfig(**config_kwargs)
        return GeminiZECTinyPMMController(
            config=config,
            market_data_provider=StaticMarketDataProvider(Decimal(str(price))),
            actions_queue=asyncio.Queue(),
        )

    def async_run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def make_position(self, amount: Decimal, price=Decimal("562.335")):
        return PositionSummary(
            connector_name="gemini",
            trading_pair="ZEC-USD",
            volume_traded_quote=Decimal("0"),
            side=TradeType.BUY,
            amount=amount,
            breakeven_price=price,
            unrealized_pnl_quote=Decimal("0"),
            realized_pnl_quote=Decimal("0"),
            cum_fees_quote=Decimal("0"),
        )

    def test_quote_shape_matches_v1_tiny_maker_only(self):
        controller = self.make_controller()
        self.async_run(controller.update_processed_data())

        actions = controller.determine_executor_actions()
        self.assertEqual(2, len(actions))
        self.assertTrue(all(isinstance(action, CreateExecutorAction) for action in actions))

        by_level = {action.executor_config.level_id: action.executor_config for action in actions}
        self.assertEqual({"buy_0", "sell_0"}, set(by_level))
        self.assertEqual(TradeType.BUY, by_level["buy_0"].side)
        self.assertEqual(TradeType.SELL, by_level["sell_0"].side)
        self.assertEqual(ExecutionStrategy.LIMIT_MAKER, by_level["buy_0"].execution_strategy)
        self.assertEqual(ExecutionStrategy.LIMIT_MAKER, by_level["sell_0"].execution_strategy)
        self.assertEqual(Decimal("0.002"), by_level["buy_0"].amount)
        self.assertEqual(Decimal("0.002"), by_level["sell_0"].amount)
        self.assertEqual(Decimal("561.772665"), by_level["buy_0"].price)
        self.assertEqual(Decimal("562.897335"), by_level["sell_0"].price)

        metrics = controller.get_custom_info()
        self.assertEqual(1, metrics["metrics_schema_version"])
        self.assertFalse(metrics["paused"])
        self.assertTrue(metrics["zero_fee_accounting"])
        self.assertEqual("0.002", metrics["effective_order_size_base"])

    def test_hard_max_order_size_caps_oversized_config(self):
        controller = self.make_controller(order_amount_base=Decimal("0.010"), max_order_size_base=Decimal("0.002"))
        self.async_run(controller.update_processed_data())

        actions = controller.determine_executor_actions()

        self.assertEqual(2, len(actions))
        self.assertTrue(all(isinstance(action.executor_config, OrderExecutorConfig) for action in actions))
        self.assertTrue(all(action.executor_config.amount == Decimal("0.002") for action in actions))
        self.assertEqual("0.002", controller.get_custom_info()["effective_order_size_base"])

    def test_inventory_guard_suppresses_buy_when_base_above_target_band(self):
        controller = self.make_controller(target_base_amount=Decimal("0.500"), max_inventory_deviation_base=Decimal("0.010"))
        controller.positions_held = [self.make_position(Decimal("0.520"))]
        self.async_run(controller.update_processed_data())

        actions = controller.determine_executor_actions()

        self.assertEqual(["sell_0"], [action.executor_config.level_id for action in actions])
        self.assertEqual(["BUY"], controller.get_custom_info()["inventory_suppressed_sides"])

    def test_inventory_guard_suppresses_sell_when_base_below_target_band(self):
        controller = self.make_controller(target_base_amount=Decimal("0.500"), max_inventory_deviation_base=Decimal("0.010"))
        controller.positions_held = [self.make_position(Decimal("0.480"))]
        self.async_run(controller.update_processed_data())

        actions = controller.determine_executor_actions()

        self.assertEqual(["buy_0"], [action.executor_config.level_id for action in actions])
        self.assertEqual(["SELL"], controller.get_custom_info()["inventory_suppressed_sides"])

    def test_external_mid_sanity_pause_blocks_quotes(self):
        controller = self.make_controller(external_mid_reference=Decimal("600"), max_external_mid_deviation_pct=Decimal("0.01"))
        self.async_run(controller.update_processed_data())

        self.assertEqual([], controller.determine_executor_actions())
        metrics = controller.get_custom_info()
        self.assertTrue(metrics["paused"])
        self.assertIn("external_mid_deviation", metrics["pause_reasons"])

    def test_volatility_pause_blocks_second_tick_after_large_mid_move(self):
        controller = self.make_controller(price=Decimal("562.335"), max_mid_move_pct=Decimal("0.01"))
        self.async_run(controller.update_processed_data())
        self.assertEqual(2, len(controller.determine_executor_actions()))

        controller.market_data_provider.price = Decimal("575")
        self.async_run(controller.update_processed_data())

        self.assertEqual([], controller.determine_executor_actions())
        self.assertIn("volatility_pause", controller.get_custom_info()["pause_reasons"])
