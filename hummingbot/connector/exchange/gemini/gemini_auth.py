import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest


class GeminiAuth(AuthBase):
    def __init__(self, api_key: str, secret_key: str, time_provider: Optional[TimeSynchronizer] = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.time_provider = time_provider

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """
        Gemini REST API authentication uses payload-based signing.
        The payload is a base64-encoded JSON object containing the request path, nonce, and parameters.
        """
        nonce = self._get_nonce()

        # Build the payload from existing request data
        payload_dict: Dict[str, Any] = {}
        if request.data:
            if isinstance(request.data, str):
                payload_dict = json.loads(request.data)
            elif isinstance(request.data, dict):
                payload_dict = dict(request.data)

        payload_dict["nonce"] = nonce

        # The "request" field must be set by the caller in request.data
        # e.g., {"request": "/v1/order/new", "symbol": "btcusd", ...}

        payload_json = json.dumps(payload_dict)
        payload_b64 = base64.b64encode(payload_json.encode("utf-8"))

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_b64,
            hashlib.sha384
        ).hexdigest()

        headers = {}
        if request.headers is not None:
            headers.update(request.headers)
        headers["Content-Type"] = "text/plain"
        headers["Content-Length"] = "0"
        headers["X-GEMINI-APIKEY"] = self.api_key
        headers["X-GEMINI-PAYLOAD"] = payload_b64.decode("utf-8")
        headers["X-GEMINI-SIGNATURE"] = signature
        headers["Cache-Control"] = "no-cache"

        request.headers = headers
        # Gemini expects an empty body; the payload is in the header
        request.data = None

        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        """
        Fast API WebSocket authentication via handshake headers.
        """
        nonce = str(self._get_nonce())
        payload_b64 = base64.b64encode(nonce.encode("utf-8")).decode("utf-8")

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha384
        ).hexdigest()

        headers = request.headers or {}
        headers["X-GEMINI-APIKEY"] = self.api_key
        headers["X-GEMINI-NONCE"] = nonce
        headers["X-GEMINI-PAYLOAD"] = payload_b64
        headers["X-GEMINI-SIGNATURE"] = signature
        request.headers = headers

        return request

    def get_ws_auth_headers(self) -> Dict[str, str]:
        """
        Generate authentication headers for WebSocket connection.
        Used when connecting via raw websocket libraries that need headers at connect time.
        """
        nonce = str(self._get_nonce())
        payload_b64 = base64.b64encode(nonce.encode("utf-8")).decode("utf-8")

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha384
        ).hexdigest()

        return {
            "X-GEMINI-APIKEY": self.api_key,
            "X-GEMINI-NONCE": nonce,
            "X-GEMINI-PAYLOAD": payload_b64,
            "X-GEMINI-SIGNATURE": signature,
        }

    def get_legacy_ws_auth_headers(self, request_path: str) -> Dict[str, str]:
        """Generate Gemini v1 order-events WebSocket auth headers.

        The legacy order-events stream signs the same JSON payload format as REST:
        {"request": "/v1/order/events", "nonce": <nonce>}. Unlike the Fast API,
        it does not use X-GEMINI-NONCE and does not sign a bare base64 nonce.
        """
        payload_dict = {"request": request_path, "nonce": self._get_nonce()}
        payload_json = json.dumps(payload_dict)
        payload_b64 = base64.b64encode(payload_json.encode("utf-8"))

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_b64,
            hashlib.sha384
        ).hexdigest()

        return {
            "X-GEMINI-APIKEY": self.api_key,
            "X-GEMINI-PAYLOAD": payload_b64.decode("utf-8"),
            "X-GEMINI-SIGNATURE": signature,
        }

    _last_nonce: float = 0

    def _get_nonce(self) -> str:
        """Return a strictly increasing nonce that stays close to wall-clock seconds.

        Gemini rejects nonces that are more than ~30 seconds away from server time.
        Using integer seconds and incrementing by 1 for rapid requests can drift outside
        that window after a small burst. Gemini accepts fractional-second nonces, so use
        microsecond precision and only bump by one microsecond on same-tick collisions.
        """
        if self.time_provider is not None:
            nonce = float(self.time_provider.time())
        else:
            nonce = time.time()
        # Reset counter if it drifted too far ahead of current time.
        if GeminiAuth._last_nonce > nonce + 15:
            GeminiAuth._last_nonce = 0
        # Ensure strictly increasing nonce for rapid sequential requests
        if nonce <= GeminiAuth._last_nonce:
            nonce = GeminiAuth._last_nonce + 0.000001
        GeminiAuth._last_nonce = nonce
        return f"{nonce:.6f}"
