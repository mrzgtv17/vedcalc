"""Настройки по умолчанию (vedcalc/data/settings.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources

from vedcalc.core.models import AllocationMethod


@dataclass(frozen=True, slots=True)
class Settings:
    import_vat_rate: Decimal
    allowed_vat_rates: tuple[Decimal, ...]
    sale_vat_rate_osn: Decimal
    profit_tax_rate_osn: Decimal
    tax_rate_usn_income_minus_expenses: Decimal
    price_dependent_expenses_rate: Decimal
    allocation_method: AllocationMethod
    currencies: tuple[str, ...]
    cbr_url: str


@cache
def load_settings() -> Settings:
    raw = json.loads(resources.files("vedcalc.data").joinpath("settings.json").read_text("utf-8"))
    return Settings(
        import_vat_rate=Decimal(raw["import_vat_rate"]),
        allowed_vat_rates=tuple(Decimal(x) for x in raw["allowed_vat_rates"]),
        sale_vat_rate_osn=Decimal(raw["sale_vat_rate_osn"]),
        profit_tax_rate_osn=Decimal(raw["profit_tax_rate_osn"]),
        tax_rate_usn_income_minus_expenses=Decimal(raw["tax_rate_usn_income_minus_expenses"]),
        price_dependent_expenses_rate=Decimal(raw["price_dependent_expenses_rate"]),
        allocation_method=AllocationMethod(raw["allocation_method"]),
        currencies=tuple(raw["currencies"]),
        cbr_url=raw["cbr_url"],
    )
