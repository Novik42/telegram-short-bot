from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

DEMO_BASE_URL = "https://api-demo.bybit.com"


class BybitApiError(RuntimeError):
    def __init__(self, code: int, message: str, *, endpoint: str) -> None:
        super().__init__(f"Bybit {endpoint}: {code} {message}")
        self.code = code
        self.message = message
        self.endpoint = endpoint


@dataclass(frozen=True, slots=True)
class BybitInstrument:
    symbol: str
    status: str
    contract_type: str
    settle_coin: str
    tick_size: Decimal
    qty_step: Decimal
    min_qty: Decimal
    max_market_qty: Decimal
    min_notional: Decimal
    max_leverage: Decimal


@dataclass(frozen=True, slots=True)
class BybitTicker:
    symbol: str
    last_price: Decimal
    mark_price: Decimal
    bid_price: Decimal
    ask_price: Decimal


@dataclass(frozen=True, slots=True)
class BybitBalance:
    total_equity: Decimal
    wallet_balance: Decimal
    available_balance: Decimal


@dataclass(frozen=True, slots=True)
class BybitPosition:
    symbol: str
    side: str
    size: Decimal
    average_price: Decimal
    mark_price: Decimal
    stop_loss: Decimal
    leverage: Decimal
    position_idx: int


@dataclass(frozen=True, slots=True)
class BybitOrderAck:
    order_id: str
    order_link_id: str


@dataclass(frozen=True, slots=True)
class BybitOrderStatus:
    order_id: str
    order_link_id: str
    status: str
    cumulative_quantity: Decimal
    average_price: Decimal


class BybitDemoClient:
    """Minimal Bybit V5 client that cannot be pointed at a live trading host."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = DEMO_BASE_URL,
        timeout: float = 15.0,
        recv_window_ms: int = 5000,
    ) -> None:
        normalized_base = base_url.rstrip("/")
        if normalized_base != DEMO_BASE_URL:
            raise ValueError("BybitDemoClient only permits https://api-demo.bybit.com")
        if not api_key or not api_secret:
            raise ValueError("Bybit Demo API key and secret are required")
        self.api_key = api_key
        self._api_secret = api_secret
        self.base_url = normalized_base
        self.recv_window = str(recv_window_ms)
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
        public: bool = False,
        accepted_codes: frozenset[int] = frozenset(),
    ) -> dict[str, Any]:
        query = urlencode(list((params or {}).items()))
        body = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True)
        url = f"{self.base_url}{endpoint}"
        if query:
            url = f"{url}?{query}"
        headers: dict[str, str] = {}
        content: str | None = None
        if not public:
            timestamp = str(int(time.time() * 1000))
            signing_value = query if method == "GET" else body
            signature = hmac.new(
                self._api_secret.encode(),
                f"{timestamp}{self.api_key}{self.recv_window}{signing_value}".encode(),
                hashlib.sha256,
            ).hexdigest()
            headers = {
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": self.recv_window,
                "X-BAPI-SIGN": signature,
                "Content-Type": "application/json",
            }
        if method == "POST":
            content = body
        response = await self._client.request(method, url, headers=headers, content=content)
        response.raise_for_status()
        data = response.json()
        code = int(data.get("retCode", -1))
        if code != 0 and code not in accepted_codes:
            raise BybitApiError(code, str(data.get("retMsg") or "Unknown error"), endpoint=endpoint)
        return data

    async def get_api_key_info(self) -> dict[str, Any]:
        return (await self._request("GET", "/v5/user/query-api")).get("result") or {}

    async def get_account_info(self) -> dict[str, Any]:
        return (await self._request("GET", "/v5/account/info")).get("result") or {}

    async def get_balance(self) -> BybitBalance:
        data = await self._request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED", "coin": "USDT"},
        )
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            raise BybitApiError(-1, "UNIFIED balance is missing", endpoint="wallet-balance")
        row = rows[0]
        wallet = Decimal(str(row.get("totalWalletBalance") or "0"))
        equity = Decimal(str(row.get("totalEquity") or wallet))
        available = Decimal(str(row.get("totalAvailableBalance") or wallet))
        return BybitBalance(equity, wallet, available)

    async def get_instrument(self, symbol: str) -> BybitInstrument:
        data = await self._request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            public=True,
        )
        rows = (data.get("result") or {}).get("list") or []
        if len(rows) != 1:
            raise BybitApiError(-1, f"Linear instrument {symbol} not found", endpoint="instrument")
        row = rows[0]
        price_filter = row.get("priceFilter") or {}
        lot_filter = row.get("lotSizeFilter") or {}
        leverage_filter = row.get("leverageFilter") or {}
        return BybitInstrument(
            symbol=str(row.get("symbol") or ""),
            status=str(row.get("status") or ""),
            contract_type=str(row.get("contractType") or ""),
            settle_coin=str(row.get("settleCoin") or ""),
            tick_size=Decimal(str(price_filter.get("tickSize") or "0")),
            qty_step=Decimal(str(lot_filter.get("qtyStep") or "0")),
            min_qty=Decimal(str(lot_filter.get("minOrderQty") or "0")),
            max_market_qty=Decimal(str(lot_filter.get("maxMktOrderQty") or "0")),
            min_notional=Decimal(str(lot_filter.get("minNotionalValue") or "0")),
            max_leverage=Decimal(str(leverage_filter.get("maxLeverage") or "0")),
        )

    async def get_ticker(self, symbol: str) -> BybitTicker:
        data = await self._request(
            "GET",
            "/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            public=True,
        )
        rows = (data.get("result") or {}).get("list") or []
        if len(rows) != 1:
            raise BybitApiError(-1, f"Ticker {symbol} not found", endpoint="tickers")
        row = rows[0]
        return BybitTicker(
            symbol=str(row.get("symbol") or ""),
            last_price=Decimal(str(row.get("lastPrice") or "0")),
            mark_price=Decimal(str(row.get("markPrice") or "0")),
            bid_price=Decimal(str(row.get("bid1Price") or "0")),
            ask_price=Decimal(str(row.get("ask1Price") or "0")),
        )

    @staticmethod
    def _position(row: dict[str, Any]) -> BybitPosition:
        return BybitPosition(
            symbol=str(row.get("symbol") or ""),
            side=str(row.get("side") or ""),
            size=Decimal(str(row.get("size") or "0")),
            average_price=Decimal(str(row.get("avgPrice") or "0")),
            mark_price=Decimal(str(row.get("markPrice") or "0")),
            stop_loss=Decimal(str(row.get("stopLoss") or "0")),
            leverage=Decimal(str(row.get("leverage") or "0")),
            position_idx=int(row.get("positionIdx") or 0),
        )

    async def get_open_positions(self) -> list[BybitPosition]:
        data = await self._request(
            "GET",
            "/v5/position/list",
            params={"category": "linear", "settleCoin": "USDT", "limit": 200},
        )
        rows = (data.get("result") or {}).get("list") or []
        return [self._position(row) for row in rows if Decimal(str(row.get("size") or "0")) > 0]

    async def get_position(self, symbol: str) -> BybitPosition | None:
        data = await self._request(
            "GET",
            "/v5/position/list",
            params={"category": "linear", "symbol": symbol},
        )
        rows = (data.get("result") or {}).get("list") or []
        positions = [self._position(row) for row in rows]
        return next((position for position in positions if position.size > 0), None)

    async def set_isolated_margin(self) -> None:
        data = await self._request(
            "POST",
            "/v5/account/set-margin-mode",
            payload={"setMarginMode": "ISOLATED_MARGIN"},
            accepted_codes=frozenset({110026}),
        )
        reasons = (data.get("result") or {}).get("reasons") or []
        if reasons:
            message = "; ".join(str(row.get("reasonMsg") or row) for row in reasons)
            raise BybitApiError(-1, message, endpoint="set-margin-mode")

    async def switch_one_way(self, symbol: str) -> None:
        await self._request(
            "POST",
            "/v5/position/switch-mode",
            payload={"category": "linear", "symbol": symbol, "mode": 0},
            accepted_codes=frozenset({110025}),
        )

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        value = str(leverage)
        await self._request(
            "POST",
            "/v5/position/set-leverage",
            payload={
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": value,
                "sellLeverage": value,
            },
            accepted_codes=frozenset({110043}),
        )

    async def place_market_short(
        self,
        symbol: str,
        quantity: Decimal,
        stop_loss: Decimal,
        order_link_id: str,
    ) -> BybitOrderAck:
        data = await self._request(
            "POST",
            "/v5/order/create",
            payload={
                "category": "linear",
                "symbol": symbol,
                "side": "Sell",
                "orderType": "Market",
                "qty": format(quantity, "f"),
                "positionIdx": 0,
                "reduceOnly": False,
                "stopLoss": format(stop_loss, "f"),
                "slTriggerBy": "MarkPrice",
                "tpslMode": "Full",
                "slOrderType": "Market",
                "orderLinkId": order_link_id,
            },
        )
        result = data.get("result") or {}
        return BybitOrderAck(
            order_id=str(result.get("orderId") or ""),
            order_link_id=str(result.get("orderLinkId") or order_link_id),
        )

    async def get_order_status(
        self, *, order_id: str | None = None, order_link_id: str | None = None
    ) -> BybitOrderStatus | None:
        if not order_id and not order_link_id:
            raise ValueError("order_id or order_link_id is required")
        params = {"category": "linear"}
        if order_id:
            params["orderId"] = order_id
        else:
            params["orderLinkId"] = str(order_link_id)
        data = await self._request("GET", "/v5/order/realtime", params=params)
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            return None
        row = rows[0]
        return BybitOrderStatus(
            order_id=str(row.get("orderId") or ""),
            order_link_id=str(row.get("orderLinkId") or ""),
            status=str(row.get("orderStatus") or "UNKNOWN"),
            cumulative_quantity=Decimal(str(row.get("cumExecQty") or "0")),
            average_price=Decimal(str(row.get("avgPrice") or "0")),
        )

    async def cancel_order(self, symbol: str, order_id: str) -> BybitOrderAck:
        data = await self._request(
            "POST",
            "/v5/order/cancel",
            payload={"category": "linear", "symbol": symbol, "orderId": order_id},
        )
        result = data.get("result") or {}
        return BybitOrderAck(
            order_id=str(result.get("orderId") or order_id),
            order_link_id=str(result.get("orderLinkId") or ""),
        )

    async def set_stop_loss(self, symbol: str, stop_loss: Decimal) -> None:
        await self._request(
            "POST",
            "/v5/position/trading-stop",
            payload={
                "category": "linear",
                "symbol": symbol,
                "tpslMode": "Full",
                "positionIdx": 0,
                "stopLoss": format(stop_loss, "f"),
                "slTriggerBy": "MarkPrice",
            },
        )

    async def emergency_close_short(
        self, symbol: str, quantity: Decimal, order_link_id: str
    ) -> BybitOrderAck:
        data = await self._request(
            "POST",
            "/v5/order/create",
            payload={
                "category": "linear",
                "symbol": symbol,
                "side": "Buy",
                "orderType": "Market",
                "qty": format(quantity, "f"),
                "positionIdx": 0,
                "reduceOnly": True,
                "closeOnTrigger": True,
                "orderLinkId": order_link_id,
            },
        )
        result = data.get("result") or {}
        return BybitOrderAck(
            order_id=str(result.get("orderId") or ""),
            order_link_id=str(result.get("orderLinkId") or order_link_id),
        )

    async def wait_for_position(
        self, symbol: str, *, timeout_seconds: float = 10.0
    ) -> BybitPosition | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            position = await self.get_position(symbol)
            if position is not None:
                return position
            await asyncio.sleep(0.5)
        return None

    async def get_latest_closed_pnl(self, symbol: str) -> dict[str, Any] | None:
        data = await self._request(
            "GET",
            "/v5/position/closed-pnl",
            params={"category": "linear", "symbol": symbol, "limit": 10},
        )
        rows = (data.get("result") or {}).get("list") or []
        return rows[0] if rows else None
