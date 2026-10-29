"""Сохранение и открытие расчёта (JSON). Decimal хранится строкой — без потерь."""

from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from vedcalc.core.models import (
    AllocationMethod,
    CostKind,
    CostLine,
    DeliveryMode,
    DutyKind,
    DutyRate,
    DutyUnit,
    Financing,
    FinancingMode,
    Item,
    ManualCost,
    Packaging,
    PriceMode,
    PriceTier,
    PricingSource,
    SaleParams,
    Shipment,
    TaxRegime,
    TierPricing,
)
from vedcalc.core.money import Money
from vedcalc.core.units import Unit

FORMAT = "vedcalc.shipment"
VERSION = 1


class FileFormatError(ValueError):
    pass


def _money(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency}


def _opt(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def _dec_opt(s: str | None) -> Decimal | None:
    return None if s is None else Decimal(s)


_FIN_DECIMALS = ("annual_rate", "borrowed_share", "supplier_prepay_pct", "buyer_prepay_pct")
_FIN_INTS = (
    "loan_days",
    "supplier_balance_day",
    "transit_days",
    "customs_days",
    "delivery_days",
    "buyer_prepay_day",
    "buyer_deferral_days",
)


def _financing_to_dict(f: Financing) -> dict[str, Any]:
    data: dict[str, Any] = {"mode": f.mode.value, "loan_amount": _opt(f.loan_amount)}
    data |= {k: str(getattr(f, k)) for k in _FIN_DECIMALS}
    data |= {k: getattr(f, k) for k in _FIN_INTS}
    return data


def _financing_from_dict(d: dict | None) -> Financing:
    if not d:
        return Financing()
    f = Financing(mode=FinancingMode(d.get("mode", FinancingMode.OFF)))
    f.loan_amount = _dec_opt(d.get("loan_amount"))
    for k in _FIN_DECIMALS:
        if k in d:
            setattr(f, k, Decimal(d[k]))
    for k in _FIN_INTS:
        if k in d:
            setattr(f, k, int(d[k]))
    return f


def _manual_from_dict(d: dict | None) -> ManualCost:
    if not d:
        return ManualCost()
    return ManualCost(
        name=d.get("name", "Товар"),
        quantity=Decimal(d.get("quantity", "1")),
        unit=Unit(d.get("unit", Unit.PCS)),
        unit_cost_net=Decimal(d.get("unit_cost_net", "0")),
        unit_deductible_vat=Decimal(d.get("unit_deductible_vat", "0")),
        tax_regime=TaxRegime(d.get("tax_regime", TaxRegime.OSN)),
    )


def _tiers_to_dict(t: TierPricing) -> dict:
    return {
        "tiers": [
            {"min_qty": str(x.min_qty), "discount_pct": str(x.discount_pct)} for x in t.tiers
        ],
        "order_cost": str(t.order_cost),
        "item": t.item,
    }


def _tiers_from_dict(d: dict | None) -> TierPricing:
    if not d:
        return TierPricing()
    return TierPricing(
        tiers=[
            PriceTier(Decimal(x["min_qty"]), Decimal(x.get("discount_pct", "0")))
            for x in d.get("tiers", [])
        ],
        order_cost=Decimal(d.get("order_cost", "0")),
        item=int(d.get("item", 0)),
    )


_PACK_DECIMALS = ("length_cm", "width_cm", "height_cm", "gross_kg", "units_per_place")


def _packaging_to_dict(pk: Packaging | None) -> dict | None:
    if pk is None:
        return None
    return {k: str(getattr(pk, k)) for k in _PACK_DECIMALS} | {
        "can_tip": pk.can_tip,
        "stackable": pk.stackable,
        "max_layers": pk.max_layers,
    }


def _packaging_from_dict(d: dict | None) -> Packaging | None:
    if not d:
        return None
    return Packaging(
        *(Decimal(d[k]) for k in _PACK_DECIMALS),
        can_tip=bool(d.get("can_tip", False)),
        stackable=bool(d.get("stackable", True)),
        max_layers=int(d["max_layers"]) if d.get("max_layers") else None,
    )


def shipment_to_dict(s: Shipment) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "shipment": {
            "name": s.name,
            "calc_date": s.calc_date.isoformat(),
            "delivery_mode": s.delivery_mode.value,
            "tax_regime": s.tax_regime.value,
            "allocation": s.allocation.value,
            "items": [
                {
                    "name": i.name,
                    "tnved_code": i.tnved_code,
                    "unit_price": _money(i.unit_price),
                    "quantity": str(i.quantity),
                    "unit": i.unit.value,
                    "duty": {
                        "kind": i.duty.kind.value,
                        "percent": str(i.duty.percent),
                        "specific": _money(i.duty.specific) if i.duty.specific else None,
                        "per_quantity": str(i.duty.per_quantity),
                        "source": i.duty.source,
                        "specific_unit": i.duty.specific_unit.value
                        if i.duty.specific_unit
                        else None,
                    },
                    "vat_rate": str(i.vat_rate),
                    "excise": _money(i.excise),
                    "gross_weight_kg": _opt(i.gross_weight_kg),
                    "net_weight_kg": _opt(i.net_weight_kg),
                    "volume_m3": _opt(i.volume_m3),
                    "duty_quantity": _opt(i.duty_quantity),
                    "packaging": _packaging_to_dict(i.packaging),
                }
                for i in s.items
            ],
            "costs": [
                {
                    "name": c.name,
                    "kind": c.kind.value,
                    "amount": _money(c.amount),
                    "in_customs_value": c.in_customs_value,
                    "vat_included": c.vat_included,
                    "vat_rate": str(c.vat_rate),
                }
                for c in s.costs
            ],
            "manual_rates": {k: str(v) for k, v in s.manual_rates.items()},
            "sale": {
                "vat_rate": str(s.sale.vat_rate),
                "profit_tax_rate": str(s.sale.profit_tax_rate),
                "price_dependent_rate": str(s.sale.price_dependent_rate),
                "target_mode": s.sale.target_mode.value,
                "target_value": str(s.sale.target_value),
            },
            "financing": _financing_to_dict(s.financing),
            "pricing_source": s.pricing_source.value,
            "manual_cost": {
                "name": s.manual_cost.name,
                "quantity": str(s.manual_cost.quantity),
                "unit": s.manual_cost.unit.value,
                "unit_cost_net": str(s.manual_cost.unit_cost_net),
                "unit_deductible_vat": str(s.manual_cost.unit_deductible_vat),
                "tax_regime": s.manual_cost.tax_regime.value,
            },
            "tier_pricing": _tiers_to_dict(s.tier_pricing),
        },
    }


def _load_money(d: dict) -> Money:
    return Money(Decimal(d["amount"]), d["currency"])


def shipment_from_dict(data: dict[str, Any]) -> Shipment:
    if data.get("format") != FORMAT:
        raise FileFormatError("Это не файл расчёта «Калькулятора ВЭД»")
    if data.get("version", 0) > VERSION:
        raise FileFormatError("Файл создан более новой версией программы — обновите её")
    try:
        s = data["shipment"]
        items = []
        for i in s["items"]:
            d = i["duty"]
            items.append(
                Item(
                    name=i["name"],
                    tnved_code=i.get("tnved_code"),
                    unit_price=_load_money(i["unit_price"]),
                    quantity=Decimal(i["quantity"]),
                    unit=Unit(i["unit"]),
                    duty=DutyRate(
                        kind=DutyKind(d["kind"]),
                        percent=Decimal(d["percent"]),
                        specific=_load_money(d["specific"]) if d.get("specific") else None,
                        specific_unit=DutyUnit(d["specific_unit"])
                        if d.get("specific_unit")
                        else None,
                        per_quantity=Decimal(d.get("per_quantity", "1")),
                        source=d.get("source", ""),
                    ),
                    vat_rate=Decimal(i["vat_rate"]),
                    excise=_load_money(i["excise"]),
                    gross_weight_kg=_dec_opt(i.get("gross_weight_kg")),
                    net_weight_kg=_dec_opt(i.get("net_weight_kg")),
                    volume_m3=_dec_opt(i.get("volume_m3")),
                    duty_quantity=_dec_opt(i.get("duty_quantity")),
                    packaging=_packaging_from_dict(i.get("packaging")),
                )
            )
        costs = [
            CostLine(
                name=c["name"],
                kind=CostKind(c["kind"]),
                amount=_load_money(c["amount"]),
                in_customs_value=bool(c["in_customs_value"]),
                vat_included=bool(c["vat_included"]),
                vat_rate=Decimal(c["vat_rate"]),
            )
            for c in s["costs"]
        ]
        sale = s["sale"]
        return Shipment(
            name=s["name"],
            calc_date=date.fromisoformat(s["calc_date"]),
            delivery_mode=DeliveryMode(s["delivery_mode"]),
            tax_regime=TaxRegime(s["tax_regime"]),
            allocation=AllocationMethod(s["allocation"]),
            items=items,
            costs=costs,
            manual_rates={k: Decimal(v) for k, v in s.get("manual_rates", {}).items()},
            sale=SaleParams(
                vat_rate=Decimal(sale["vat_rate"]),
                profit_tax_rate=Decimal(sale["profit_tax_rate"]),
                price_dependent_rate=Decimal(sale["price_dependent_rate"]),
                target_mode=PriceMode(sale.get("target_mode", PriceMode.MARKUP)),
                target_value=Decimal(sale.get("target_value", "20")),
            ),
            financing=_financing_from_dict(s.get("financing")),
            pricing_source=PricingSource(s.get("pricing_source", PricingSource.IMPORT)),
            manual_cost=_manual_from_dict(s.get("manual_cost")),
            tier_pricing=_tiers_from_dict(s.get("tier_pricing")),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise FileFormatError(f"Файл расчёта повреждён: {exc}") from exc


def project_to_dict(shipment: Shipment, modules: dict[str, dict] | None = None) -> dict:
    """Файл проекта: расчёт импорта и цены + состояние вкладок-модулей («Контейнер», «WB»).

    Модули лежат в необязательном ключе "modules" — старые версии программы его не читают
    и открывают такой файл как обычный расчёт.
    """
    data = shipment_to_dict(shipment)
    if modules:
        data["modules"] = modules
    return data


def project_from_dict(data: dict[str, Any]) -> tuple[Shipment, dict[str, dict]]:
    modules = data.get("modules") or {}
    if not isinstance(modules, dict):
        raise FileFormatError("Файл расчёта повреждён: раздел modules")
    return shipment_from_dict(data), {k: v for k, v in modules.items() if isinstance(v, dict)}


def write_json(data: dict, path: Path) -> None:
    """Запись через временный файл: при сбое на диске остаётся прежняя версия."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FileFormatError(f"Файл не является JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FileFormatError("Это не файл расчёта")
    return data


def save_project(shipment: Shipment, modules: dict[str, dict] | None, path: Path) -> None:
    write_json(project_to_dict(shipment, modules), path)


def load_project(path: Path) -> tuple[Shipment, dict[str, dict]]:
    return project_from_dict(read_json(path))


def save_shipment(shipment: Shipment, path: Path) -> None:
    save_project(shipment, None, path)


def load_shipment(path: Path) -> Shipment:
    return load_project(path)[0]


def duplicate_shipment(shipment: Shipment, suffix: str = " (копия)") -> Shipment:
    copy_ = copy.deepcopy(shipment)
    copy_.name = shipment.name + suffix
    return copy_
