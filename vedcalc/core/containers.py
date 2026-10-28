"""Морские контейнеры: внутренние размеры, лимиты веса, оценка загрузки партии."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from functools import cache, lru_cache
from importlib import resources
from typing import Any

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
    """Загрузка контейнера партией.

    Если у всех позиций задана упаковка — по укладке грузовых мест (алгоритм модуля
    «Контейнер»), иначе — прикидка по сумме объёмов и весов позиций.
    """

    spec: ContainerSpec
    volume_m3: Decimal
    weight_kg: Decimal
    missing_volume: tuple[str, ...]  # позиции без объёма
    missing_weight: tuple[str, ...]  # позиции без веса брутто
    packing: Any = None  # PackingResult — укладка мест, если упаковка задана у всех позиций
    missing_packaging: tuple[str, ...] = ()  # позиции без упаковки

    @property
    def volume_share(self) -> Decimal:
        return self.volume_m3 / self.spec.volume_m3

    @property
    def weight_share(self) -> Decimal:
        return self.weight_kg / self.spec.max_cargo_kg

    @property
    def packed(self) -> bool:
        return self.packing is not None and self.packing.complete

    @property
    def containers_needed(self) -> int:
        if self.packed:
            return max(1, self.packing.containers_needed)
        share = max(self.volume_share, self.weight_share)
        return max(1, int(share.to_integral_value(rounding=ROUND_CEILING)))

    @property
    def fits(self) -> bool:
        if self.packed:
            return self.packing.containers_needed <= 1
        return self.volume_share <= 1 and self.weight_share <= 1

    @property
    def binding(self) -> str:
        """Что ограничивает раньше: «объём» или «вес»."""
        if self.packed:
            return self.packing.binding
        return "вес" if self.weight_share >= self.volume_share else "объём"


def cargo_kinds(shipment: Shipment) -> list | None:
    """Грузовые места партии для укладки; None — не у всех позиций задана упаковка."""
    from vedcalc.modules.container_calc.packing import CargoKind

    kinds = []
    for n, item in enumerate(shipment.items):
        pk = item.packaging
        if pk is None:
            return None
        count = pk.places(item.quantity)
        if count <= 0:
            return None
        mm = lambda cm: int((cm * 10).to_integral_value())  # noqa: E731
        kinds.append(CargoKind(item.name or f"Позиция {n + 1}", mm(pk.length_cm), mm(pk.width_cm),
                               mm(pk.height_cm), pk.gross_kg, count, can_tip=pk.can_tip,
                               stackable=pk.stackable, max_layers=pk.max_layers))  # fmt: skip
    return kinds


@lru_cache(maxsize=32)
def _pack(kinds: tuple, code: str):
    from vedcalc.modules.container_calc.packing import PackingInputError, pack

    try:
        return pack(list(kinds), load_containers()[code])
    except PackingInputError:
        return None


def estimate_load(shipment: Shipment) -> LoadEstimate | None:
    """Оценка для контейнерного способа доставки; None — не контейнер или нет данных."""
    spec = load_containers().get(shipment.delivery_mode.value)
    if spec is None:
        return None
    volumes, weights = [], []
    for item in shipment.items:
        pk = item.packaging
        places = pk.places(item.quantity) if pk else 0
        volumes.append(item.volume_m3 or (pk.place_volume_m3 * places if pk else None))
        weights.append(item.gross_weight_kg or (pk.gross_kg * places if pk else None))
    volume = sum((v or ZERO for v in volumes), ZERO)
    weight = sum((w or ZERO for w in weights), ZERO)
    kinds = cargo_kinds(shipment)
    packing = _pack(tuple(kinds), spec.code) if kinds else None
    if volume <= 0 and weight <= 0 and packing is None:
        return None
    if packing is not None and packing.complete:
        # По укладке — объём и вес самих мест (с тарой), как во вкладке «Контейнер»
        volume, weight = packing.total_volume_m3, packing.total_weight_kg
    items = list(enumerate(shipment.items))
    name = lambda n, i: i.name or f"позиция {n + 1}"  # noqa: E731
    return LoadEstimate(
        spec,
        volume,
        weight,
        tuple(name(n, i) for n, i in items if not volumes[n]),
        tuple(name(n, i) for n, i in items if not weights[n]),
        packing,
        tuple(name(n, i) for n, i in items if i.packaging is None),
    )
