from decimal import Decimal
from typing import Dict, List, Optional, Set

from pydantic import Field

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction


class GeminiZECTinyPMMConfig(MarketMakingControllerConfigBase):
    """Tiny maker-only Gemini ZEC-USD V2 baseline for paper/sim gates.

    Defaults intentionally mirror the validated V1 tiny pilot:
    - one bid and one ask
    - 10 bp spread per side
    - 0.002 ZEC base size per order
    - maker-only order executor
    """

    controller_name: str = "gemini_zec_pmm_tiny"
    connector_name: str = "gemini"
    trading_pair: str = "ZEC-USD"
    buy_spreads: List[float] = Field(default=[0.001])
    sell_spreads: List[float] = Field(default=[0.001])
    buy_amounts_pct: List[Decimal] = Field(default=[Decimal("1")])
    sell_amounts_pct: List[Decimal] = Field(default=[Decimal("1")])
    order_amount_base: Decimal = Field(default=Decimal("0.002"), gt=Decimal("0"))
    max_order_size_base: Decimal = Field(default=Decimal("0.002"), gt=Decimal("0"))
    executor_refresh_time: int = 20
    cooldown_time: int = 20
    leverage: int = 1
    skip_rebalance: bool = True
    zero_fee_accounting: bool = True
    target_base_amount: Optional[Decimal] = None
    max_inventory_deviation_base: Decimal = Field(default=Decimal("0.010"), ge=Decimal("0"))
    external_mid_reference: Optional[Decimal] = None
    max_external_mid_deviation_pct: Decimal = Field(default=Decimal("0.01"), gt=Decimal("0"))
    volatility_pause_enabled: bool = True
    max_mid_move_pct: Decimal = Field(default=Decimal("0.02"), gt=Decimal("0"))
    manual_pause: bool = False
    one_shot_mode: bool = True
    metrics_schema_version: int = 1


class GeminiZECTinyPMMController(MarketMakingControllerBase):
    def __init__(self, config: GeminiZECTinyPMMConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config
        self._last_reference_price: Optional[Decimal] = None
        self._last_pause_reasons: List[str] = []
        self._last_inventory_suppressed_sides: List[str] = []
        self._last_quote_prices: Dict[str, str] = {}
        self._last_quote_amounts: Dict[str, str] = {}
        self._one_shot_started_level_ids: Set[str] = set()

    async def update_processed_data(self):
        await super().update_processed_data()
        reference_price = Decimal(str(self.processed_data["reference_price"]))
        pause_reasons: List[str] = []

        if self.config.manual_pause or self.config.manual_kill_switch:
            pause_reasons.append("manual_pause")

        if self.config.external_mid_reference is not None:
            external_mid = Decimal(str(self.config.external_mid_reference))
            deviation_pct = abs(reference_price - external_mid) / external_mid if external_mid != 0 else Decimal("Infinity")
            if deviation_pct > self.config.max_external_mid_deviation_pct:
                pause_reasons.append("external_mid_deviation")

        if self.config.volatility_pause_enabled and self._last_reference_price is not None:
            mid_move_pct = abs(reference_price - self._last_reference_price) / self._last_reference_price
            if mid_move_pct > self.config.max_mid_move_pct:
                pause_reasons.append("volatility_pause")

        self._last_reference_price = reference_price
        self._last_pause_reasons = pause_reasons
        self.processed_data.update({
            "paused": len(pause_reasons) > 0,
            "pause_reasons": pause_reasons,
            "zero_fee_accounting": self.config.zero_fee_accounting,
        })

    def create_actions_proposal(self) -> List[ExecutorAction]:
        if self.processed_data.get("paused"):
            return []
        actions = super().create_actions_proposal()
        if not self.config.one_shot_mode:
            return actions
        filtered_actions: List[ExecutorAction] = []
        for action in actions:
            if isinstance(action, CreateExecutorAction):
                level_id = action.executor_config.level_id
                if level_id in self._one_shot_started_level_ids:
                    continue
                if level_id is not None:
                    self._one_shot_started_level_ids.add(level_id)
            filtered_actions.append(action)
        return filtered_actions

    def get_levels_to_execute(self) -> List[str]:
        level_ids = super().get_levels_to_execute()
        suppressed_sides = self._inventory_suppressed_sides()
        self._last_inventory_suppressed_sides = suppressed_sides
        if not suppressed_sides:
            return level_ids
        return [level_id for level_id in level_ids if self.get_trade_type_from_level_id(level_id).name not in suppressed_sides]

    def get_price_and_amount(self, level_id: str):
        level = self.get_level_from_level_id(level_id)
        trade_type = self.get_trade_type_from_level_id(level_id)
        spreads = self.config.buy_spreads if trade_type == TradeType.BUY else self.config.sell_spreads
        reference_price = Decimal(str(self.processed_data["reference_price"]))
        spread_in_pct = Decimal(str(spreads[level])) * Decimal(str(self.processed_data["spread_multiplier"]))
        side_multiplier = Decimal("-1") if trade_type == TradeType.BUY else Decimal("1")
        price = reference_price * (Decimal("1") + side_multiplier * spread_in_pct)
        amount = min(self.config.order_amount_base, self.config.max_order_size_base)
        self._last_quote_prices[level_id] = str(price)
        self._last_quote_amounts[level_id] = str(amount)
        return price, amount

    def get_executor_config(self, level_id: str, price: Decimal, amount: Decimal):
        trade_type = self.get_trade_type_from_level_id(level_id)
        return OrderExecutorConfig(
            timestamp=self.market_data_provider.time(),
            controller_id=self.config.id,
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            side=trade_type,
            amount=amount,
            price=price,
            execution_strategy=ExecutionStrategy.LIMIT_MAKER,
            leverage=self.config.leverage,
            level_id=level_id,
        )

    def quote_snapshot_from_actions(self, actions: List[CreateExecutorAction]) -> Dict[str, Dict[str, str]]:
        snapshot: Dict[str, Dict[str, str]] = {}
        for action in actions:
            config = action.executor_config
            snapshot[config.level_id] = {
                "side": config.side.name,
                "price": str(config.price),
                "amount": str(config.amount),
                "execution_strategy": config.execution_strategy.name,
            }
        return snapshot

    def get_custom_info(self) -> Dict:
        return {
            "metrics_schema_version": self.config.metrics_schema_version,
            "connector_name": self.config.connector_name,
            "trading_pair": self.config.trading_pair,
            "reference_price": str(self.processed_data.get("reference_price", "")),
            "spread_multiplier": str(self.processed_data.get("spread_multiplier", "")),
            "paused": bool(self.processed_data.get("paused", False)),
            "pause_reasons": list(self.processed_data.get("pause_reasons", [])),
            "zero_fee_accounting": self.config.zero_fee_accounting,
            "order_amount_base": str(self.config.order_amount_base),
            "max_order_size_base": str(self.config.max_order_size_base),
            "effective_order_size_base": str(min(self.config.order_amount_base, self.config.max_order_size_base)),
            "target_base_amount": str(self.config.target_base_amount) if self.config.target_base_amount is not None else None,
            "max_inventory_deviation_base": str(self.config.max_inventory_deviation_base),
            "inventory_suppressed_sides": list(self._last_inventory_suppressed_sides),
            "one_shot_mode": self.config.one_shot_mode,
            "one_shot_started_level_ids": sorted(self._one_shot_started_level_ids),
            "last_quote_prices": dict(self._last_quote_prices),
            "last_quote_amounts": dict(self._last_quote_amounts),
        }

    def _inventory_suppressed_sides(self) -> List[str]:
        if self.config.target_base_amount is None:
            return []
        current_base = self.get_current_base_position()
        lower_bound = self.config.target_base_amount - self.config.max_inventory_deviation_base
        upper_bound = self.config.target_base_amount + self.config.max_inventory_deviation_base
        if current_base <= lower_bound:
            return [TradeType.SELL.name]
        if current_base >= upper_bound:
            return [TradeType.BUY.name]
        return []
