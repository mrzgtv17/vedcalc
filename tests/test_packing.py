"""Модуль «Контейнер»: укладка грузовых мест, лимит веса, сравнение типов."""

import random
from decimal import Decimal as D

import pytest

from vedcalc.core.containers import load_containers
from vedcalc.modules.container_calc.packing import (
    MAX_KINDS,
    CargoKind,
    PackingInputError,
    best_choice,
    compare,
    pack,
)

C = load_containers()


def euro(qty=40, **kw):
    return CargoKind("Европаллета", 1200, 800, 1000, D(300), qty, stackable=False, **kw)


@pytest.mark.parametrize(("code", "norm"), [("20DC", 11), ("40DC", 25), ("40HC", 25), ("45PW", 33)])
def test_europallets_match_industry_norms(code, norm):
    """Отраслевые нормы: 11 / 25 / 25 / 33 европаллеты в один ярус."""
    assert pack([euro()], C[code]).containers[0].count == norm


def test_stacking_and_tipping():
    box = CargoKind("Ящик", 1200, 1000, 800, D(100), 100)
    first = pack([box], C["40HC"]).containers[0]
    # Смешанные ряды: 1 200 + 1 000 = 2 200 мм поперёк ≤ 2 330 мм → 33 места на 6 м × 2 = 66
    # (одна ориентация дала бы только 2 × 3 × 10 = 60)
    assert first.count == 66
    flat = pack([CargoKind("Ящик", 1200, 1000, 800, D(100), 100, stackable=False)], C["40HC"])
    assert flat.containers[0].count == 22  # без штабелирования — один ярус (66 / 3)
    two = pack([CargoKind("Ящик", 1200, 1000, 800, D(100), 100, max_layers=2)], C["40HC"])
    assert two.containers[0].count == 44  # не более 2 ярусов, выше ничего


def test_weight_limit_binds():
    heavy = CargoKind("Паллета", 1200, 1000, 800, D(400), 100)
    result = pack([heavy], C["40HC"])
    first = result.containers[0]
    assert first.count == 65  # 26 000 кг / 400 кг
    assert first.weight_kg == D(26000) and result.binding == "вес"
    assert result.containers_needed == 2 and result.complete


def test_20ft_has_higher_weight_limit():
    heavy = CargoKind("Слиток", 300, 300, 200, D(1000), 40)
    assert pack([heavy], C["20DC"]).containers[0].count == 27
    assert pack([heavy], C["40DC"]).containers[0].count == 26


def test_tipping_lets_long_items_fit():
    pipe = CargoKind("Труба", 400, 400, 2800, D(50), 10)  # выше контейнера стоя
    result = pack([pipe], C["40HC"])
    assert not result.complete and "кантование" in result.problems[0]
    laid = pack([CargoKind("Труба", 400, 400, 2800, D(50), 10, can_tip=True)], C["40HC"])
    assert laid.complete and laid.containers_needed == 1


def test_too_heavy_piece():
    result = pack([CargoKind("Пресс", 1000, 1000, 1000, D(30000), 1)], C["40HC"])
    assert result.unplaced == {"Пресс": 1}
    assert "тяжелее" in result.problems[0]


def test_mixed_cargo_fills_gaps():
    mix = [
        CargoKind("Коробка", 600, 400, 400, D(18), 300),
        CargoKind("Бочка", 600, 600, 900, D(220), 40, stackable=False),
        CargoKind("Рулон", 1500, 300, 300, D(25), 120, can_tip=True),
    ]
    result = pack(mix, C["45PW"])
    assert result.complete and result.containers_needed == 1
    loaded = result.containers[0]
    assert loaded.counts() == {"Бочка": 40, "Рулон": 120, "Коробка": 300}
    # Ничего не стоит на бочках, ничего не выходит за стены
    spec = loaded.spec
    for b in loaded.blocks:
        assert b.x_mm + b.depth_mm <= spec.length_mm
        assert b.y_mm + b.width_mm <= spec.width_mm
        assert b.z_mm + b.height_mm <= spec.height_mm
    barrels = [b for b in loaded.blocks if b.name == "Бочка"]
    for b in loaded.blocks:
        for barrel in barrels:
            above = b.z_mm >= barrel.z_mm + barrel.height_mm
            overlaps = (
                b.x_mm < barrel.x_mm + barrel.depth_mm
                and barrel.x_mm < b.x_mm + b.depth_mm
                and b.y_mm < barrel.y_mm + barrel.width_mm
                and barrel.y_mm < b.y_mm + b.width_mm
            )
            assert not (above and overlaps), "на бочки ничего не ставится"


def test_blocks_never_overlap():
    random.seed(7)
    kinds = [
        CargoKind(f"К{i}", random.randint(200, 1200), random.randint(200, 1000),
                  random.randint(150, 900), D(random.randint(5, 120)), random.randint(3, 40),
                  can_tip=random.random() < 0.5, stackable=random.random() < 0.8)
        for i in range(MAX_KINDS)
    ]  # fmt: skip
    result = pack(kinds, C["40HC"])
    assert result.placed_pieces == result.total_pieces
    for loaded in result.containers:
        boxes = [(b.x_mm, b.y_mm, b.z_mm, b.depth_mm, b.width_mm, b.height_mm)
                 for b in loaded.blocks]  # fmt: skip
        for n, a in enumerate(boxes):
            for b in boxes[n + 1 :]:
                overlap = all(a[k] < b[k] + b[k + 3] and b[k] < a[k] + a[k + 3] for k in range(3))
                assert not overlap
        assert loaded.weight_kg <= loaded.spec.max_cargo_kg


def test_compare_and_best_choice():
    results = compare([euro(30)])
    by_code = {r.spec.code: r.containers_needed for r in results}
    assert by_code["20DC"] == 3 and by_code["40DC"] == 2 and by_code["45PW"] == 1
    assert best_choice(results).spec.code in {"45PW", "45HCPW"}


def test_validation():
    with pytest.raises(PackingInputError, match="хотя бы один"):
        pack([], C["20DC"])
    with pytest.raises(PackingInputError, match="больше нуля"):
        pack([CargoKind("x", 0, 1, 1, D(1), 1)], C["20DC"])
    with pytest.raises(PackingInputError, match="Не больше 20"):
        pack([euro(1)] * 21, C["20DC"])
