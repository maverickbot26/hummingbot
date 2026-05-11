from decimal import Decimal
from unittest import TestCase

from scripts.maverick.gemini_zec_phase5_status import order_alerts, pct, stale_orders


class GeminiZECPhase5StatusTests(TestCase):

    def test_pct_handles_zero_total(self):
        self.assertEqual(Decimal("0"), pct(Decimal("10"), Decimal("0")))

    def test_stale_orders_flags_old_open_order_without_full_order_id(self):
        orders = [{
            "side": "buy",
            "price": "560.00",
            "remaining_amount": "0.002",
            "timestampms": "1778500000000",
            "order_id": "1234567890",
        }]

        stale = stale_orders(orders, now_ms=1778501001000)

        self.assertEqual(1, len(stale))
        self.assertEqual("buy", stale[0]["side"])
        self.assertEqual("34567890", stale[0]["order_id_suffix"])
        self.assertGreater(stale[0]["age_seconds"], 600)

    def test_order_alerts_enforces_phase5_tiny_bounds(self):
        alerts = order_alerts(
            orders=[
                {"side": "buy", "remaining_amount": "0.002"},
                {"side": "sell", "remaining_amount": "0.002"},
                {"side": "sell", "remaining_amount": "0.002"},
            ],
            stale=[],
        )

        self.assertIn("too_many_open_orders:3>2", alerts)
        self.assertIn("total_remaining_exceeds_bound:0.006>0.0055", alerts)

    def test_order_alerts_flags_single_order_and_stale(self):
        alerts = order_alerts(
            orders=[{"side": "buy", "remaining_amount": "0.003"}],
            stale=[{"side": "buy"}],
        )

        self.assertIn("single_order_exceeds_bound:0.003>0.0025", alerts)
        self.assertIn("stale_orders:1", alerts)
