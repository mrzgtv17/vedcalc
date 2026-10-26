"""Курсы валют ЦБ РФ: загрузка XML, локальный кэш, ручное переопределение.

Допущение методики: все курсы берутся на дату расчёта (см. docs/ASSUMPTIONS.md).
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from vedcalc.core.money import RUB

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp?date_req={date}"
SOURCE_CBR = "ЦБ РФ"
SOURCE_CACHE = "ЦБ РФ (из кэша)"
SOURCE_MANUAL = "вручную"


class FxError(Exception):
    """Курс получить не удалось."""


@dataclass(frozen=True, slots=True)
class Rate:
    """Курс: value рублей за nominal единиц валюты."""

    currency: str
    value: Decimal
    nominal: int = 1

    @property
    def per_unit(self) -> Decimal:
        return self.value / self.nominal


@dataclass(frozen=True, slots=True)
class RateSet:
    rate_date: date  # дата, на которую ЦБ установил курсы
    rates: dict[str, Rate]
    source: str = SOURCE_CBR
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class AppliedRate:
    """Курс, фактически применённый в расчёте, — для отчёта."""

    currency: str
    value: Decimal
    nominal: int
    rate_date: date
    source: str
    manual: bool = False

    @property
    def per_unit(self) -> Decimal:
        return self.value / self.nominal

    def to_rub(self, amount: Decimal) -> Decimal:
        return amount * self.value / self.nominal


RUB_RATE_SOURCE = "рубль"


def rub_rate(on: date) -> AppliedRate:
    return AppliedRate(RUB, Decimal(1), 1, on, RUB_RATE_SOURCE)


class RateProvider(Protocol):
    def get_rates(self, on: date) -> RateSet: ...


def parse_cbr_xml(payload: bytes) -> RateSet:
    """Разбирает ответ XML_daily.asp. Числа в нём — с запятой, у курса есть номинал."""
    root = ET.fromstring(payload)
    raw_date = root.get("Date")
    if not raw_date:
        raise FxError("В ответе ЦБ нет даты курсов")
    rate_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
    rates: dict[str, Rate] = {}
    for valute in root.iter("Valute"):
        code = (valute.findtext("CharCode") or "").strip().upper()
        value = (valute.findtext("Value") or "").strip().replace(",", ".")
        nominal = (valute.findtext("Nominal") or "1").strip()
        if code and value:
            rates[code] = Rate(code, Decimal(value), int(nominal))
    if not rates:
        raise FxError("В ответе ЦБ нет курсов")
    return RateSet(rate_date, rates)


def default_cache_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "vedcalc" / "fx_cache.json"


def _ssl_context() -> ssl.SSLContext:
    """Корневые сертификаты certifi: Python с python.org и собранный .app не видят
    системное хранилище macOS."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _http_get(url: str, timeout: float = 10.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "vedcalc/0.1"})
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.read()


class CbrRateProvider:
    """Курсы ЦБ РФ с кэшем в JSON-файле.

    Без интернета возвращает последний сохранённый курс (на ближайшую прошедшую дату)
    с предупреждением о его дате.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        fetch: Callable[[str], bytes] = _http_get,
        url_template: str = CBR_URL,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.cache_path = cache_path or default_cache_path()
        self._fetch = fetch
        self._url_template = url_template
        self._today = today

    def get_rates(self, on: date) -> RateSet:
        cache = self._load_cache()
        key = on.isoformat()
        if key in cache:
            return self._from_cache_entry(cache[key], SOURCE_CBR)
        try:
            payload = self._fetch(self._url_template.format(date=on.strftime("%d/%m/%Y")))
            rate_set = parse_cbr_xml(payload)
        except Exception as exc:  # сеть, таймаут, битый XML — всё ведёт к кэшу
            return self._fallback(cache, on, exc)
        # Курсы на будущую дату ЦБ ещё может поменять — их не кэшируем.
        if on <= self._today():
            cache[key] = self._to_cache_entry(rate_set)
            self._save_cache(cache)
        return rate_set

    def _fallback(self, cache: dict, on: date, exc: Exception) -> RateSet:
        candidates = sorted(k for k in cache if k <= on.isoformat()) or sorted(cache)
        if not candidates:
            raise FxError(f"Нет связи с ЦБ РФ и нет сохранённых курсов: {exc}") from exc
        cached = self._from_cache_entry(cache[candidates[-1]], SOURCE_CACHE)
        warning = (
            "Нет связи с ЦБ РФ. Использованы сохранённые курсы на "
            f"{cached.rate_date:%d.%m.%Y}, а не на {on:%d.%m.%Y}."
        )
        return RateSet(cached.rate_date, cached.rates, SOURCE_CACHE, warning)

    @staticmethod
    def _to_cache_entry(rate_set: RateSet) -> dict:
        return {
            "rate_date": rate_set.rate_date.isoformat(),
            "rates": {
                code: {"value": str(r.value), "nominal": r.nominal}
                for code, r in rate_set.rates.items()
            },
        }

    @staticmethod
    def _from_cache_entry(entry: dict, source: str) -> RateSet:
        rates = {
            code: Rate(code, Decimal(r["value"]), int(r["nominal"]))
            for code, r in entry["rates"].items()
        }
        return RateSet(date.fromisoformat(entry["rate_date"]), rates, source)

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self, cache: dict) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError:
            pass  # кэш — оптимизация, расчёт без него не ломается


@dataclass(frozen=True, slots=True)
class ResolvedRates:
    rates: dict[str, AppliedRate]
    warnings: tuple[str, ...] = ()

    def __getitem__(self, currency: str) -> AppliedRate:
        try:
            return self.rates[currency]
        except KeyError:
            raise FxError(f"Нет курса для валюты {currency}") from None


def resolve_rates(
    currencies: Iterable[str],
    on: date,
    provider: RateProvider | None,
    manual: dict[str, Decimal] | None = None,
) -> ResolvedRates:
    """Собирает курсы для расчёта: ручные имеют приоритет, остальные — от провайдера."""
    manual = {k.upper(): v for k, v in (manual or {}).items()}
    applied: dict[str, AppliedRate] = {RUB: rub_rate(on)}
    warnings: list[str] = []
    needed = {c.upper() for c in currencies} - {RUB}
    for code in sorted(needed & manual.keys()):
        if manual[code] <= 0:
            raise FxError(f"Ручной курс {code} должен быть больше нуля")
        applied[code] = AppliedRate(code, manual[code], 1, on, SOURCE_MANUAL, manual=True)
    remaining = needed - manual.keys()
    if remaining:
        if provider is None:
            raise FxError(f"Нет курсов для {', '.join(sorted(remaining))}")
        rate_set = provider.get_rates(on)
        if rate_set.warning:
            warnings.append(rate_set.warning)
        for code in sorted(remaining):
            if code not in rate_set.rates:
                raise FxError(f"ЦБ РФ не устанавливает курс {code} на {on:%d.%m.%Y}")
            r = rate_set.rates[code]
            applied[code] = AppliedRate(
                code, r.value, r.nominal, rate_set.rate_date, rate_set.source
            )
    if any(a.manual for a in applied.values()):
        warnings.append("Курс изменён вручную")
    return ResolvedRates(applied, tuple(warnings))
