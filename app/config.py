from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://margin_monitor:margin_monitor@localhost:5432/margin_monitor"
    log_level: str = "INFO"

    borrow_provider: Literal["fixture", "http_json", "html", "manual"] = "fixture"
    fixture_borrow_file: Path = Path("fixtures/borrow_snapshots.json")
    fixture_replay_speed: float = Field(default=60.0, gt=0)
    borrow_json_url: str | None = None
    borrow_html_url: str | None = None
    borrow_json_headers: dict[str, str] = Field(default_factory=dict)
    borrow_symbol_field: str = "symbol"
    borrow_amount_field: str = "borrow_usd"
    repay_amount_field: str = "repay_usd"
    borrow_ratio_field: str = "ratio"
    borrow_timestamp_field: str = "timestamp"
    borrow_row_selector: str | None = None
    borrow_symbol_selector: str | None = None
    borrow_amount_selector: str | None = None
    repay_amount_selector: str | None = None
    borrow_ratio_selector: str | None = None

    collection_interval_seconds: int | None = Field(default=None, ge=10)
    collection_interval_minutes: int = Field(default=5, gt=0)
    borrow_data_max_age_minutes: int = Field(default=15, gt=0)
    http_timeout_seconds: float = Field(default=15, gt=0)
    http_max_retries: int = Field(default=3, ge=1, le=10)
    binance_max_concurrency: int = Field(default=5, ge=1, le=50)
    binance_exchange_info_cache_minutes: int = Field(default=60, gt=0)
    binance_spot_base_url: str = "https://api.binance.com"
    binance_futures_base_url: str = "https://fapi.binance.com"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    min_borrow_delta_usd: Decimal = Decimal("100000")
    min_borrow_delta_pct: Decimal = Decimal("30")
    min_net_borrow_delta_usd: Decimal = Decimal("75000")
    min_price_pump_1h_pct: Decimal = Decimal("5")
    min_price_pump_4h_pct: Decimal = Decimal("10")
    min_volume_spike_ratio: Decimal = Decimal("1.5")
    min_anomaly_score: Decimal = Decimal("60")
    safe_repay_epsilon_usd: Decimal = Decimal("100")
    require_confirmation_snapshots: int = Field(default=2, ge=1)
    alert_cooldown_minutes: int = Field(default=60, ge=0)
    alert_renotify_score_increase: Decimal = Decimal("15")
    alert_renotify_borrow_increase_pct: Decimal = Decimal("50")

    @property
    def collection_seconds(self) -> int:
        return self.collection_interval_seconds or self.collection_interval_minutes * 60

    @field_validator("borrow_json_url", "borrow_html_url", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_async_postgres_url(cls, value: object) -> object:
        """Convert provider-style PostgreSQL URLs to SQLAlchemy's async driver URL."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
