from decimal import Decimal

import httpx
import pytest

from app.providers.binance_spot import BinanceMarketDataProvider


@pytest.mark.asyncio
async def test_market_snapshots_use_batch_endpoints_and_skip_unknown_symbol() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "KAITOUSDT",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json=[{"symbol": "KAITOUSDT", "price": "1.25"}])
        if request.url.path == "/api/v3/ticker/24hr":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "KAITOUSDT",
                        "quoteVolume": "5000000",
                        "volume": "4000000",
                        "priceChangePercent": "12.4",
                    }
                ],
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    client = httpx.AsyncClient(
        base_url="https://api.binance.test", transport=httpx.MockTransport(handler)
    )
    provider = BinanceMarketDataProvider(client=client)
    try:
        items = await provider.fetch_market_snapshots({"KAITO", "NOTREAL"})
    finally:
        await client.aclose()

    assert len(items) == 1
    assert items[0].symbol == "KAITO"
    assert items[0].price == Decimal("1.25")
    assert calls.count("/api/v3/exchangeInfo") == 1
    assert calls.count("/api/v3/ticker/price") == 1
    assert calls.count("/api/v3/ticker/24hr") == 1


@pytest.mark.asyncio
async def test_exchange_info_is_cached() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"symbols": []})

    client = httpx.AsyncClient(
        base_url="https://api.binance.test", transport=httpx.MockTransport(handler)
    )
    provider = BinanceMarketDataProvider(client=client)
    try:
        await provider.get_exchange_symbols()
        await provider.get_exchange_symbols()
    finally:
        await client.aclose()
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_candles_maps_public_kline_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {"symbol": "KAITOUSDT", "status": "TRADING", "quoteAsset": "USDT"}
                    ]
                },
            )
        assert request.url.path == "/api/v3/klines"
        return httpx.Response(
            200,
            json=[
                [
                    1786257600000,
                    "1.0",
                    "1.3",
                    "0.9",
                    "1.2",
                    "1000",
                    1786257899999,
                    "1150",
                    42,
                    "0",
                    "0",
                    "0",
                ]
            ],
        )

    client = httpx.AsyncClient(
        base_url="https://api.binance.test", transport=httpx.MockTransport(handler)
    )
    provider = BinanceMarketDataProvider(client=client)
    try:
        candles = await provider.fetch_candles("kaito", limit=1)
    finally:
        await client.aclose()
    assert candles[0].close == Decimal("1.2")
    assert candles[0].trades_count == 42
    assert candles[0].open_time.tzinfo is not None


@pytest.mark.asyncio
async def test_retries_http_429() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"symbols": []}, request=request)

    client = httpx.AsyncClient(
        base_url="https://api.binance.test", transport=httpx.MockTransport(handler)
    )
    provider = BinanceMarketDataProvider(client=client, max_retries=2)
    try:
        assert await provider.get_exchange_symbols() == set()
    finally:
        await client.aclose()
    assert call_count == 2

