from app.models import Base


def test_stage_one_database_schema_contains_all_research_tables() -> None:
    assert set(Base.metadata.tables) == {
        "borrow_snapshots",
        "market_snapshots",
        "candles",
        "anomaly_events",
        "event_outcomes",
        "notification_log",
        "pump_watches",
        "pump_watch_transitions",
    }


def test_borrow_snapshot_deduplication_constraint() -> None:
    table = Base.metadata.tables["borrow_snapshots"]
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("source_name", "symbol", "source_timestamp") in unique_column_sets
