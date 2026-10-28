"""Модель входных данных расчёта. Проценты хранятся как проценты (22 = 22%)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from vedcalc.core.money import RUB, ZERO, Money
from vedcalc.core.units import Unit


class DeliveryMode(StrEnum):
    """Способ доставки. Для контейнеров — подсказка о загрузке по весу и объёму."""

    DC20 = "20DC"
    HC20 = "20HC"
    DC40 = "40DC"
    HC40 = "40HC"
    PW45 = "45PW"
    HCPW45 = "45HCPW"
    LCL = "LCL"
    FTL = "FTL"
    RAIL = "RAIL"
    AIR = "AIR"

    @property
    def label(self) -> str:
        return {
            DeliveryMode.DC20: "Контейнер 20' DC",
            DeliveryMode.HC20: "Контейнер 20' HC",
            DeliveryMode.DC40: "Контейнер 40' DC",
            DeliveryMode.HC40: "Контейнер 40' HC",
            DeliveryMode.PW45: "Контейнер 45' PW",
            DeliveryMode.HCPW45: "Контейнер 45' HC PW",
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
    SPECIFIC = "specific"  # Y EUR за единицу (кг, л, шт…)
    COMBINED = "combined"  # X%, но не менее Y EUR за единицу — большая из двух
    SUM = "sum"  # X% плюс Y EUR за единицу


class DutyUnit(StrEnum):
    """Единица специфической ставки пошлины (как в ЕТТ)."""

    KG = "kg"
    T = "t"
    PCS = "pcs"
    L = "l"
    PAIR = "pair"
    CM3 = "cm3"
    M2 = "m2"

    @property
    def label(self) -> str:
        return {
            DutyUnit.KG: "кг", DutyUnit.T: "т", DutyUnit.PCS: "шт", DutyUnit.L: "л",
            DutyUnit.PAIR: "пар", DutyUnit.CM3: "см³", DutyUnit.M2: "м²",
        }[self]  # fmt: skip

    @property
    def by_weight(self) -> bool:
        """Ставка за массу — считается от веса нетто."""
        return self in (DutyUnit.KG, DutyUnit.T)


@dataclass(slots=True)
class DutyRate:
    kind: DutyKind = DutyKind.AD_VALOREM
    percent: Decimal = ZERO
    specific: Money | None = None  # сумма специфической части: Y EUR / USD…
    specific_unit: DutyUnit | None = None  # …за per_quantity единиц
    per_quantity: Decimal = Decimal(1)  # «за 1 кг», «за 1000 шт»
    source: str = ""  # исходная строка ставки из справочника ЕТТ («50%, но не менее…»)

    @classmethod
    def ad_valorem(cls, percent: Decimal | int | str) -> DutyRate:
        return cls(DutyKind.AD_VALOREM, Decimal(percent))

    @property
    def has_specific(self) -> bool:
        return self.kind is not DutyKind.AD_VALOREM

    @property
    def label(self) -> str:
        if self.source:
            return self.source
        if not self.has_specific:
            return f"{self.percent.normalize():f}%".replace(".", ",")
        per = f"{self.per_quantity.normalize():f} " if self.per_quantity != 1 else ""
        spec = f"{self.specific.amount} {self.specific.currency} за {per}{self.specific_unit.label}"
        pct_txt = f"{self.percent.normalize():f}%".replace(".", ",")
        return {
            DutyKind.SPECIFIC: spec,
            DutyKind.COMBINED: f"{pct_txt}, но не менее {spec}",
            DutyKind.SUM: f"{pct_txt} плюс {spec}",
        }[self.kind]


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
    # Количество в единице специфической ставки (л, пары, м², см³), если она не кг/т/шт
    duty_quantity: Decimal | None = None


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


class FinancingMode(StrEnum):
    OFF = "off"
    SIMPLE = "simple"  # сумма × ставка × дни / 365
    SCHEDULE = "schedule"  # по графику платежей поставщику и от покупателя

    @property
    def label(self) -> str:
        return {
            FinancingMode.OFF: "Не учитывать",
            FinancingMode.SIMPLE: "Упрощённо: сумма × ставка × срок",
            FinancingMode.SCHEDULE: "По графику платежей",
        }[self]


@dataclass(slots=True)
class Financing:
    """Стоимость денег: проценты за деньги, вложенные в партию до оплаты покупателем.

    График (дни от предоплаты поставщику = день 0):
    предоплата поставщику → остаток поставщику → прибытие (логистика) →
    выпуск (пошлина, НДС, сбор) → поставка покупателю → оплата покупателя.
    """

    mode: FinancingMode = FinancingMode.OFF
    annual_rate: Decimal = Decimal("20")  # % годовых
    borrowed_share: Decimal = Decimal("100")  # доля заёмных денег во вложениях, %
    # Упрощённо
    loan_days: int = 60
    loan_amount: Decimal | None = None  # None — себестоимость партии с НДС
    # По графику
    supplier_prepay_pct: Decimal = Decimal("30")
    supplier_balance_day: int = 30
    transit_days: int = 40
    customs_days: int = 5
    delivery_days: int = 7
    buyer_prepay_pct: Decimal = ZERO
    buyer_prepay_day: int = 0
    buyer_deferral_days: int = 30


class PricingSource(StrEnum):
    IMPORT = "import"  # себестоимость из вкладки «Импорт»
    MANUAL = "manual"  # себестоимость вводится вручную

    @property
    def label(self) -> str:
        return {
            PricingSource.IMPORT: "Из расчёта импорта",
            PricingSource.MANUAL: "Ввести вручную",
        }[self]


@dataclass(slots=True)
class ManualCost:
    """Себестоимость, заданная вручную (вкладка «Цена продажи», без расчёта импорта)."""

    name: str = "Товар"
    quantity: Decimal = Decimal(1)
    unit: Unit = Unit.PCS
    unit_cost_net: Decimal = ZERO  # себестоимость единицы без НДС (при УСН — с НДС)
    unit_deductible_vat: Decimal = ZERO  # НДС к вычету на единицу
    tax_regime: TaxRegime = TaxRegime.OSN


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
    financing: Financing = field(default_factory=Financing)
    pricing_source: PricingSource = PricingSource.IMPORT
    manual_cost: ManualCost = field(default_factory=ManualCost)

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
