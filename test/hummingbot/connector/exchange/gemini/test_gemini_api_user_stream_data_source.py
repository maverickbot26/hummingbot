import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock

from hummingbot.connector.exchange.gemini import gemini_constants as CONSTANTS
from hummingbot.connector.exchange.gemini.gemini_api_user_stream_data_source import GeminiAPIUserStreamDataSource
from hummingbot.connector.exchange.gemini.gemini_auth import GeminiAuth


class GeminiAPIUserStreamDataSourceTests(TestCase):

    def test_convert_legacy_ack_and_heartbeat_are_skipped(self):
        self.assertIsNone(GeminiAPIUserStreamDataSource._convert_legacy_order_event({"type": "subscription_ack"}))
        self.assertIsNone(GeminiAPIUserStreamDataSource._convert_legacy_order_event({"type": "heartbeat"}))

    def test_convert_legacy_fill_event_to_execution_report_shape(self):
        event = {
            "type": "fill",
            "timestampms": 1_778_501_269_003,
            "symbol": "zecusd",
            "order_id": "73771280617163296",
            "client_order_id": "HBOT-ZEC-1",
            "side": "buy",
            "price": "568.75",
            "original_amount": "0.002",
            "executed_amount": "0.001",
            "remaining_amount": "0.001",
            "fill": {
                "price": "568.75",
                "trade_id": "trade-1",
            },
        }

        converted = GeminiAPIUserStreamDataSource._convert_legacy_order_event(event)

        self.assertEqual(1_778_501_269_003, converted["E"])
        self.assertEqual("zecusd", converted["s"])
        self.assertEqual("73771280617163296", converted["i"])
        self.assertEqual("HBOT-ZEC-1", converted["c"])
        self.assertEqual("BUY", converted["S"])
        self.assertEqual("PARTIALLY_FILLED", converted["X"])
        self.assertEqual("568.75", converted["p"])
        self.assertEqual("0.002", converted["q"])
        self.assertEqual("0.001", converted["Z"])
        self.assertEqual("568.75", converted["L"])
        self.assertEqual("trade-1", converted["t"])

    def test_convert_legacy_closed_cancelled_event(self):
        converted = GeminiAPIUserStreamDataSource._convert_legacy_order_event({
            "type": "closed",
            "timestampms": 1_778_501_269_003,
            "symbol": "zecusd",
            "order_id": "73771280617163296",
            "client_order_id": "HBOT-ZEC-1",
            "side": "buy",
            "price": "568.75",
            "original_amount": "0.002",
            "executed_amount": "0",
            "is_cancelled": True,
        })

        self.assertEqual("cancelled", converted["X"])
        self.assertEqual("0", converted["Z"])

    def test_process_event_message_handles_batched_legacy_events(self):
        queue = asyncio.Queue()
        events = [
            {"type": "heartbeat"},
            {
                "type": "booked",
                "timestampms": 1_778_501_269_003,
                "symbol": "zecusd",
                "order_id": "73771280617163296",
                "client_order_id": "HBOT-ZEC-1",
                "side": "sell",
                "price": "569.32",
                "original_amount": "0.002",
                "executed_amount": "0",
            },
        ]

        data_source = GeminiAPIUserStreamDataSource(
            auth=MagicMock(),
            trading_pairs=["ZEC-USD"],
            connector=MagicMock(),
            api_factory=MagicMock(),
        )

        asyncio.get_event_loop().run_until_complete(data_source._process_event_message(events, queue))

        self.assertEqual(1, queue.qsize())
        converted = queue.get_nowait()
        self.assertEqual("accepted", converted["X"])
        self.assertEqual("SELL", converted["S"])

    def test_connected_websocket_uses_legacy_order_events_url_and_auth_headers(self):
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = 1234567890.0
        auth = GeminiAuth(api_key="testApiKey", secret_key="testSecret", time_provider=mock_time_provider)
        connector = MagicMock()
        connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="zecusd")
        ws = AsyncMock()
        api_factory = MagicMock()
        api_factory.get_ws_assistant = AsyncMock(return_value=ws)
        data_source = GeminiAPIUserStreamDataSource(
            auth=auth,
            trading_pairs=["ZEC-USD"],
            connector=connector,
            api_factory=api_factory,
        )

        asyncio.get_event_loop().run_until_complete(data_source._connected_websocket_assistant())

        ws.connect.assert_awaited_once()
        kwargs = ws.connect.await_args.kwargs
        self.assertEqual(f"{CONSTANTS.WSS_ORDER_EVENTS_URL}?symbolFilter=zecusd&heartbeat=true", kwargs["ws_url"])
        self.assertEqual(CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL, kwargs["ping_timeout"])
        self.assertIn("X-GEMINI-APIKEY", kwargs["ws_headers"])
        self.assertIn("X-GEMINI-PAYLOAD", kwargs["ws_headers"])
        self.assertIn("X-GEMINI-SIGNATURE", kwargs["ws_headers"])
        self.assertNotIn("X-GEMINI-NONCE", kwargs["ws_headers"])
