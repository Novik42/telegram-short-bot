from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.borrow_metrics import BorrowMetrics
from app.services.price_analyzer import PriceContext
from app.services.volume_analyzer import VolumeContext


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: Decimal
    components: dict[str, Decimal]


def _scaled(value: Decimal, threshold: Decimal) -> Decimal:
    if threshold <= 0 or value <= 0:
        return Decimal("0")
    return min(Decimal("100"), value / threshold * Decimal("50"))


def calculate_anomaly_score(
    metrics: BorrowMetrics,
    price: PriceContext,
    volume: VolumeContext,
    *,
    min_borrow_delta: Decimal,
    min_borrow_delta_pct: Decimal,
    min_net_borrow_delta: Decimal,
    min_price_pump_1h_pct: Decimal,
    min_volume_spike_ratio: Decimal,
) -> ScoreResult:
    price_pump = max(
        price.price_change_1h or Decimal("0"),
        price.pump_from_low_1h or Decimal("0"),
    )
    components = {
        "absolute": _scaled(metrics.borrow_delta, min_borrow_delta),
        "relative": _scaled(metrics.borrow_delta_pct, min_borrow_delta_pct),
        "velocity": _scaled(
            metrics.borrow_velocity,
            min_borrow_delta / Decimal(metrics.window_minutes),
        ),
        "net": _scaled(metrics.net_borrow_delta, min_net_borrow_delta),
        "price_pump": _scaled(price_pump, min_price_pump_1h_pct),
        "volume_spike": _scaled(
            volume.volume_spike_ratio or Decimal("0"), min_volume_spike_ratio
        ),
        "impact": _scaled(
            volume.borrow_to_volume_ratio or Decimal("0"), Decimal("0.10")
        ),
        "rarity": Decimal("0"),
    }
    total = (
        Decimal("0.20") * components["absolute"]
        + Decimal("0.15") * components["relative"]
        + Decimal("0.15") * components["velocity"]
        + Decimal("0.15") * components["net"]
        + Decimal("0.15") * components["price_pump"]
        + Decimal("0.05") * components["volume_spike"]
        + Decimal("0.10") * components["impact"]
        + Decimal("0.05") * components["rarity"]
    )
    return ScoreResult(total=min(Decimal("100"), total), components=components)

