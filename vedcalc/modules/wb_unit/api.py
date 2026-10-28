"""Тарифы и комиссии WB по API-токену продавца (раздел «Тарифы», только чтение).

- Комиссия по предметам: GET /api/v1/tariffs/commission — лимит 1 запрос в минуту.
- Тарифы коробов по складам: GET /api/v1/tariffs/box?date=ГГГГ-ММ-ДД. Коэффициент склада
  в суммах уже учтён; программа раскладывает их обратно на базовый тариф и коэффициент,
  чтобы обратная логистика (без коэффициента) считалась как раньше.

Токен хранится в Связке ключей macOS (keyring), ответы — в кэше папки программы, чтобы
программа работала и без интернета.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from vedcalc import __version__
from vedcalc.core.offer import requisites_dir

HOST = "https://common-api.wildberries.ru"
COMMISSION_URL = f"{HOST}/api/v1/tariffs/commission?locale=ru"
BOX_URL = f"{HOST}/api/v1/tariffs/box?date={{date}}"
KEYRING_SERVICE = "vedcalc.wildberries"
KEYRING_USER = "api-token"
STALE_AFTER = timedelta(days=1)
HUNDRED = Decimal(100)
NO_KEYCHAIN_ENV = "VEDCALC_NO_KEYCHAIN"


class WbApiError(Exception):
    pass


# --- данные -------------------------------------------------------------------------------


def _dec(raw) -> Decimal | None:
    """«11,2» → 11.2; «-» и пусто → None."""
    if raw is None:
        return None
    text = str(raw).replace("\xa0", "").replace(" ", "").replace(",", ".")
    if text in ("", "-"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class Subject:
    """Предмет WB и комиссии по моделям продаж, %."""

    subject_id: int
    name: str
    parent: str
    fbo_pct: Decimal | None  # «Склад WB» (paidStorageKgvp)
    fbs_pct: Decimal | None  # «Маркетплейс» (kgvpMarketplace)

    @property
    def title(self) -> str:
        return f"{self.name} ({self.parent})" if self.parent else self.name


@dataclass(frozen=True, slots=True)
class Warehouse:
    """Тарифы коробов склада: суммы с учётом коэффициентов, коэффициенты в %."""

    name: str
    geo: str
    delivery_base: Decimal | None
    delivery_liter: Decimal | None
    delivery_coef: Decimal | None
    fbs_base: Decimal | None
    fbs_liter: Decimal | None
    fbs_coef: Decimal | None
    storage_base: Decimal | None
    storage_liter: Decimal | None
    storage_coef: Decimal | None

    @property
    def title(self) -> str:
        return f"{self.name} · {self.geo}" if self.geo and self.geo not in self.name else self.name

    def has_fbo(self) -> bool:
        return None not in (self.delivery_base, self.delivery_liter, self.delivery_coef)

    def has_fbs(self) -> bool:
        return None not in (self.fbs_base, self.fbs_liter, self.fbs_coef)

    def tariff_fields(self, fbs: bool) -> dict[str, Decimal]:
        """Поля вкладки «WB»: базовые тарифы (без коэффициента) и коэффициент склада."""
        base, liter, coef = (
            (self.fbs_base, self.fbs_liter, self.fbs_coef)
            if fbs
            else (self.delivery_base, self.delivery_liter, self.delivery_coef)
        )
        out: dict[str, Decimal] = {}
        if None not in (base, liter, coef) and coef > 0:
            k = coef / HUNDRED
            out |= {
                "first_liter": round(base / k, 2),
                "extra_liter": round(liter / k, 2),
                "warehouse_coef_pct": coef,
            }
        storage = (self.storage_base, self.storage_liter, self.storage_coef)
        if not fbs and None not in storage and self.storage_coef > 0:
            k = self.storage_coef / HUNDRED
            out |= {
                "storage_first_liter": round(self.storage_base / k, 4),
                "storage_per_liter_day": round(self.storage_liter / k, 4),
                "storage_coef_pct": self.storage_coef,
            }
        return out


def parse_commissions(data: dict) -> list[Subject]:
    report = data.get("report") if isinstance(data, dict) else None
    if not isinstance(report, list):
        raise WbApiError("WB вернул комиссии в неожиданном формате")
    out = []
    for row in report:
        if not isinstance(row, dict) or not row.get("subjectName"):
            continue
        out.append(
            Subject(
                int(row.get("subjectID") or 0),
                str(row["subjectName"]).strip(),
                str(row.get("parentName") or "").strip(),
                _dec(row.get("paidStorageKgvp")),
                _dec(row.get("kgvpMarketplace")),
            )
        )
    return sorted(out, key=lambda s: (s.name.lower(), s.parent.lower()))


def parse_box(data: dict) -> list[Warehouse]:
    try:
        rows = data["response"]["data"]["warehouseList"]
    except (KeyError, TypeError):
        raise WbApiError("WB вернул тарифы складов в неожиданном формате") from None
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("warehouseName"):
            continue
        out.append(
            Warehouse(
                str(row["warehouseName"]).strip(),
                str(row.get("geoName") or "").strip(),
                _dec(row.get("boxDeliveryBase")),
                _dec(row.get("boxDeliveryLiter")),
                _dec(row.get("boxDeliveryCoefExpr")),
                _dec(row.get("boxDeliveryMarketplaceBase")),
                _dec(row.get("boxDeliveryMarketplaceLiter")),
                _dec(row.get("boxDeliveryMarketplaceCoefExpr")),
                _dec(row.get("boxStorageBase")),
                _dec(row.get("boxStorageLiter")),
                _dec(row.get("boxStorageCoefExpr")),
            )
        )
    return sorted(out, key=lambda w: w.name.lower())


# --- сеть ---------------------------------------------------------------------------------

Fetch = Callable[[str, str], dict]  # (url, токен) → JSON


def http_fetch(url: str, token: str, timeout: float = 20.0) -> dict:
    import urllib.error
    import urllib.request

    from vedcalc.core.fx import _ssl_context

    request = urllib.request.Request(
        url, headers={"Authorization": token, "User-Agent": f"vedcalc/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WbApiError(_http_message(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WbApiError(f"Нет связи с API WB: {getattr(exc, 'reason', exc)}") from None
    except ValueError:
        raise WbApiError("WB вернул не JSON") from None


def _http_message(code: int) -> str:
    return {
        401: "Токен не подходит: проверьте срок действия и категорию «Тарифы» "
        "(в кабинете продавца → Настройки → Доступ к API).",
        402: "Доступ к API WB не оплачен или отключён для этого токена.",
        403: "У токена нет доступа к разделу «Тарифы».",
        429: "Слишком много запросов к WB — повторите через минуту.",
    }.get(code, f"API WB ответил ошибкой {code}.")


# --- кэш ----------------------------------------------------------------------------------


def cache_path() -> Path:
    return requisites_dir() / "wb_tariffs_cache.json"


@dataclass(slots=True)
class WbData:
    fetched_at: datetime | None = None
    subjects: tuple[Subject, ...] = ()
    warehouses: tuple[Warehouse, ...] = ()

    def stale(self, now: datetime | None = None) -> bool:
        return self.fetched_at is None or (now or datetime.now()) - self.fetched_at > STALE_AFTER

    def subject(self, title: str) -> Subject | None:
        return next((s for s in self.subjects if s.title == title), None)

    def warehouse(self, name: str) -> Warehouse | None:
        return next((w for w in self.warehouses if w.name == name), None)


def _to_json(obj) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(obj).items()}


def save_cache(data: WbData, path: Path | None = None) -> None:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": data.fetched_at.isoformat() if data.fetched_at else None,
        "subjects": [_to_json(s) for s in data.subjects],
        "warehouses": [_to_json(w) for w in data.warehouses],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_cache(path: Path | None = None) -> WbData:
    try:
        raw = json.loads((path or cache_path()).read_text("utf-8"))
        subjects = tuple(
            Subject(int(s["subject_id"]), s["name"], s["parent"], _dec(s["fbo_pct"]),
                    _dec(s["fbs_pct"]))
            for s in raw["subjects"]
        )  # fmt: skip
        names = [f.name for f in Warehouse.__dataclass_fields__.values()]
        warehouses = tuple(
            Warehouse(w["name"], w["geo"], *(_dec(w[n]) for n in names[2:]))
            for w in raw["warehouses"]
        )
        fetched = datetime.fromisoformat(raw["fetched_at"]) if raw["fetched_at"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return WbData()
    return WbData(fetched, subjects, warehouses)


def refresh(token: str, fetch: Fetch = http_fetch, today: date | None = None) -> WbData:
    """Загрузить комиссии и тарифы складов и сохранить в кэш."""
    if not token.strip():
        raise WbApiError("Не задан токен API WB")
    subjects = parse_commissions(fetch(COMMISSION_URL, token.strip()))
    day = (today or date.today()).isoformat()
    warehouses = parse_box(fetch(BOX_URL.format(date=day), token.strip()))
    data = WbData(datetime.now().replace(microsecond=0), tuple(subjects), tuple(warehouses))
    save_cache(data)
    return data


# --- токен --------------------------------------------------------------------------------


class TokenStore:
    """Токен в Связке ключей macOS. Без keyring или с VEDCALC_NO_KEYCHAIN (тесты) — в памяти."""

    def __init__(self) -> None:
        self._memory: str | None = None

    def _keyring(self):
        if os.environ.get(NO_KEYCHAIN_ENV):
            return None
        try:
            import keyring

            return keyring
        except ImportError:  # pragma: no cover
            return None

    def get(self) -> str:
        kr = self._keyring()
        if kr is None:
            return self._memory or ""
        try:
            return kr.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
        except Exception:
            return self._memory or ""

    def set(self, token: str) -> None:
        kr = self._keyring()
        self._memory = token
        if kr is None:
            return
        try:
            kr.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        except Exception as exc:
            raise WbApiError(f"Не удалось сохранить токен в Связке ключей: {exc}") from None

    def delete(self) -> None:
        self._memory = None
        kr = self._keyring()
        if kr is None:
            return
        with contextlib.suppress(Exception):  # токена и не было
            kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
