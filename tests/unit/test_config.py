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
