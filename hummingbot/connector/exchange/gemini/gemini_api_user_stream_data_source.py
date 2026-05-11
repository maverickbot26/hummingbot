import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.gemini import gemini_constants as CONSTANTS
from hummingbot.connector.exchange.gemini.gemini_auth import GeminiAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.gemini.gemini_exchange import GeminiExchange


class GeminiAPIUserStreamDataSource(UserStreamTrackerDataSource):

    HEARTBEAT_TIME_INTERVAL = 30.0

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 auth: GeminiAuth,
                 trading_pairs: List[str],
                 connector: 'GeminiExchange',
                 api_factory: WebAssistantsFactory):
        super().__init__()
        self._auth: GeminiAuth = auth
        self._api_factory = api_factory
        self._connector = connector
        self._trading_pairs = trading_pairs

    async def _get_ws_assistant(self) -> WSAssistant:
        return await self._api_factory.get_ws_assistant()

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Creates a WebSocket connection to Gemini's authenticated v1 order-events stream.

        The Fast API requires specially provisioned account-scoped keys. The REST key used
        by Hummingbot here works with Gemini's legacy order-events stream, which is enough
        for order/fill tracking while order placement still goes through REST.
        """
        ws = await self._get_ws_assistant()
        auth_headers = self._auth.get_legacy_ws_auth_headers(CONSTANTS.ORDER_EVENTS_PATH_URL)
        query_params = []
        for trading_pair in self._trading_pairs:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            query_params.append(f"symbolFilter={symbol}")
        query_params.append("heartbeat=true")
        ws_url = f"{CONSTANTS.WSS_ORDER_EVENTS_URL}?{'&'.join(query_params)}"
        await ws.connect(
            ws_url=ws_url,
            ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL,
            ws_headers=auth_headers,
        )
        self.logger().info("Successfully connected to authenticated Gemini order-events stream")
        return ws

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        """The v1 order-events stream subscribes via URL parameters at connection time."""
        self.logger().info("Subscribed to Gemini order-events stream via connection URL...")

    async def _process_event_message(self, event_message: Any, queue: asyncio.Queue):
        messages = event_message if isinstance(event_message, list) else [event_message]
        for message in messages:
            if not isinstance(message, dict):
                continue
            converted_message = self._convert_legacy_order_event(message)
            if converted_message:
                queue.put_nowait(converted_message)

    @staticmethod
    def _convert_legacy_order_event(event_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_type = event_message.get("type")
        if event_type in ("subscription_ack", "heartbeat"):
            return None
        if "order_id" not in event_message:
            return None

        status = event_type
        if event_type in ("initial", "accepted", "booked"):
            status = "accepted"
        elif event_type == "fill":
            status = "FILLED" if str(event_message.get("remaining_amount", "0")) == "0" else "PARTIALLY_FILLED"
        elif event_type == "closed":
            status = "cancelled" if event_message.get("is_cancelled") else "closed"

        converted = {
            "E": event_message.get("timestampms", event_message.get("timestamp", 0)),
            "s": event_message.get("symbol", ""),
            "i": event_message.get("order_id", ""),
            "c": event_message.get("client_order_id", ""),
            "S": str(event_message.get("side", "")).upper(),
            "X": status,
            "p": event_message.get("price", "0"),
            "q": event_message.get("original_amount", "0"),
            "Z": event_message.get("executed_amount", "0"),
        }

        fill = event_message.get("fill") or {}
        if fill:
            converted["L"] = fill.get("price", event_message.get("avg_execution_price", "0"))
            converted["t"] = fill.get("trade_id", event_message.get("event_id", ""))

        return converted

    async def _on_user_stream_interruption(self, websocket_assistant: Optional[WSAssistant]):
        self.logger().info("User stream interrupted. Cleaning up...")
        websocket_assistant and await websocket_assistant.disconnect()
