"""Укладка грузовых мест в контейнер (без GUI).

Метод — «стенками» вдоль длины контейнера. Каждая стенка (поперечный срез глубиной в одно
место ведущего вида) заполняется как двумерная раскладка «ширина × высота»: сначала
крупные места, затем свободное пространство сверху и сбоку отдаётся другим видам, которые
туда помещаются (в т.ч. несколько штук в глубину стенки). На места без штабелирования
ничего не ставится. Общий вес груза ограничен предельным весом контейнера.

Для одинаковых коробок и паллет результат близок к оптимальному; для смешанного груза —
хорошее приближение (раскладка не перебирает все варианты).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from decimal import Decimal

from vedcalc.core.containers import ContainerSpec, load_containers
from vedcalc.core.money import ZERO

MAX_KINDS = 20
MAX_CONTAINERS = 100
MM3_IN_M3 = Decimal(1_000_000_000)


@dataclass(frozen=True, slots=True)
class CargoKind:
    """Вид грузовых мест: размеры одного места, вес брутто одного места, количество."""

    name: str
    length_mm: int
    width_mm: int
    height_mm: int
    weight_kg: Decimal
    quantity: int
    can_tip: bool = False  # можно класть на бок (любая ориентация)
    stackable: bool = True  # можно ставить друг на друга
    max_layers: int | None = None  # предел ярусов (None — по высоте контейнера)

    @property
    def piece_volume_mm3(self) -> int:
        return self.length_mm * self.width_mm * self.height_mm

    @property
    def piece_volume_m3(self) -> Decimal:
        return Decimal(self.length_mm * self.width_mm * self.height_mm) / MM3_IN_M3

    def orientations(self) -> list[tuple[int, int, int]]:
        """(вдоль длины, поперёк, высота). Без кантования — только поворот вокруг вертикали."""
        dims = (self.length_mm, self.width_mm, self.height_mm)
        if self.can_tip:
            options = itertools.permutations(dims)
        else:
            options = [dims, (self.width_mm, self.length_mm, self.height_mm)]
        return sorted(set(options))


@dataclass(frozen=True, slots=True)
class Block:
    """Прямоугольная группа мест одного вида в одной ориентации."""

    kind_index: int
    name: str
    count: int
    x_mm: int  # от торцевой стены вдоль длины
    y_mm: int  # от левой стены поперёк
    z_mm: int  # от пола
    along_mm: int  # размер одного места вдоль длины контейнера
    across_mm: int
    up_mm: int
    deep: int  # мест в глубину стенки
    per_row: int  # мест поперёк
    layers: int  # ярусов

    @property
    def depth_mm(self) -> int:
        return self.deep * self.along_mm

    @property
    def width_mm(self) -> int:
        return self.per_row * self.across_mm

    @property
    def height_mm(self) -> int:
        return self.layers * self.up_mm


@dataclass(frozen=True, slots=True)
class LoadedContainer:
    spec: ContainerSpec
    blocks: tuple[Block, ...]
    kinds: tuple[CargoKind, ...]

    @property
    def count(self) -> int:
        return sum(b.count for b in self.blocks)

    @property
    def volume_m3(self) -> Decimal:
        return sum((self.kinds[b.kind_index].piece_volume_m3 * b.count for b in self.blocks), ZERO)

    @property
    def weight_kg(self) -> Decimal:
        return sum((self.kinds[b.kind_index].weight_kg * b.count for b in self.blocks), ZERO)

    @property
    def used_length_mm(self) -> int:
        return max((b.x_mm + b.depth_mm for b in self.blocks), default=0)

    @property
    def volume_share(self) -> Decimal:
        return self.volume_m3 / self.spec.volume_m3

    @property
    def weight_share(self) -> Decimal:
        return self.weight_kg / self.spec.max_cargo_kg

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for b in self.blocks:
            result[b.name] = result.get(b.name, 0) + b.count
        return result


@dataclass(frozen=True, slots=True)
class PackingResult:
    spec: ContainerSpec
    kinds: tuple[CargoKind, ...]
    containers: tuple[LoadedContainer, ...]
    unplaced: dict[str, int] = field(default_factory=dict)
    problems: tuple[str, ...] = ()

    @property
    def containers_needed(self) -> int:
        return len(self.containers)

    @property
    def total_pieces(self) -> int:
        return sum(k.quantity for k in self.kinds)

    @property
    def placed_pieces(self) -> int:
        return sum(c.count for c in self.containers)

    @property
    def total_volume_m3(self) -> Decimal:
        return sum((k.piece_volume_m3 * k.quantity for k in self.kinds), ZERO)

    @property
    def total_weight_kg(self) -> Decimal:
        return sum((k.weight_kg * k.quantity for k in self.kinds), ZERO)

    @property
    def complete(self) -> bool:
        return not self.unplaced

    @property
    def binding(self) -> str:
        """Что ограничивает загрузку первого контейнера: «вес» или «объём»."""
        if not self.containers:
            return "—"
        first = self.containers[0]
        return "вес" if first.weight_share >= first.volume_share else "объём"


class PackingInputError(ValueError):
    pass


def validate(kinds: list[CargoKind]) -> list[str]:
    problems = []
    if not kinds:
        problems.append("Добавьте хотя бы один вид грузовых мест")
    if len(kinds) > MAX_KINDS:
        problems.append(f"Не больше {MAX_KINDS} видов грузовых мест в одной загрузке")
    for n, k in enumerate(kinds, 1):
        who = f"Место {n} «{k.name or n}»"
        if min(k.length_mm, k.width_mm, k.height_mm) <= 0:
            problems.append(f"{who}: размеры должны быть больше нуля")
        if k.quantity <= 0:
            problems.append(f"{who}: количество должно быть больше нуля")
        if k.weight_kg < 0:
            problems.append(f"{who}: вес не может быть отрицательным")
        if k.max_layers is not None and k.max_layers <= 0:
            problems.append(f"{who}: число ярусов должно быть больше нуля")
    return problems


def _fits_anywhere(kind: CargoKind, spec: ContainerSpec) -> bool:
    return any(
        d[0] <= spec.length_mm and d[1] <= spec.width_mm and d[2] <= spec.height_mm
        for d in kind.orientations()
    )


LOOKAHEAD = 6  # сколько лучших вариантов сравнивать с заглядыванием вперёд

# Стратегии выбора ведущего места стенки; из всех берётся лучший результат.
STRATEGIES = {
    "volume": lambda k, dims, left: k.piece_volume_mm3,
    "footprint": lambda k, dims, left: dims[1] * dims[0],
    "height": lambda k, dims, left: dims[2],
    "share": lambda k, dims, left: k.piece_volume_mm3 * left,
}


class _Packer:
    """Заполнение одного контейнера; left — сколько мест каждого вида ещё не уложено."""

    def __init__(
        self,
        spec: ContainerSpec,
        kinds: tuple[CargoKind, ...],
        left: list[int],
        strategy: str = "volume",
    ):
        self.spec, self.kinds, self.left = spec, kinds, left
        self.priority = STRATEGIES[strategy]
        # Расчёт в целых мм: Decimal здесь не нужен и в сотни раз медленнее
        self.volumes = [k.piece_volume_mm3 for k in kinds]
        self.orients = [k.orientations() for k in kinds]
        # Вес — в целых граммах (точно и быстро)
        self.weight_left = int(spec.max_cargo_kg * 1000)
        self.weights = [int((k.weight_kg * 1000).to_integral_value()) for k in kinds]
        self.blocks: list[Block] = []

    def _by_weight(self, i: int) -> int:
        w = self.weights[i]
        return self.left[i] if w <= 0 else min(self.left[i], self.weight_left // w)

    def _options(self, depth: int, width: int, height: int):
        """Варианты (объём, i, ориентация, deep, per_row, layers, count) для прямоугольника."""
        for i, kind in enumerate(self.kinds):
            available = self._by_weight(i)
            if available <= 0:
                continue
            for along, across, up in self.orients[i]:
                if along > depth or across > width or up > height:
                    continue
                deep, per_row = depth // along, width // across
                layers = height // up if kind.stackable else 1
                if kind.max_layers:
                    layers = min(layers, kind.max_layers)
                count = min(available, deep * per_row * layers)
                yield (count * self.volumes[i], i, (along, across, up), deep, per_row,
                       layers, count)  # fmt: skip

    @staticmethod
    def _top_allowed(kind: CargoKind) -> bool:
        return kind.stackable and not kind.max_layers

    @staticmethod
    def _footprint(option) -> tuple[int, int, int]:
        """Сколько мест в глубину, рядов и ярусов займёт вариант (неполный блок — частично)."""
        _, _, _, deep, per_row, _, count = option
        per_layer = deep * per_row
        layers_used = math.ceil(count / per_layer)
        if layers_used == 1:
            deep_used = min(deep, count)
            return deep_used, math.ceil(count / deep_used), 1
        return deep, per_row, layers_used

    def _lookahead(self, option, depth: int, width: int, height: int) -> int:
        """Объём варианта + лучший одиночный вариант для остатков сбоку и сверху."""
        volume, i, (_, across, up), *_ = option
        _, rows, layers = self._footprint(option)
        used_w, used_h = rows * across, layers * up
        side = max((o[0] for o in self._options(depth, width - used_w, height)), default=0)
        top = 0
        if self._top_allowed(self.kinds[i]):
            top = max((o[0] for o in self._options(depth, used_w, height - used_h)), default=0)
        return volume + side + top

    def _place(self, x: int, y: int, z: int, depth: int, width: int, height: int) -> None:
        """Заполняет прямоугольник поперечного среза; остатки сверху и сбоку — рекурсивно.

        Вариант выбирается с заглядыванием на шаг вперёд: важен не только его объём, но и
        что поместится рядом (3 паллеты поперёк + 2 вдоль лучше, чем 4 вдоль)."""
        options = sorted(self._options(depth, width, height), key=lambda o: o[0], reverse=True)
        options = [o for o in options if o[6] > 0][:LOOKAHEAD]
        if not options:
            return
        best = max(options, key=lambda o: self._lookahead(o, depth, width, height))
        _, i, (along, across, up), *_ = best
        count = best[6]
        kind = self.kinds[i]
        deep_used, rows_used, layers_used = self._footprint(best)
        block = Block(i, kind.name, count, x, y, z, along, across, up,
                      deep_used, rows_used, layers_used)  # fmt: skip
        self.blocks.append(block)
        self.left[i] -= count
        self.weight_left -= self.weights[i] * count
        used_w, used_h = block.width_mm, block.height_mm
        # Сверху — только если места можно штабелировать и для них не задан предел ярусов
        # (предел — обычно по прочности упаковки: выше него ничего не ставят)
        if self._top_allowed(kind) and height - used_h > 0:
            self._place(x, y, z + used_h, depth, used_w, height - used_h)
        if width - used_w > 0:
            self._place(x, y + used_w, z, depth, width - used_w, height)

    def _depths(self, length_left: int) -> set[int]:
        """Глубины стенки: размеры вдоль длины самого крупного из оставшихся мест."""
        candidates = [
            (self.priority(kind, dims, self.left[i]), dims[0])
            for i, kind in enumerate(self.kinds)
            if self._by_weight(i) > 0
            for dims in self.orients[i]
            if dims[0] <= length_left
            and dims[1] <= self.spec.width_mm
            and dims[2] <= self.spec.height_mm
        ]
        if not candidates:
            return set()
        biggest = max(v for v, _ in candidates)
        base = sorted({d for v, d in candidates if v == biggest})
        # Глубина в 2–3 места и общие кратные (2 400 = 3 × 800 = 2 × 1 200) позволяют
        # ставить в одной стенке места вдоль и поперёк — как европаллеты в 40' контейнере.
        depths = {d * k for d in base for k in (1, 2, 3)}
        depths |= {math.lcm(a, b) for a, b in itertools.combinations(base, 2)}
        return {d for d in depths if d <= length_left}

    def _wall(self, x: int, depth: int) -> list[Block]:
        before = len(self.blocks)
        self._place(x, 0, 0, depth, self.spec.width_mm, self.spec.height_mm)
        return self.blocks[before:]

    def _best_depth(self, x: int, length_left: int) -> int | None:
        """Пробная раскладка стенки для каждой глубины. Лучшая — больше груза на всю
        оставшуюся длину (если такие стенки повторять), при равенстве — на мм длины."""
        best = None
        for depth in sorted(self._depths(length_left)):
            saved = (list(self.left), self.weight_left, len(self.blocks))
            placed = self._wall(x, depth)
            volume = sum(self.volumes[b.kind_index] * b.count for b in placed)
            used = max((b.depth_mm for b in placed), default=depth)
            self.left[:], self.weight_left = saved[0], saved[1]
            del self.blocks[saved[2] :]
            score = (volume * (length_left // used), volume / used)
            if volume and (best is None or score > best[0]):
                best = (score, depth)
        return None if best is None else best[1]

    def fill(self) -> LoadedContainer:
        x = 0
        while any(self.left):
            depth = self._best_depth(x, self.spec.length_mm - x)
            if depth is None:
                break
            placed = self._wall(x, depth)
            if not placed:
                break
            x += max(b.depth_mm for b in placed)
        return LoadedContainer(self.spec, tuple(self.blocks), self.kinds)


def _pack_with(spec: ContainerSpec, kinds: tuple[CargoKind, ...], strategy: str):
    left = [k.quantity for k in kinds]
    containers: list[LoadedContainer] = []
    while any(left) and len(containers) < MAX_CONTAINERS:
        loaded = _Packer(spec, kinds, left, strategy).fill()
        if loaded.count == 0:
            break
        containers.append(loaded)
    return containers, left


def pack(kinds: list[CargoKind], spec: ContainerSpec) -> PackingResult:
    problems = validate(kinds)
    if problems:
        raise PackingInputError("; ".join(problems))
    kinds_t = tuple(kinds)

    def quality(attempt):
        containers, left = attempt
        # Меньше неуложенного, меньше контейнеров, плотнее первые контейнеры
        return (sum(left), len(containers), [-c.volume_m3 for c in containers])

    containers, left = min((_pack_with(spec, kinds_t, name) for name in STRATEGIES), key=quality)
    unplaced = {k.name: n for k, n in zip(kinds_t, left, strict=True) if n}
    notes: list[str] = []
    for k, n in zip(kinds_t, left, strict=True):
        if not n:
            continue
        if not _fits_anywhere(k, spec):
            hint = "" if k.can_tip else " (попробуйте разрешить кантование)"
            notes.append(f"«{k.name}» не помещается в {spec.title} ни в одной ориентации{hint}")
        elif k.weight_kg > spec.max_cargo_kg:
            notes.append(f"«{k.name}»: одно место тяжелее предельного веса груза {spec.title}")
        else:
            notes.append(f"«{k.name}»: не уложено {n} шт. — превышен лимит {MAX_CONTAINERS} "
                         "контейнеров")  # fmt: skip
    return PackingResult(spec, kinds_t, tuple(containers), unplaced, tuple(notes))


def compare(kinds: list[CargoKind]) -> list[PackingResult]:
    """Укладка во все типы контейнеров — для выбора подходящего."""
    return [pack(kinds, spec) for spec in load_containers().values()]


def best_choice(results: list[PackingResult]) -> PackingResult | None:
    """Меньше контейнеров, затем меньше суммарного объёма контейнеров."""
    complete = [r for r in results if r.complete and r.containers]
    if not complete:
        return None
    return min(
        complete, key=lambda r: (r.containers_needed, r.containers_needed * r.spec.volume_m3)
    )
