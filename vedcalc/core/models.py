"""Модель входных данных расчёта. Проценты хранятся как проценты (22 = 22%)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from vedcalc.core.money import RUB, ZERO, Money
from vedcalc.core.units import Unit


class DeliveryMode(StrEnum):
    """Способ доставки. Пока справочно, в будущем — для калькулятора загрузки."""

    DC20 = "20DC"
    DC40 = "40DC"
    HC40 = "40HC"
    LCL = "LCL"
    FTL = "FTL"
    RAIL = "RAIL"
    AIR = "AIR"

    @property
    def label(self) -> str:
        return {
            DeliveryMode.DC20: "Контейнер 20DC",
            DeliveryMode.DC40: "Контейнер 40DC",
            DeliveryMode.HC40: "Контейнер 40HC",
            DeliveryMode.LCL: "Сборный груз (LCL)",
            DeliveryMode.FTL: "Фура (FTL)",
            DeliveryMode.RAIL: "Ж/д контейнер",
            DeliveryMode.AIR: "Авиа",
        }[self]


class TaxRegime(StrEnum):
    OSN = "osn"  # НДС к вычету
    USN_INCOME_MINUS_EXPENSES = "usn_dr"  # УСН «доходы минус расходы», без НДС
    USN_INCOME = "usn_d"  # УСН «доходы» — заглушка

    @property
    def label(self) -> str:
        return {
            TaxRegime.OSN: "ОСН (НДС к вычету)",
            TaxRegime.USN_INCOME_MINUS_EXPENSES: "УСН «доходы минус расходы», без НДС",
            TaxRegime.USN_INCOME: "УСН «доходы» (не поддерживается)",
        }[self]

    @property
    def vat_payer(self) -> bool:
        return self is TaxRegime.OSN


class AllocationMethod(StrEnum):
    BY_VALUE = "by_value"
    BY_WEIGHT = "by_weight"

    @property
    def label(self) -> str:
        return {
            AllocationMethod.BY_VALUE: "пропорционально стоимости товара",
            AllocationMethod.BY_WEIGHT: "пропорционально весу брутто",
        }[self]


class DutyKind(StrEnum):
    AD_VALOREM = "ad_valorem"  # X% от таможенной стоимости
    SPECIFIC = "specific"  # Y EUR за кг/шт — не реализовано
    COMBINED = "combined"  # X%, но не менее Y EUR за кг/шт — не реализовано


@dataclass(slots=True)
class DutyRate:
    kind: DutyKind = DutyKind.AD_VALOREM
    percent: Decimal = ZERO
    specific: Money | None = None  # для специфической/комбинированной: сумма за единицу
    specific_unit: Unit | None = None

    @classmethod
    def ad_valorem(cls, percent: Decimal | int | str) -> DutyRate:
        return cls(DutyKind.AD_VALOREM, Decimal(percent))


class CostKind(StrEnum):
    DELIVERY_TO_BORDER = "delivery_to_border"
    INSURANCE = "insurance"
    DELIVERY_RF = "delivery_rf"
    BROKER = "broker"
    WAREHOUSE = "warehouse"  # СВХ
    CERTIFICATION = "certification"
    BANK = "bank"
    AGENT = "agent"
    OTHER = "other"


@dataclass(slots=True)
class Item:
    name: str
    unit_price: Money
    quantity: Decimal
    unit: Unit = Unit.PCS
    duty: DutyRate = field(default_factory=DutyRate)
    vat_rate: Decimal = Decimal("22")
    excise: Money = field(default_factory=lambda: Money.rub(0))  # заглушка
    tnved_code: str | None = None
    gross_weight_kg: Decimal | None = None
    net_weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None


@dataclass(slots=True)
class CostLine:
    name: str
    amount: Money
    in_customs_value: bool = False
    vat_included: bool = False  # сумма указана с НДС
    vat_rate: Decimal = ZERO
    kind: CostKind = CostKind.OTHER


class PriceMode(StrEnum):
    MARKUP = "markup"  # наценка k, % к себестоимости
    MARGIN = "margin"  # маржинальность m, % от цены
    NET_PROFIT = "net_profit"  # целевая чистая прибыль N на партию, руб.
    UNIT_PRICE = "unit_price"  # обратный расчёт: цена за единицу без НДС (одна позиция)
    PRICE = "price"  # обратный расчёт: цена партии без НДС

    @property
    def label(self) -> str:
        return {
            PriceMode.MARKUP: "Наценка, %",
            PriceMode.MARGIN: "Маржинальность, %",
            PriceMode.NET_PROFIT: "Чистая прибыль на партию, руб.",
            PriceMode.UNIT_PRICE: "Цена за единицу без НДС, руб.",
            PriceMode.PRICE: "Цена партии без НДС, руб.",
        }[self]


@dataclass(slots=True)
class SaleParams:
    vat_rate: Decimal = Decimal("22")  # v — НДС при продаже (при УСН игнорируется)
    profit_tax_rate: Decimal = Decimal("25")  # t — налог на прибыль / УСН
    price_dependent_rate: Decimal = ZERO  # r — расходы в % от выручки без НДС
    # Сценарий цены, который пользователь смотрит в блоке 3 (сохраняется с расчётом).
    target_mode: PriceMode = PriceMode.MARKUP
    target_value: Decimal = Decimal("20")

    @classmethod
    def for_regime(cls, regime: TaxRegime) -> SaleParams:
        if regime is TaxRegime.OSN:
            return cls()
        return cls(vat_rate=ZERO, profit_tax_rate=Decimal("15"))

    def with_regime_defaults(self, regime: TaxRegime) -> SaleParams:
        """Ставки налогов по умолчанию для режима; сценарий цены сохраняется."""
        fresh = SaleParams.for_regime(regime)
        fresh.price_dependent_rate = self.price_dependent_rate
        fresh.target_mode = self.target_mode
        fresh.target_value = self.target_value
        return fresh


@dataclass(slots=True)
class Shipment:
    name: str = "Новый расчёт"
    calc_date: date = field(default_factory=date.today)
    delivery_mode: DeliveryMode = DeliveryMode.DC40
    tax_regime: TaxRegime = TaxRegime.OSN
    allocation: AllocationMethod = AllocationMethod.BY_VALUE
    items: list[Item] = field(default_factory=list)
    costs: list[CostLine] = field(default_factory=list)
    # Ручные курсы «что если»: валюта → рублей за 1 единицу. Пусто — курсы ЦБ.
    manual_rates: dict[str, Decimal] = field(default_factory=dict)
    sale: SaleParams = field(default_factory=SaleParams)

    def currencies(self) -> set[str]:
        found = {i.unit_price.currency for i in self.items}
        found |= {i.excise.currency for i in self.items}
        found |= {c.amount.currency for c in self.costs}
        found |= {i.duty.specific.currency for i in self.items if i.duty.specific}
        return found - {RUB}


def default_cost_lines(currency: str = RUB) -> list[CostLine]:
    """Предустановленные строки расходов партии (см. ТЗ, п. 2.3)."""
    return [
        CostLine(
            "Доставка до границы ЕАЭС",
            Money(ZERO, currency),
            in_customs_value=True,
            kind=CostKind.DELIVERY_TO_BORDER,
        ),
        CostLine(
            "Страхование груза до границы",
            Money(ZERO, currency),
            in_customs_value=True,
            kind=CostKind.INSURANCE,
        ),
        CostLine(
            "Доставка по РФ (часть международной перевозки)",
            Money(ZERO, currency),
            kind=CostKind.DELIVERY_RF,
        ),
    ]
