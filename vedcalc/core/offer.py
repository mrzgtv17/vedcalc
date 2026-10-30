"""Коммерческое предложение (КП) для клиента: цены и условия — без себестоимости и маржи.

Модель КП содержит только то, что видит клиент: позиции, цену за единицу с НДС, суммы,
выделенный НДС, условия оплаты и поставки, реквизиты продавца. Внутренних показателей
(себестоимость, наценка, прибыль, стоимость денег) в ней нет по построению.

Суммы считаются от округлённой цены за единицу — клиент может пересчитать их сам.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from vedcalc.core.explain import fmt_money, fmt_number, fmt_rate
from vedcalc.core.models import Financing, FinancingMode, TierPricing
from vedcalc.core.money import HUNDRED, ZERO, round_money
from vedcalc.core.report import PriceReport
from vedcalc.core.tiers import tier_label, tier_table


class OfferStyle(StrEnum):
    PROGRAM = "program"
    NEUTRAL = "neutral"
    MONO = "mono"

    @property
    def label(self) -> str:
        return {
            OfferStyle.PROGRAM: "В стиле программы",
            OfferStyle.NEUTRAL: "Нейтральный деловой",
            OfferStyle.MONO: "Строгий монохром",
        }[self]


@dataclass(slots=True)
class Requisites:
    """Реквизиты продавца — хранятся на компьютере, а не в файле расчёта."""

    company: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    bank_name: str = ""
    bik: str = ""
    account: str = ""
    corr_account: str = ""
    signer_name: str = ""
    signer_position: str = ""
    logo_path: str = ""
    signature_path: str = ""
    stamp_path: str = ""

    @property
    def tax_ids(self) -> str:
        parts = [
            f"ИНН {self.inn}" if self.inn else "",
            f"КПП {self.kpp}" if self.kpp else "",
            f"{'ОГРНИП' if len(self.ogrn) == 15 else 'ОГРН'} {self.ogrn}" if self.ogrn else "",
        ]
        return " · ".join(p for p in parts if p)

    @property
    def contacts(self) -> str:
        return " · ".join(p for p in (self.phone, self.email, self.website) if p)

    @property
    def bank(self) -> str:
        if not (self.bank_name or self.account):
            return ""
        parts = [
            f"р/с {self.account}" if self.account else "",
            self.bank_name,
            f"БИК {self.bik}" if self.bik else "",
            f"к/с {self.corr_account}" if self.corr_account else "",
        ]
        return ", ".join(p for p in parts if p)

    def image(self, kind: str) -> Path | None:
        """Путь к логотипу / подписи / печати, если файл существует."""
        raw = getattr(self, f"{kind}_path")
        path = Path(raw) if raw else None
        return path if path is not None and path.is_file() else None


DATA_DIR_ENV = "VEDCALC_DATA_DIR"  # другая папка данных (тесты)


def requisites_dir() -> Path:
    """Папка данных программы на этом компьютере: реквизиты, автосохранение."""
    if os.environ.get(DATA_DIR_ENV):
        return Path(os.environ[DATA_DIR_ENV])
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "vedcalc"


def load_requisites(directory: Path | None = None) -> Requisites:
    path = (directory or requisites_dir()) / "requisites.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return Requisites()
    names = {f.name for f in fields(Requisites)}
    return Requisites(**{k: str(v) for k, v in data.items() if k in names})


def save_requisites(req: Requisites, directory: Path | None = None) -> Requisites:
    """Сохраняет реквизиты; картинки копируются рядом, чтобы не зависеть от исходных файлов."""
    directory = directory or requisites_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for kind in ("logo", "signature", "stamp"):
        raw = getattr(req, f"{kind}_path")
        if not raw:
            continue
        src = Path(raw)
        target = directory / f"{kind}{src.suffix.lower()}"
        if src.is_file() and src.resolve() != target.resolve():
            for old in directory.glob(f"{kind}.*"):
                old.unlink()
            shutil.copyfile(src, target)
        if src.is_file() or target.is_file():
            setattr(req, f"{kind}_path", str(target))
    path = directory / "requisites.json"
    path.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=2), encoding="utf-8")
    return req


@dataclass(slots=True)
class OfferInput:
    offer_date: date = field(default_factory=date.today)
    client_company: str = ""
    client_person: str = ""
    subject: str = "Коммерческое предложение"
    payment_terms: str = ""
    delivery_terms: str = ""
    delivery_time: str = ""
    validity_days: int = 10
    comment: str = ""
    show_tnved: bool = False
    show_tiers: bool = False  # таблица «Цены по объёму заказа»
    style: OfferStyle = OfferStyle.NEUTRAL
    use_logo: bool = True
    use_signature: bool = False
    use_stamp: bool = False


@dataclass(frozen=True, slots=True)
class OfferLine:
    name: str
    tnved: str
    quantity: Decimal
    unit_label: str
    unit_price: Decimal  # с НДС (при УСН — без НДС, НДС не облагается)
    amount: Decimal
    vat_amount: Decimal  # НДС в сумме (0 при УСН)

    @property
    def quantity_text(self) -> str:
        return f"{fmt_number(self.quantity)} {self.unit_label}"


@dataclass(frozen=True, slots=True)
class OfferTier:
    """Строка «Цены по объёму»: только то, что видит клиент — объём, скидка, цена."""

    label: str  # «от 50 шт»
    discount: str  # «3%» или «—»
    unit_price: Decimal  # с НДС (при УСН — без НДС)


@dataclass(frozen=True, slots=True)
class Offer:
    inp: OfferInput
    requisites: Requisites
    lines: tuple[OfferLine, ...]
    vat_payer: bool
    vat_rate: Decimal
    tiers: tuple[OfferTier, ...] = ()
    tiers_item: str = ""

    @property
    def tiers_title(self) -> str:
        return f"Цена в зависимости от объёма заказа: {self.tiers_item}"

    @property
    def total(self) -> Decimal:
        return sum((x.amount for x in self.lines), ZERO)

    @property
    def vat_total(self) -> Decimal:
        return sum((x.vat_amount for x in self.lines), ZERO)

    @property
    def valid_until(self) -> date:
        return self.inp.offer_date + timedelta(days=self.inp.validity_days)

    @property
    def vat_text(self) -> str:
        if not self.vat_payer:
            return "НДС не облагается (упрощённая система налогообложения)"
        return f"В том числе НДС {fmt_rate(self.vat_rate)}: {fmt_money(self.vat_total)} ₽"

    @property
    def price_header(self) -> str:
        return "Цена за ед. с НДС, ₽" if self.vat_payer else "Цена за ед., ₽"

    def terms(self) -> list[tuple[str, str]]:
        i = self.inp
        rows = [
            ("Условия оплаты", i.payment_terms),
            ("Условия поставки", i.delivery_terms),
            ("Срок поставки", i.delivery_time),
            ("Предложение действительно до", f"{self.valid_until:%d.%m.%Y}"),
        ]
        return [(k, v) for k, v in rows if v.strip()]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """1 день, 2 дня, 5 дней."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[{1: 0, 2: 1, 3: 1, 4: 1}.get(n % 10, 2)]


def days_text(n: int) -> str:
    return f"{n} {plural(n, ('календарный день', 'календарных дня', 'календарных дней'))}"


def suggested_payment_terms(fin: Financing) -> str:
    """Подсказка из графика «стоимости денег» вкладки «Цена продажи»."""
    if fin.mode is not FinancingMode.SCHEDULE:
        return ""
    prepay = fin.buyer_prepay_pct
    rest = (
        f"в течение {days_text(fin.buyer_deferral_days)} после поставки"
        if fin.buyer_deferral_days
        else "в день поставки"
    )
    if prepay >= HUNDRED:
        return "100% предоплата"
    if prepay > 0:
        return f"Предоплата {fmt_rate(prepay)}, остаток {HUNDRED - prepay:f}% {rest}".replace(
            ".", ","
        )
    return f"Оплата {rest}"


def suggested_delivery_time(fin: Financing) -> str:
    if fin.mode is not FinancingMode.SCHEDULE:
        return ""
    days = fin.supplier_balance_day + fin.transit_days + fin.customs_days + fin.delivery_days
    return f"{days_text(days)} с даты подтверждения заказа"


def build_offer(
    report: PriceReport,
    inp: OfferInput,
    req: Requisites,
    tnved_codes: list[str | None] | None = None,
    tier_pricing: TierPricing | None = None,
) -> Offer:
    """КП из расчёта цены. tnved_codes — коды позиций (из расчёта импорта), если есть;
    tier_pricing — пороги ступенчатого прайса (в КП — при inp.show_tiers)."""
    if report.target is None:
        raise ValueError(report.target_error or "Цена не рассчитана")
    vat_payer = report.vat_payer
    rate = report.basis.sale.vat_rate if vat_payer else ZERO
    codes = tnved_codes or [None] * len(report.basis.items)
    lines = []
    for view, code in zip(report.unit_views(), codes, strict=True):
        unit_price = view.price.gross if vat_payer else view.price.net
        amount = round_money(unit_price * view.quantity)
        vat = round_money(amount * rate / (HUNDRED + rate)) if vat_payer else ZERO
        lines.append(
            OfferLine(
                view.name, code or "", view.quantity, view.unit_label, unit_price, amount, vat
            )
        )
    tiers: tuple[OfferTier, ...] = ()
    tiers_item = ""
    if inp.show_tiers and tier_pricing is not None:
        table = tier_table(report, tier_pricing)
        if table is not None:
            tiers_item = table.item_name
            tiers = tuple(
                OfferTier(
                    tier_label(r, table.unit_label),
                    f"{fmt_number(r.discount_pct)}%" if r.discount_pct else "—",
                    r.price_gross if vat_payer else r.price_net,
                )
                for r in table.rows
            )
    return Offer(inp, req, tuple(lines), vat_payer, rate, tiers, tiers_item)
