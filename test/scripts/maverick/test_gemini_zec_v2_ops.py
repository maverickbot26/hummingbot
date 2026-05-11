import urllib.error
from unittest import TestCase
from unittest.mock import patch

from scripts.maverick.gemini_zec_deadman_watchdog import cancel_all_or_reconcile_zec
from scripts.maverick.run_gemini_zec_v2_tiny_live_smoke import validate_deadman_heartbeat_config


class GeminiZECV2OpsTests(TestCase):

    def test_long_check_interval_is_safe_with_independent_heartbeat(self):
        validate_deadman_heartbeat_config(
            check_seconds=300,
            heartbeat_seconds=10,
            deadman_stale_seconds=45,
        )

    def test_heartbeat_must_be_well_below_deadman_stale_threshold(self):
        with self.assertRaisesRegex(ValueError, "heartbeat_seconds"):
            validate_deadman_heartbeat_config(
                check_seconds=300,
                heartbeat_seconds=20,
                deadman_stale_seconds=45,
            )

    def test_deadman_treats_cancel_all_400_as_success_when_no_zec_orders_remain(self):
        http_400 = urllib.error.HTTPError(
            url="https://api.gemini.com/v1/order/cancel/all",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.maverick.gemini_zec_deadman_watchdog.gemini_private_post") as post:
            post.side_effect = [http_400, [], []]

            result = cancel_all_or_reconcile_zec()

        self.assertTrue(result["ok"])
        self.assertEqual("HTTPError", result["cancel_all_error"])
        self.assertEqual(0, result["fallback_reconcile"]["final_open_orders"])

    def test_deadman_falls_back_to_individual_cancel_and_reconciles(self):
        http_400 = urllib.error.HTTPError(
            url="https://api.gemini.com/v1/order/cancel/all",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        open_order = {
            "symbol": "zecusd",
            "order_id": "1234567890",
            "side": "sell",
            "remaining_amount": "0.002",
            "price": "557.13",
        }

        def fake_post(path, extra=None):
            if path == "/v1/order/cancel/all":
                raise http_400
            if path == "/v1/orders":
                fake_post.order_calls += 1
                return [open_order] if fake_post.order_calls == 1 else []
            if path == "/v1/order/cancel":
                return {"is_cancelled": True}
            raise AssertionError(path)

        fake_post.order_calls = 0
        with patch("scripts.maverick.gemini_zec_deadman_watchdog.gemini_private_post", side_effect=fake_post):
            result = cancel_all_or_reconcile_zec()

        self.assertTrue(result["ok"])
        fallback = result["fallback_reconcile"]
        self.assertEqual(1, fallback["initial_open_orders"])
        self.assertEqual(0, fallback["final_open_orders"])
        self.assertEqual("34567890", fallback["cancel_results"][0]["order_id_suffix"])
        self.assertNotIn("order_id", fallback["cancel_results"][0])
