from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from app.providers.base import CandleItem, DataSourceError, MarketDataProvider, MarketSnapshotItem
from app.utils.datetime import utc_now
from app.utils.retry import with_backoff
from app.utils.symbols import normalize_asset_symbol, to_usdt_pair

log = structlog.get_logger(__name__)


class BinanceMarketDataProvider(MarketDataProvider):
    """Public Binance Spot adapter. It does not accept or send API credentials."""

    def __init__(
        self,
        base_url: str = "https://api.binance.com",
        *,
        timeout: float = 15,
        max_retries: int = 3,
        max_concurrency: int = 5,
        exchange_info_cache_minutes: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._exchange_cache_ttl = timedelta(minutes=exchange_info_cache_minutes)
        self._exchange_symbols: set[str] = set()
        self._exchange_cache_at: datetime | None = None

    async def __aenter__(self) -> BinanceMarketDataProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async def operation() -> Any:
            async with self._semaphore:
                response = await self._client.get(path, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            if response.is_error:
                raise DataSourceError(
                    f"Binance returned HTTP {response.status_code} for {response.request.url.path}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise DataSourceError("Binance returned invalid JSON") from exc

        def retry_delay(exc: Exception) -> float | None:
            if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 429:
                return None
            value = exc.response.headers.get("Retry-After")
            if not value:
                return 1.0
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    return max(0.0, (parsedate_to_datetime(value) - utc_now()).total_seconds())
                except (TypeError, ValueError):
                    return 1.0

        try:
            return await with_backoff(
                operation, attempts=self.max_retries, retry_after=retry_delay
            )
        except (httpx.HTTPError, DataSourceError) as exc:
            raise DataSourceError(f"Binance request failed: {exc}") from exc

    async def get_exchange_symbols(self) -> set[str]:
        now = utc_now()
        if self._exchange_cache_at and now - self._exchange_cache_at < self._exchange_cache_ttl:
            return self._exchange_symbols
        payload = await self._request("/api/v3/exchangeInfo")
        self._exchange_symbols = {
            item["symbol"]
            for item in payload.get("symbols", [])
            if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
        }
        self._exchange_cache_at = now
        return self._exchange_symbols

    async def resolve_pair(self, symbol: str) -> str | None:
        pair = to_usdt_pair(symbol)
        return pair if pair in await self.get_exchange_symbols() else None

    async def fetch_market_snapshots(self, symbols: set[str]) -> list[MarketSnapshotItem]:
        available = await self.get_exchange_symbols()
        pairs = {to_usdt_pair(symbol): normalize_asset_symbol(symbol) for symbol in symbols}
        pairs = {pair: asset for pair, asset in pairs.items() if pair in available}
        if not pairs:
            return []

        prices_payload, tickers_payload = await asyncio.gather(
            self._request("/api/v3/ticker/price"),
            self._request("/api/v3/ticker/24hr"),
        )
        prices = {item["symbol"]: item for item in prices_payload if item.get("symbol") in pairs}
        tickers = {item["symbol"]: item for item in tickers_payload if item.get("symbol") in pairs}
        captured_at = utc_now()
        result: list[MarketSnapshotItem] = []
        for pair, asset in pairs.items():
            price_item = prices.get(pair)
            ticker = tickers.get(pair)
            if not price_item or not ticker:
                log.warning("binance_symbol_missing_from_batch", symbol=asset, pair=pair)
                continue
            result.append(
                MarketSnapshotItem(
                    symbol=asset,
                    price=Decimal(price_item["price"]),
                    quote_volume_24h=Decimal(ticker["quoteVolume"]),
                    base_volume_24h=Decimal(ticker["volume"]),
                    price_change_percent_24h=Decimal(ticker["priceChangePercent"]),
                    captured_at=captured_at,
                )
            )
        return result

    async def fetch_candles(
        self, symbol: str, *, interval: str = "5m", limit: int = 288
    ) -> list[CandleItem]:
        if not 1 <= limit <= 1000:
            raise ValueError("Binance kline limit must be between 1 and 1000")
        pair = await self.resolve_pair(symbol)
        if pair is None:
            return []
        payload = await self._request(
            "/api/v3/klines", {"symbol": pair, "interval": interval, "limit": limit}
        )
        asset = normalize_asset_symbol(symbol)
        return [
            CandleItem(
                symbol=asset,
                interval=interval,
                open_time=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
                close_time=datetime.fromtimestamp(row[6] / 1000, tz=UTC),
                quote_volume=Decimal(row[7]),
                trades_count=int(row[8]),
            )
            for row in payload
        ]

