"""Морские контейнеры: внутренние размеры, лимиты веса, оценка загрузки партии."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from functools import cache
from importlib import resources

from vedcalc.core.models import Shipment
from vedcalc.core.money import ZERO

MM3_IN_M3 = Decimal(1_000_000_000)


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    code: str  # совпадает с DeliveryMode: 20DC, 40HC…
    title: str
    length_mm: int
    width_mm: int
    height_mm: int
    max_cargo_kg: Decimal  # предельный вес груза

    @property
    def volume_m3(self) -> Decimal:
        """Внутренний объём, м³."""
        return Decimal(self.length_mm * self.width_mm * self.height_mm) / MM3_IN_M3

    @property
    def dims_label(self) -> str:
        return f"{self.length_mm} × {self.width_mm} × {self.height_mm} мм"


@cache
def load_containers() -> dict[str, ContainerSpec]:
    raw = json.loads(resources.files("vedcalc.data").joinpath("containers.json").read_text("utf-8"))
    return {
        c["code"]: ContainerSpec(
            c["code"],
            c["title"],
            c["length_mm"],
            c["width_mm"],
            c["height_mm"],
            Decimal(c["max_cargo_kg"]),
        )
        for c in raw["containers"]
    }


@dataclass(frozen=True, slots=True)
class LoadEstimate:
    """Загрузка контейнера партией — по сумме объёмов и весов позиций (без учёта укладки)."""

    spec: ContainerSpec
    volume_m3: Decimal
    weight_kg: Decimal
    missing_volume: tuple[str, ...]  # позиции без объёма
    missing_weight: tuple[str, ...]  # позиции без веса брутто

    @property
    def volume_share(self) -> Decimal:
        return self.volume_m3 / self.spec.volume_m3

    @property
    def weight_share(self) -> Decimal:
        return self.weight_kg / self.spec.max_cargo_kg

    @property
    def containers_needed(self) -> int:
        share = max(self.volume_share, self.weight_share)
        return max(1, int(share.to_integral_value(rounding=ROUND_CEILING)))

    @property
    def fits(self) -> bool:
        return self.volume_share <= 1 and self.weight_share <= 1

    @property
    def binding(self) -> str:
        """Что ограничивает раньше: «объём» или «вес»."""
        return "вес" if self.weight_share >= self.volume_share else "объём"


def estimate_load(shipment: Shipment) -> LoadEstimate | None:
    """Оценка для контейнерного способа доставки; None — не контейнер или нет данных."""
    spec = load_containers().get(shipment.delivery_mode.value)
    if spec is None:
        return None
    volume = sum((i.volume_m3 or ZERO for i in shipment.items), ZERO)
    weight = sum((i.gross_weight_kg or ZERO for i in shipment.items), ZERO)
    if volume <= 0 and weight <= 0:
        return None
    name = lambda n, i: i.name or f"позиция {n + 1}"  # noqa: E731
    return LoadEstimate(
        spec,
        volume,
        weight,
        tuple(name(n, i) for n, i in enumerate(shipment.items) if not i.volume_m3),
        tuple(name(n, i) for n, i in enumerate(shipment.items) if not i.gross_weight_kg),
    )
