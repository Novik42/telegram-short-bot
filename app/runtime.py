from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.database import create_engine_and_session
from app.providers.base import BorrowDataProvider
from app.providers.binance_spot import BinanceMarketDataProvider
from app.providers.borrow_bbm import BbmBorrowProvider
from app.providers.borrow_fixture import FixtureBorrowProvider
from app.providers.bybit_demo import BybitDemoClient
from app.services.anomaly_detector import AnomalyDetector
from app.services.collector import Collector
from app.services.demo_trading import DemoTradingService
from app.services.outcome_evaluator import OutcomeEvaluator
from app.services.reversal_tracker import ReversalTracker
from app.services.watch_market_updater import WatchMarketUpdater


@dataclass(slots=True)
class Runtime:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    borrow_provider: BorrowDataProvider
    market_provider: BinanceMarketDataProvider
    collector: Collector
    outcome_evaluator: OutcomeEvaluator
    reversal_tracker: ReversalTracker
    watch_market_updater: WatchMarketUpdater
    bybit_client: BybitDemoClient | None = None
    demo_trading: DemoTradingService | None = None

    async def close(self) -> None:
        await self.borrow_provider.aclose()
        await self.market_provider.aclose()
        if self.bybit_client is not None:
            await self.bybit_client.aclose()
        await self.engine.dispose()


def build_runtime(settings: Settings) -> Runtime:
    engine, session_factory = create_engine_and_session(settings.database_url)
    if settings.borrow_provider == "fixture":
        borrow_provider: BorrowDataProvider = FixtureBorrowProvider(
            settings.fixture_borrow_file, replay_speed=settings.fixture_replay_speed
        )
    elif settings.borrow_provider == "html":
        borrow_provider = BbmBorrowProvider(
            settings.borrow_html_url or "https://bbm.iflint.pro/",
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            max_age_minutes=settings.borrow_data_max_age_minutes,
        )
    else:
        raise ValueError(f"Borrow provider {settings.borrow_provider!r} is not implemented")
    market_provider = BinanceMarketDataProvider(
        settings.binance_spot_base_url,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        max_concurrency=settings.binance_max_concurrency,
        exchange_info_cache_minutes=settings.binance_exchange_info_cache_minutes,
    )
    anomaly_detector = AnomalyDetector(session_factory, settings)
    bybit_client: BybitDemoClient | None = None
    demo_trading: DemoTradingService | None = None
    if settings.demo_trading_enabled:
        assert settings.bybit_api_key is not None
        assert settings.bybit_api_secret is not None
        bybit_client = BybitDemoClient(
            settings.bybit_api_key.get_secret_value(),
            settings.bybit_api_secret.get_secret_value(),
            base_url=settings.bybit_base_url,
            timeout=settings.http_timeout_seconds,
        )
        demo_trading = DemoTradingService(bybit_client, session_factory, settings)
    return Runtime(
        engine=engine,
        session_factory=session_factory,
        borrow_provider=borrow_provider,
        market_provider=market_provider,
        collector=Collector(
            borrow_provider,
            market_provider,
            session_factory,
            anomaly_detector=anomaly_detector,
        ),
        outcome_evaluator=OutcomeEvaluator(session_factory, market_provider),
        reversal_tracker=ReversalTracker(session_factory, settings),
        watch_market_updater=WatchMarketUpdater(market_provider, session_factory, settings),
        bybit_client=bybit_client,
        demo_trading=demo_trading,
    )
