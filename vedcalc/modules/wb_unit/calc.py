"""Юнит-экономика Wildberries для ИП на УСН (без GUI, не связана с импортом).

Всё считается на одну **проданную** единицу. При выкупе b на одну продажу приходится 1/b
заказов: прямая логистика оплачивается за каждый заказ, обратная — за каждый невыкуп.

    Цена продажи P = цена до скидки × (1 − скидка продавца)   — база комиссии, эквайринга,
                                                                 рекламы и налога
    Логистика к покупателю = тариф(литры) × коэф. склада × ИЛ
        тариф: до 1 л — по диапазонам; свыше — первый литр + доп. литры (дробно)
    Обратная логистика = тариф(литры) без коэффициента склада
    Логистика на продажу = прямая / b + обратная × (1/b − 1)
    Хранение = литры × ставка × коэф. склада × дни   (FBO)
    Приёмка = литры × ставка × коэф. приёмки         (FBO)
    К перечислению = P − комиссия − эквайринг − логистика − хранение − приёмка
    Налог: УСН «Доходы» = P × ставка; «Доходы − расходы» = max((P − расходы) × ставка, P × 1%)
    Прибыль = к перечислению − реклама − прочие − себестоимость − налог

Деньги — Decimal; суммы на единицу округляются до копеек построчно, итоги — из округлённых.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from functools import cache
from importlib import resources

from vedcalc.core.explain import fmt_money, fmt_number, fmt_pct, fmt_rate
from vedcalc.core.money import HUNDRED, ZERO, pct, round_money

ONE = Decimal(1)
LITER_CM3 = Decimal(1000)
MIN_TAX_PCT = Decimal(1)  # минимальный налог УСН «доходы − расходы», % от доходов


class Scheme(StrEnum):
    FBO = "fbo"  # склад WB
    FBS = "fbs"  # склад продавца

    @property
    def label(self) -> str:
        return {Scheme.FBO: "FBO — склад WB", Scheme.FBS: "FBS — свой склад"}[self]


class TaxMode(StrEnum):
    INCOME = "income"
    INCOME_MINUS_EXPENSES = "income_minus_expenses"

    @property
    def label(self) -> str:
        return {
            TaxMode.INCOME: "УСН «Доходы»",
            TaxMode.INCOME_MINUS_EXPENSES: "УСН «Доходы минус расходы»",
        }[self]


class Target(StrEnum):
    PRICE = "price"  # цена задана
    MARGIN = "margin"  # подобрать цену под маржу, %
    PROFIT = "profit"  # подобрать цену под прибыль с единицы, ₽

    @property
    def label(self) -> str:
        return {
            Target.PRICE: "Цена задана",
            Target.MARGIN: "Подобрать цену: маржа, %",
            Target.PROFIT: "Подобрать цену: прибыль с единицы, ₽",
        }[self]


@dataclass(frozen=True, slots=True)
class Tariffs:
    small_tiers: tuple[tuple[Decimal, Decimal], ...]  # (до литров включительно, ₽)
    first_liter: Decimal
    extra_liter: Decimal
    warehouse_coef_pct: Decimal
    storage_per_liter_day: Decimal
    acceptance_per_liter: Decimal
    acquiring_pct: Decimal
    valid_from: date
    verified_on: date
    sources: tuple[str, ...] = ()

    def base_logistics(self, liters: Decimal) -> Decimal:
        """Базовый тариф логистики за единицу (без коэффициента склада)."""
        if liters <= ONE:
            for up_to, rub in self.small_tiers:
                if liters <= up_to:
                    return rub
            return self.small_tiers[-1][1]
        return self.first_liter + self.extra_liter * (liters - ONE)

    def base_formula(self, liters: Decimal) -> str:
        if liters <= ONE:
            return f"до 1 л: {fmt_money(self.base_logistics(liters))} ₽ по диапазону объёма"
        return (
            f"({fmt_money(self.first_liter)} + {fmt_money(self.extra_liter)} × "
            f"{fmt_number(liters - ONE)} л)"
        )


@cache
def load_tariffs() -> Tariffs:
    raw = json.loads(resources.files("vedcalc.data").joinpath("wb_tariffs.json").read_text("utf-8"))
    return Tariffs(
        small_tiers=tuple(
            (Decimal(t["up_to_liters"]), Decimal(t["rub"])) for t in raw["logistics_small_tiers"]
        ),
        first_liter=Decimal(raw["first_liter_rub"]),
        extra_liter=Decimal(raw["extra_liter_rub"]),
        warehouse_coef_pct=Decimal(raw["warehouse_coef_pct"]),
        storage_per_liter_day=Decimal(raw["storage_rub_per_liter_day"]),
        acceptance_per_liter=Decimal(raw["acceptance_rub_per_liter"]),
        acquiring_pct=Decimal(raw["acquiring_pct"]),
        valid_from=date.fromisoformat(raw["valid_from"]),
        verified_on=date.fromisoformat(raw["verified_on"]),
        sources=tuple(raw.get("sources", [])),
    )


@dataclass(slots=True)
class WbInput:
    name: str = "Товар"
    length_cm: Decimal = Decimal(20)
    width_cm: Decimal = Decimal(15)
    height_cm: Decimal = Decimal(10)
    cost_price: Decimal = Decimal(500)  # себестоимость единицы, ₽
    retail_price: Decimal = Decimal(2000)  # цена до скидки, ₽
    seller_discount_pct: Decimal = Decimal(30)
    scheme: Scheme = Scheme.FBO
    commission_fbo_pct: Decimal = Decimal(25)
    commission_fbs_pct: Decimal = Decimal(28)
    buyout_pct: Decimal = Decimal(90)
    warehouse_coef_pct: Decimal | None = None  # None — из тарифов
    localization_index: Decimal = ONE
    storage_days: int = 30
    acceptance_coef: Decimal = ZERO  # 0 — бесплатная приёмка
    acquiring_pct: Decimal | None = None  # None — из тарифов
    ads_pct: Decimal = ZERO
    other_per_unit: Decimal = ZERO  # упаковка, маркировка, доставка до WB…
    tax_mode: TaxMode = TaxMode.INCOME
    tax_rate_pct: Decimal = Decimal(6)
    target: Target = Target.PRICE
    target_value: Decimal = ZERO
    # Базовые тарифы можно переопределить (если WB поменял их раньше, чем файл)
    first_liter: Decimal | None = None
    extra_liter: Decimal | None = None
    storage_per_liter_day: Decimal | None = None
    # Тарифы склада из API WB: хранение «первый литр + доп. литр» и свой коэффициент
    storage_first_liter: Decimal | None = None
    storage_coef_pct: Decimal | None = None  # None — коэффициент склада


class WbInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    formula: str
    value: Decimal
    strong: bool = False
    informational: bool = False  # справочно, не входит в сумму

    @property
    def text(self) -> str:
        return fmt_money(self.value)


@dataclass(frozen=True, slots=True)
class WbResult:
    inp: WbInput
    liters: Decimal
    retail_price: Decimal
    price: Decimal  # цена продажи P (после скидки продавца)
    commission: Decimal
    acquiring: Decimal
    logistics: Decimal
    storage: Decimal
    acceptance: Decimal
    payout: Decimal  # к перечислению от WB
    ads: Decimal
    other: Decimal
    cost_price: Decimal
    tax: Decimal
    profit: Decimal
    lines: tuple[Line, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def margin(self) -> Decimal | None:
        return self.profit / self.price if self.price else None

    @property
    def roi(self) -> Decimal | None:
        return self.profit / self.cost_price if self.cost_price else None

    @property
    def wb_expenses(self) -> Decimal:
        return self.commission + self.acquiring + self.logistics + self.storage + self.acceptance


def validate(inp: WbInput) -> list[str]:
    errors = []
    for attr, label in (("length_cm", "Длина"), ("width_cm", "Ширина"), ("height_cm", "Высота")):
        if getattr(inp, attr) <= 0:
            errors.append(f"{label} упаковки должна быть больше нуля")
    if inp.cost_price < 0:
        errors.append("Себестоимость не может быть отрицательной")
    if inp.target is Target.PRICE and inp.retail_price <= 0:
        errors.append("Цена до скидки должна быть больше нуля")
    if not ZERO <= inp.seller_discount_pct < HUNDRED:
        errors.append("Скидка продавца — от 0 до 99%")
    if not ZERO < inp.buyout_pct <= HUNDRED:
        errors.append("Процент выкупа — больше 0 и не больше 100")
    for value, label in (
        (inp.commission_fbo_pct, "Комиссия FBO"),
        (inp.commission_fbs_pct, "Комиссия FBS"),
        (inp.ads_pct, "Реклама"),
        (inp.tax_rate_pct, "Ставка налога"),
    ):
        if not ZERO <= value < HUNDRED:
            errors.append(f"{label} — от 0 до 99%")
    if inp.tax_mode is TaxMode.INCOME and not Decimal(1) <= inp.tax_rate_pct <= Decimal(8):
        errors.append("Ставка УСН «Доходы» — от 1 до 8%")
    if inp.storage_days < 0:
        errors.append("Дней хранения — не меньше нуля")
    if inp.localization_index <= 0:
        errors.append("Индекс локализации должен быть больше нуля")
    return errors


def liters_of(inp: WbInput) -> Decimal:
    """Объём упаковки в литрах (как у WB — по габаритам упаковки, до тысячных)."""
    return (inp.length_cm * inp.width_cm * inp.height_cm / LITER_CM3).quantize(Decimal("0.001"))


def _tariffs(inp: WbInput, base: Tariffs) -> Tariffs:
    """Тарифы с переопределениями пользователя."""
    return Tariffs(
        base.small_tiers,
        inp.first_liter if inp.first_liter is not None else base.first_liter,
        inp.extra_liter if inp.extra_liter is not None else base.extra_liter,
        inp.warehouse_coef_pct if inp.warehouse_coef_pct is not None else base.warehouse_coef_pct,
        inp.storage_per_liter_day
        if inp.storage_per_liter_day is not None
        else base.storage_per_liter_day,
        base.acceptance_per_liter,
        inp.acquiring_pct if inp.acquiring_pct is not None else base.acquiring_pct,
        base.valid_from,
        base.verified_on,
        base.sources,
    )


def evaluate(inp: WbInput, base: Tariffs | None = None, retail_price: Decimal | None = None):
    """Юнит-экономика при заданной цене до скидки (по умолчанию — из ввода)."""
    t = _tariffs(inp, base or load_tariffs())
    retail = round_money(inp.retail_price if retail_price is None else retail_price)
    liters = liters_of(inp)
    b = pct(inp.buyout_pct)
    fbo = inp.scheme is Scheme.FBO
    coef = pct(t.warehouse_coef_pct)
    commission_pct = inp.commission_fbo_pct if fbo else inp.commission_fbs_pct

    price = round_money(retail * (ONE - pct(inp.seller_discount_pct)))
    commission = round_money(price * pct(commission_pct))
    acquiring = round_money(price * pct(t.acquiring_pct))
    base_tariff = t.base_logistics(liters)
    forward = round_money(base_tariff * coef * inp.localization_index)
    reverse = round_money(base_tariff)
    orders = ONE / b
    logistics = round_money(forward * orders + reverse * (orders - ONE))
    storage_coef_pct = (
        inp.storage_coef_pct if inp.storage_coef_pct is not None else t.warehouse_coef_pct
    )
    if inp.storage_first_liter is not None:
        storage_day = inp.storage_first_liter + t.storage_per_liter_day * max(liters - ONE, ZERO)
        storage_base = (
            f"({fmt_money(inp.storage_first_liter)} + {fmt_money(t.storage_per_liter_day)} × "
            f"{fmt_number(max(liters - ONE, ZERO))} л)"
        )
    else:
        storage_day = liters * t.storage_per_liter_day
        storage_base = f"{fmt_number(liters)} л × {fmt_money(t.storage_per_liter_day)}"
    storage = round_money(storage_day * pct(storage_coef_pct) * inp.storage_days) if fbo else ZERO
    acceptance = round_money(liters * t.acceptance_per_liter * inp.acceptance_coef) if fbo else ZERO
    payout = price - commission - acquiring - logistics - storage - acceptance
    ads = round_money(price * pct(inp.ads_pct))
    other = round_money(inp.other_per_unit)
    cost = round_money(inp.cost_price)

    rate = pct(inp.tax_rate_pct)
    if inp.tax_mode is TaxMode.INCOME:
        tax = round_money(price * rate)
        tax_formula = f"{fmt_money(price)} × {fmt_rate(inp.tax_rate_pct)}"
    else:
        expenses = price - payout + ads + other + cost
        base_tax = round_money(max(ZERO, price - expenses) * rate)
        minimum = round_money(price * pct(MIN_TAX_PCT))
        tax = max(base_tax, minimum)
        tax_formula = (
            f"max(({fmt_money(price)} − {fmt_money(expenses)}) × {fmt_rate(inp.tax_rate_pct)}; "
            f"минимум 1% = {fmt_money(minimum)})"
        )
    profit = payout - ads - other - cost - tax

    li = f"{fmt_number(liters)} л"
    lines = [
        Line("Цена до скидки", "задана" if retail_price is None else "подобрана", retail,
             informational=True),
        Line("Цена продажи", f"{fmt_money(retail)} × (1 − {fmt_rate(inp.seller_discount_pct)})",
             price, strong=True),
        Line(f"Комиссия WB ({'FBO' if fbo else 'FBS'})",
             f"{fmt_money(price)} × {fmt_rate(commission_pct)}", commission),
        Line("Эквайринг", f"{fmt_money(price)} × {fmt_rate(t.acquiring_pct)}", acquiring),
        Line("Логистика к покупателю (за заказ)",
             f"{t.base_formula(liters)} × {fmt_rate(t.warehouse_coef_pct)}"
             + (f" × ИЛ {fmt_number(inp.localization_index)}" if inp.localization_index != ONE
                else ""),
             forward, informational=True),
        Line("Обратная логистика (за невыкуп)", f"{t.base_formula(liters)}, без коэф. склада",
             reverse, informational=True),
        Line("Логистика на продажу",
             f"{fmt_money(forward)} / {fmt_rate(inp.buyout_pct)} + {fmt_money(reverse)} × "
             f"(1 / {fmt_rate(inp.buyout_pct)} − 1)", logistics),
    ]  # fmt: skip
    if fbo:
        lines += [
            Line("Хранение",
                 f"{storage_base} × {fmt_rate(storage_coef_pct)} × {inp.storage_days} дн.",
                 storage),
            Line("Приёмка", f"{li} × {fmt_money(t.acceptance_per_liter)} × коэф. "
                 f"{fmt_number(inp.acceptance_coef)}", acceptance),
        ]  # fmt: skip
    lines.append(Line("К перечислению от WB", "цена продажи − расходы WB", payout, strong=True))
    if ads:
        lines.append(Line("Реклама", f"{fmt_money(price)} × {fmt_rate(inp.ads_pct)}", ads))
    if other:
        lines.append(Line("Прочие расходы", "на единицу", other))
    lines += [
        Line("Себестоимость", "закупка и доставка до склада", cost),
        Line(f"Налог ({inp.tax_mode.label})", tax_formula, tax),
        Line("Чистая прибыль", "к перечислению − реклама − прочие − себестоимость − налог",
             profit, strong=True),
    ]  # fmt: skip

    warnings = []
    if (date.today() - t.verified_on).days > 60:
        warnings.append(
            f"Тарифы WB сверены {t.verified_on:%d.%m.%Y} — проверьте их в кабинете продавца"
        )
    return WbResult(inp, liters, retail, price, commission, acquiring, logistics, storage,
                    acceptance, payout, ads, other, cost, tax, profit, tuple(lines),
                    tuple(warnings))  # fmt: skip


def solve(inp: WbInput, base: Tariffs | None = None) -> WbResult:
    """Расчёт по режиму: цена задана — как есть; иначе подбор цены до скидки (бисекция).

    Прибыль растёт с ценой монотонно (все расходы WB — доля цены < 100%), поэтому
    цена до скидки находится делением отрезка пополам с точностью до копейки.
    """
    errors = validate(inp)
    if errors:
        raise WbInputError("; ".join(errors))
    if inp.target is Target.PRICE:
        return evaluate(inp, base)

    def goal(result: WbResult) -> Decimal:
        if inp.target is Target.MARGIN:
            return result.profit - result.price * pct(inp.target_value)
        return result.profit - inp.target_value

    if inp.target is Target.MARGIN and inp.target_value >= HUNDRED:
        raise WbInputError("Маржа должна быть меньше 100%")
    low, high = Decimal("0.01"), Decimal(100)
    while goal(evaluate(inp, base, high)) < 0:
        high *= 2
        if high > Decimal(100_000_000):
            raise WbInputError("Такую маржу получить нельзя: расходы съедают всю цену")
    while high - low > Decimal("0.01"):
        mid = round_money((low + high) / 2)
        if mid in (low, high):
            break
        if goal(evaluate(inp, base, mid)) >= 0:
            high = mid
        else:
            low = mid
    return evaluate(inp, base, high)


def compare_schemes(inp: WbInput, base: Tariffs | None = None) -> dict[Scheme, WbResult]:
    """Тот же товар по FBO и по FBS (для подбора цены — при цене, найденной для каждой)."""
    out = {}
    for scheme in Scheme:
        copy = WbInput(**{f: getattr(inp, f) for f in inp.__dataclass_fields__})
        copy.scheme = scheme
        out[scheme] = solve(copy, base)
    return out


def summary(result: WbResult) -> str:
    return (
        f"Прибыль {fmt_money(result.profit)} ₽ с продажи · маржа {fmt_pct(result.margin)} · "
        f"ROI {fmt_pct(result.roi)}"
    )
