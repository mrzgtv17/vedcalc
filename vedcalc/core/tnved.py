"""Справочник ТН ВЭД ЕАЭС со ставками ввозной пошлины (вшит в программу).

Источник — бесплатная выгрузка tws.by (data/tnved_tws.json, обновляется скриптом
scripts/update_tnved.py и выходит вместе с новой версией программы). В выгрузке — код,
наименование и ставка ЕТТ; НДС, акцизов и мер нетарифного регулирования в ней нет.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from importlib import resources

from vedcalc.core.models import DutyKind, DutyRate, DutyUnit
from vedcalc.core.money import Money

DATA_FILE = "tnved_tws.json"
STALE_AFTER_DAYS = 35  # справочник старше — напоминаем обновить программу

_UNITS = {
    "кг": DutyUnit.KG,
    "т": DutyUnit.T,
    "шт": DutyUnit.PCS,
    "л": DutyUnit.L,
    "пар": DutyUnit.PAIR,
    "см3, мл": DutyUnit.CM3,
    "см3": DutyUnit.CM3,
    "м2": DutyUnit.M2,
}
_NUM = r"(\d+(?:[.,]\d+)?)"
_SPEC = rf"{_NUM} (EUR|USD) за {_NUM}(?: (\d+))? (кг|т|шт|л|пар|см3, мл|см3|м2)"
_PATTERNS = [
    (re.compile(rf"^{_NUM}%$"), DutyKind.AD_VALOREM),
    (re.compile(rf"^{_NUM}%, но не менее {_SPEC}$"), DutyKind.COMBINED),
    (re.compile(rf"^{_NUM}% плюс {_SPEC}$"), DutyKind.SUM),
    (re.compile(rf"^{_SPEC}$"), DutyKind.SPECIFIC),
]


class TariffParseError(ValueError):
    pass


def _dec(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def parse_tariff(text: str) -> DutyRate:
    """«50%, но не менее 1 EUR за 1 кг» → DutyRate. Пустая ставка → 0% (беспошлинно)."""
    text = " ".join(text.split())
    if not text:
        return DutyRate(DutyKind.AD_VALOREM, Decimal(0), source="ставка не указана")
    for pattern, kind in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = list(m.groups())
        if kind is DutyKind.AD_VALOREM:
            return DutyRate(kind, _dec(g[0]), source=text)
        percent = Decimal(0)
        if kind is not DutyKind.SPECIFIC:
            percent = _dec(g.pop(0))
        amount, currency, per, per_extra, unit = g
        per_quantity = _dec(per) * (_dec(per_extra) if per_extra else 1)
        return DutyRate(
            kind,
            percent,
            Money(_dec(amount), currency),
            _UNITS[unit],
            per_quantity,
            source=text,
        )
    raise TariffParseError(f"Не удалось разобрать ставку пошлины: «{text}»")


@dataclass(frozen=True, slots=True)
class TnvedEntry:
    code: str
    name: str
    tariff: str  # исходная строка ставки
    tws_id: str  # внутренний номер страницы на tws.by

    @property
    def duty(self) -> DutyRate:
        return parse_tariff(self.tariff)


@dataclass(frozen=True, slots=True)
class TnvedDirectory:
    actual_date: date
    source: str
    details_url: str
    entries: dict[str, TnvedEntry]

    def get(self, code: str) -> TnvedEntry | None:
        return self.entries.get(code.strip())

    def details(self, entry: TnvedEntry) -> str:
        return self.details_url.format(id=entry.tws_id) if entry.tws_id else ""

    def age_days(self, today: date | None = None) -> int:
        return ((today or date.today()) - self.actual_date).days

    def stale(self, today: date | None = None) -> bool:
        return self.age_days(today) > STALE_AFTER_DAYS

    def stale_warning(self, today: date | None = None) -> str | None:
        if not self.stale(today):
            return None
        return (
            f"Справочник ставок ТН ВЭД от {self.actual_date:%d.%m.%Y} — старше месяца. "
            "Обновите программу: Справка → Проверить обновления."
        )

    def search(self, query: str, limit: int = 200) -> list[TnvedEntry]:
        """Поиск по началу кода или по словам наименования (все слова, без регистра, ё = е)."""
        q = query.strip()
        if not q:
            return []
        digits = q.replace(" ", "")
        if digits.isdigit():
            return [e for c, e in self.entries.items() if c.startswith(digits)][:limit]
        words = [_norm(w) for w in q.split() if w]
        found = []
        for entry in self.entries.values():
            name = _norm(entry.name)
            if all(w in name for w in words):
                found.append(entry)
                if len(found) >= limit:
                    break
        return found


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


@cache
def load_directory() -> TnvedDirectory:
    raw = json.loads(resources.files("vedcalc.data").joinpath(DATA_FILE).read_text("utf-8"))
    entries = {
        code: TnvedEntry(code, name, tariff, tws_id)
        for code, (name, tariff, tws_id) in raw["codes"].items()
    }
    return TnvedDirectory(
        date.fromisoformat(raw["actual_date"]), raw["source"], raw["details_url"], entries
    )
