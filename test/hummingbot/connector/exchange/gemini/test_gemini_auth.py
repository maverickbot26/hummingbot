import asyncio
import base64
import hashlib
import hmac
import json
from unittest import TestCase
from unittest.mock import MagicMock

from typing_extensions import Awaitable

from hummingbot.connector.exchange.gemini import gemini_constants as CONSTANTS
from hummingbot.connector.exchange.gemini.gemini_auth import GeminiAuth
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest


class GeminiAuthTests(TestCase):

    def setUp(self) -> None:
        self._api_key = "testApiKey"
        self._secret = "testSecret"
        GeminiAuth._last_nonce = 0

    def async_run_with_timeout(self, coroutine: Awaitable, timeout: float = 1):
        ret = asyncio.get_event_loop().run_until_complete(asyncio.wait_for(coroutine, timeout))
        return ret

    def test_rest_authenticate(self):
        now = 1234567890.000
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = now

        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)

        params = {
            "request": "/v1/order/new",
            "symbol": "btcusd",
            "amount": "1.0",
            "price": "50000.00",
            "side": "buy",
            "type": "exchange limit",
        }

        request = RESTRequest(
            method=RESTMethod.POST,
            data=json.dumps(params),
            is_auth_required=True,
        )
        configured_request = self.async_run_with_timeout(auth.rest_authenticate(request))

        # Verify headers are set
        self.assertIn("X-GEMINI-APIKEY", configured_request.headers)
        self.assertIn("X-GEMINI-PAYLOAD", configured_request.headers)
        self.assertIn("X-GEMINI-SIGNATURE", configured_request.headers)
        self.assertEqual(self._api_key, configured_request.headers["X-GEMINI-APIKEY"])

        # Verify signature
        payload_b64 = configured_request.headers["X-GEMINI-PAYLOAD"]
        expected_signature = hmac.new(
            self._secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha384
        ).hexdigest()
        self.assertEqual(expected_signature, configured_request.headers["X-GEMINI-SIGNATURE"])

        # Verify payload contains a wall-clock, fractional-second nonce.
        decoded_payload = json.loads(base64.b64decode(payload_b64))
        self.assertIn("nonce", decoded_payload)
        self.assertEqual(f"{now:.6f}", decoded_payload["nonce"])

        # Verify body is cleared (Gemini uses headers, not body)
        self.assertIsNone(configured_request.data)

    def test_ws_authenticate(self):
        now = 1234567890.000
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = now

        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)

        request = MagicMock()
        request.headers = None
        configured_request = self.async_run_with_timeout(auth.ws_authenticate(request))

        self.assertIn("X-GEMINI-APIKEY", configured_request.headers)
        self.assertIn("X-GEMINI-NONCE", configured_request.headers)
        self.assertIn("X-GEMINI-PAYLOAD", configured_request.headers)
        self.assertIn("X-GEMINI-SIGNATURE", configured_request.headers)

        self.assertEqual(self._api_key, configured_request.headers["X-GEMINI-APIKEY"])

        # Verify WS signature
        nonce = configured_request.headers["X-GEMINI-NONCE"]
        payload_b64 = base64.b64encode(nonce.encode("utf-8")).decode("utf-8")
        expected_signature = hmac.new(
            self._secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha384
        ).hexdigest()
        self.assertEqual(expected_signature, configured_request.headers["X-GEMINI-SIGNATURE"])

    def test_get_ws_auth_headers(self):
        now = 1234567890.000
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = now

        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)
        headers = auth.get_ws_auth_headers()

        self.assertIn("X-GEMINI-APIKEY", headers)
        self.assertIn("X-GEMINI-NONCE", headers)
        self.assertIn("X-GEMINI-PAYLOAD", headers)
        self.assertIn("X-GEMINI-SIGNATURE", headers)
        self.assertEqual(self._api_key, headers["X-GEMINI-APIKEY"])

    def test_get_legacy_ws_auth_headers_signs_order_events_payload(self):
        now = 1234567890.000
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = now

        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)
        headers = auth.get_legacy_ws_auth_headers(CONSTANTS.ORDER_EVENTS_PATH_URL)

        self.assertIn("X-GEMINI-APIKEY", headers)
        self.assertIn("X-GEMINI-PAYLOAD", headers)
        self.assertIn("X-GEMINI-SIGNATURE", headers)
        self.assertNotIn("X-GEMINI-NONCE", headers)
        self.assertEqual(self._api_key, headers["X-GEMINI-APIKEY"])

        payload_b64 = headers["X-GEMINI-PAYLOAD"]
        decoded_payload = json.loads(base64.b64decode(payload_b64))
        self.assertEqual(CONSTANTS.ORDER_EVENTS_PATH_URL, decoded_payload["request"])
        self.assertEqual(f"{now:.6f}", decoded_payload["nonce"])

        expected_signature = hmac.new(
            self._secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha384
        ).hexdigest()
        self.assertEqual(expected_signature, headers["X-GEMINI-SIGNATURE"])

    def test_nonce_is_strictly_increasing_without_integer_second_drift(self):
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = 1234567890.000000
        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)

        nonces = [auth._get_nonce() for _ in range(3)]

        self.assertEqual(["1234567890.000000", "1234567890.000001", "1234567890.000002"], nonces)
        self.assertLess(float(nonces[-1]) - mock_time_provider.time.return_value, 1)

    def test_nonce_resets_when_counter_drifted_far_ahead(self):
        GeminiAuth._last_nonce = 1234568000.000000
        mock_time_provider = MagicMock()
        mock_time_provider.time.return_value = 1234567890.000000
        auth = GeminiAuth(api_key=self._api_key, secret_key=self._secret, time_provider=mock_time_provider)

        self.assertEqual("1234567890.000000", auth._get_nonce())
