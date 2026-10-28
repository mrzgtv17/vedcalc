"""Проверка входных данных с понятными сообщениями (путь к полю + текст)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.customs import DutyQuantityError, duty_base_quantity
from vedcalc.core.models import AllocationMethod, Shipment, TaxRegime
from vedcalc.core.money import HUNDRED, ZERO

ALLOWED_IMPORT_VAT = (Decimal("22"), Decimal("10"), ZERO)


@dataclass(frozen=True, slots=True)
class Issue:
    path: str  # например "items[0].quantity" — GUI подсвечивает поле
    message: str


class InputError(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues))


def _check_rate(issues: list[Issue], path: str, label: str, value: Decimal, *, below_100: bool):
    if value < 0:
        issues.append(Issue(path, f"{label}: значение не может быть отрицательным"))
    elif below_100 and value >= HUNDRED:
        issues.append(Issue(path, f"{label}: значение должно быть меньше 100%"))
    elif value > HUNDRED:
        issues.append(Issue(path, f"{label}: значение не может быть больше 100%"))


def validate_shipment(shipment: Shipment) -> list[Issue]:
    issues: list[Issue] = []
    if shipment.tax_regime is TaxRegime.USN_INCOME:
        issues.append(
            Issue(
                "tax_regime",
                "Режим УСН «доходы» пока не поддерживается — выберите ОСН "
                "или УСН «доходы минус расходы»",
            )
        )
    if not shipment.items:
        issues.append(Issue("items", "Добавьте хотя бы одну позицию товара"))
    freight = shipment.freight
    if freight.rate is not None and freight.rate.amount < 0:
        issues.append(Issue("freight.rate", "Ставка фрахта не может быть отрицательной"))
    if freight.containers is not None and freight.containers < 1:
        issues.append(Issue("freight.containers", "Число контейнеров — от 1"))

    for n, item in enumerate(shipment.items):
        p = f"items[{n}]"
        who = f"Позиция {n + 1}"
        if not item.name.strip():
            issues.append(Issue(f"{p}.name", f"{who}: укажите наименование"))
        if item.quantity <= 0:
            issues.append(Issue(f"{p}.quantity", f"{who}: количество должно быть больше нуля"))
        if item.unit_price.amount < 0:
            issues.append(Issue(f"{p}.unit_price", f"{who}: цена не может быть отрицательной"))
        pk = item.packaging
        if pk is not None:
            dims = (pk.length_cm, pk.width_cm, pk.height_cm, pk.units_per_place)
            if min(dims) <= 0 or pk.gross_kg < 0:
                issues.append(Issue(f"{p}.packaging", f"{who}: размеры места и количество в "
                                    "месте должны быть больше нуля"))  # fmt: skip
        if item.excise.amount < 0:
            issues.append(Issue(f"{p}.excise", f"{who}: акциз не может быть отрицательным"))
        if item.vat_rate not in ALLOWED_IMPORT_VAT:
            issues.append(Issue(f"{p}.vat_rate", f"{who}: ставка НДС может быть 22%, 10% или 0%"))
        if item.duty.has_specific:
            try:
                duty_base_quantity(item.duty, item)
            except DutyQuantityError as exc:
                field = "net_weight_kg" if item.duty.specific_unit.by_weight else "duty_quantity"
                issues.append(Issue(f"{p}.{field}", f"{who}: {exc}"))
        if item.duty.percent < 0:
            issues.append(Issue(f"{p}.duty", f"{who}: ставка пошлины не может быть отрицательной"))
        code = (item.tnved_code or "").strip()
        if code and not (code.isdigit() and len(code) == 10):
            issues.append(Issue(f"{p}.tnved_code", f"{who}: код ТН ВЭД — 10 цифр"))
        for attr, label in (
            ("gross_weight_kg", "вес брутто"),
            ("net_weight_kg", "вес нетто"),
            ("volume_m3", "объём"),
        ):
            value = getattr(item, attr)
            if value is not None and value < 0:
                issues.append(Issue(f"{p}.{attr}", f"{who}: {label} не может быть отрицательным"))
        if shipment.allocation is AllocationMethod.BY_WEIGHT and item.gross_weight_kg is None:
            issues.append(
                Issue(
                    f"{p}.gross_weight_kg",
                    f"{who}: для распределения расходов по весу укажите вес брутто",
                )
            )

    if (
        shipment.items
        and sum((i.unit_price.amount * i.quantity for i in shipment.items), ZERO) <= 0
    ):
        issues.append(Issue("items", "Общая стоимость товара должна быть больше нуля"))
    if (
        shipment.allocation is AllocationMethod.BY_WEIGHT
        and all(i.gross_weight_kg is not None for i in shipment.items)
        and sum((i.gross_weight_kg for i in shipment.items), ZERO) <= 0
    ):
        issues.append(Issue("items", "Для распределения по весу общий вес брутто должен быть > 0"))

    for n, line in enumerate(shipment.costs):
        p = f"costs[{n}]"
        who = f"Расход «{line.name or n + 1}»"
        if line.amount.amount < 0:
            issues.append(Issue(f"{p}.amount", f"{who}: сумма не может быть отрицательной"))
        _check_rate(issues, f"{p}.vat_rate", f"{who}, ставка НДС", line.vat_rate, below_100=False)

    sale = shipment.sale
    _check_rate(issues, "sale.vat_rate", "НДС при продаже", sale.vat_rate, below_100=False)
    _check_rate(
        issues, "sale.profit_tax_rate", "Налог на прибыль", sale.profit_tax_rate, below_100=True
    )
    _check_rate(
        issues,
        "sale.price_dependent_rate",
        "Расходы в % от выручки",
        sale.price_dependent_rate,
        below_100=True,
    )
    return issues


def ensure_valid(shipment: Shipment) -> None:
    issues = validate_shipment(shipment)
    if issues:
        raise InputError(issues)
