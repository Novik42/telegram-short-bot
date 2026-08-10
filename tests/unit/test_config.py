from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_render_postgres_url_uses_asyncpg_driver() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:password@db.internal:5432/margin_monitor",
    )

    assert (
        settings.database_url
        == "postgresql+asyncpg://user:password@db.internal:5432/margin_monitor"
    )


def test_existing_sqlalchemy_driver_url_is_unchanged() -> None:
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///./monitor.db")

    assert settings.database_url == "sqlite+aiosqlite:///./monitor.db"


def test_high_cap_exclusion_is_configurable_csv() -> None:
    settings = Settings(
        _env_file=None,
        high_cap_excluded_symbols="sol, TRX, sui",
    )

    assert settings.high_cap_excluded_symbol_set == frozenset({"SOL", "TRX", "SUI"})


def test_demo_mode_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="BYBIT_API_KEY"):
        Settings(_env_file=None, trading_mode="demo")


def test_demo_mode_rejects_live_bybit_host() -> None:
    with pytest.raises(ValidationError, match="api-demo.bybit.com"):
        Settings(
            _env_file=None,
            trading_mode="demo",
            bybit_api_key="demo-key",
            bybit_api_secret="demo-secret",
            bybit_base_url="https://api.bybit.com",
        )


def test_demo_mode_accepts_only_demo_credentials_and_defaults() -> None:
    settings = Settings(
        _env_file=None,
        trading_mode="demo",
        bybit_api_key="demo-key",
        bybit_api_secret="demo-secret",
    )

    assert settings.demo_trading_enabled is True
    assert settings.demo_leverage == 5
    assert settings.demo_risk_percent == 1
    assert settings.demo_entry_slippage_buffer_pct == Decimal("0.25")
