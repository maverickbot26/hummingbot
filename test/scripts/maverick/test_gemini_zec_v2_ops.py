import urllib.error
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from scripts.maverick.gemini_zec_basis_policy import (
    STATE_CHEAP_BUY_ONLY,
    STATE_HALT_EXTREME,
    STATE_HALT_STALE,
    STATE_HALT_SUSTAINED_WIDE,
    STATE_HALT_UNCONFIRMED,
    STATE_NORMAL,
    STATE_RICH_SELL_ONLY,
    decide_basis_state,
    make_basis_sample,
    signed_basis_bp,
)
from scripts.maverick.gemini_zec_deadman_watchdog import cancel_all_or_reconcile_zec
from scripts.maverick.run_gemini_zec_v2_tiny_live_smoke import validate_deadman_heartbeat_config


class GeminiZECBasisPolicyTests(TestCase):

    def samples(self, values):
        return [make_basis_sample(ts, gemini, Decimal("100")) for ts, gemini in values]

    def test_signed_basis_positive_negative_zero_and_invalid(self):
        self.assertEqual(Decimal("30.0"), signed_basis_bp(Decimal("100.30"), Decimal("100")))
        self.assertEqual(Decimal("-30.0"), signed_basis_bp(Decimal("99.70"), Decimal("100")))
        self.assertEqual(Decimal("0"), signed_basis_bp(Decimal("100"), Decimal("100")))
        with self.assertRaises(ValueError):
            signed_basis_bp(Decimal("100"), Decimal("0"))

    def test_normal_basis_is_two_sided(self):
        decision = decide_basis_state(self.samples([(100, Decimal("100.20"))]), now=Decimal("100"))

        self.assertEqual(STATE_NORMAL, decision.state)
        self.assertEqual(["BUY", "SELL"], decision.allowed_sides)
        self.assertFalse(decision.halt)

    def test_rich_confirmed_is_sell_only_after_three_samples_and_30s(self):
        decision = decide_basis_state(self.samples([(70, Decimal("100.31")), (85, Decimal("100.32")), (100, Decimal("100.33"))]), now=Decimal("100"))

        self.assertEqual(STATE_RICH_SELL_ONLY, decision.state)
        self.assertEqual(["SELL"], decision.allowed_sides)
        self.assertEqual(["BUY"], decision.suppress_sides)
        self.assertTrue(decision.confirmed)

    def test_modest_rich_probe_above_25bp_is_sell_only_after_confirmation(self):
        decision = decide_basis_state(self.samples([(70, Decimal("100.26")), (85, Decimal("100.27")), (100, Decimal("100.28"))]), now=Decimal("100"))

        self.assertEqual(STATE_RICH_SELL_ONLY, decision.state)
        self.assertEqual(["SELL"], decision.allowed_sides)
        self.assertEqual("gemini_rich_probe_sell_only:28.0000bp", decision.reason)
        self.assertTrue(decision.confirmed)

    def test_cheap_confirmed_is_buy_only_after_three_samples_and_30s(self):
        decision = decide_basis_state(self.samples([(70, Decimal("99.69")), (85, Decimal("99.68")), (100, Decimal("99.67"))]), now=Decimal("100"))

        self.assertEqual(STATE_CHEAP_BUY_ONLY, decision.state)
        self.assertEqual(["BUY"], decision.allowed_sides)
        self.assertEqual(["SELL"], decision.suppress_sides)
        self.assertTrue(decision.confirmed)

    def test_modest_cheap_probe_below_25bp_is_buy_only_after_confirmation(self):
        decision = decide_basis_state(self.samples([(70, Decimal("99.74")), (85, Decimal("99.73")), (100, Decimal("99.72"))]), now=Decimal("100"))

        self.assertEqual(STATE_CHEAP_BUY_ONLY, decision.state)
        self.assertEqual(["BUY"], decision.allowed_sides)
        self.assertEqual("gemini_cheap_probe_buy_only:-28.0000bp", decision.reason)
        self.assertTrue(decision.confirmed)

    def test_previous_directional_state_stays_valid_with_fresh_same_side_sample(self):
        decision = decide_basis_state(
            self.samples([(100, Decimal("100.27"))]),
            now=Decimal("100"),
            previous_state=STATE_RICH_SELL_ONLY,
        )

        self.assertEqual(STATE_RICH_SELL_ONLY, decision.state)
        self.assertEqual(["SELL"], decision.allowed_sides)
        self.assertFalse(decision.halt)

    def test_unconfirmed_elevated_basis_halts(self):
        decision = decide_basis_state(self.samples([(100, Decimal("100.28"))]), now=Decimal("100"))

        self.assertEqual(STATE_HALT_UNCONFIRMED, decision.state)
        self.assertTrue(decision.halt)
        self.assertEqual([], decision.allowed_sides)

    def test_stale_basis_halts(self):
        decision = decide_basis_state(self.samples([(90, Decimal("100.20"))]), now=Decimal("100"))

        self.assertEqual(STATE_HALT_STALE, decision.state)
        self.assertTrue(decision.halt)

    def test_sustained_wide_basis_halts_after_60s(self):
        decision = decide_basis_state(self.samples([(40, Decimal("100.80")), (70, Decimal("100.82")), (100, Decimal("100.81"))]), now=Decimal("100"))

        self.assertEqual(STATE_HALT_SUSTAINED_WIDE, decision.state)
        self.assertTrue(decision.halt)

    def test_wide_not_yet_sustained_basis_does_not_probe_quote(self):
        decision = decide_basis_state(self.samples([(100, Decimal("100.80"))]), now=Decimal("100"))

        self.assertEqual(STATE_HALT_UNCONFIRMED, decision.state)
        self.assertEqual([], decision.allowed_sides)
        self.assertTrue(decision.halt)

    def test_extreme_basis_halts_immediately(self):
        decision = decide_basis_state(self.samples([(100, Decimal("101.00"))]), now=Decimal("100"))

        self.assertEqual(STATE_HALT_EXTREME, decision.state)
        self.assertTrue(decision.halt)


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
