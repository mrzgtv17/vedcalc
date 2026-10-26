"""Экспорт отчёта в XLSX (openpyxl). Суммы пишутся числами, а не текстом."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from vedcalc.core.report import Report, Row

MONEY = "#,##0.00"
HEADER_FILL = PatternFill("solid", fgColor="EEF2F7")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)
SECTION = Font(bold=True, size=12, color="0B5394")


def _write_rows(ws: Worksheet, start: int, title: str, rows: tuple[Row, ...]) -> int:
    ws.cell(start, 1, title).font = SECTION
    r = start + 1
    for row in rows:
        ws.cell(r, 1, row.label)
        cell = ws.cell(r, 2, row.value if isinstance(row.value, Decimal) else row.text)
        if isinstance(row.value, Decimal):
            cell.number_format = MONEY
        else:
            cell.alignment = Alignment(horizontal="right")
        if row.hint:
            ws.cell(r, 3, row.hint).font = Font(color="6E6E73")
        if row.strong:
            ws.cell(r, 1).font = BOLD
            cell.font = BOLD
        r += 1
    return r + 1


def _write_unit_sheet(ws: Worksheet, report: Report) -> None:
    """Первый лист — расчёт на 1 шт / кг / т по каждой позиции."""
    s = report.shipment
    ws.cell(1, 1, s.name).font = TITLE
    ws.cell(2, 1, f"Дата расчёта {s.calc_date:%d.%m.%Y} · {report.cost.tax_regime.label}")
    r = 4
    for w in report.cost.warnings:
        ws.cell(r, 1, f"⚠ {w}").font = Font(color="9C4A00")
        r += 1
    r += 1 if report.cost.warnings else 0
    for view in report.unit_views():
        ws.cell(r, 1, f"{view.name} — {view.per}").font = SECTION
        r += 1
        for c, h in enumerate(("Себестоимость", "Сумма, руб.", "Формула"), 1):
            cell = ws.cell(r, c, h)
            cell.font = BOLD
            cell.fill = HEADER_FILL
        r += 1
        for line in view.cost_lines:
            ws.cell(r, 1, line.label)
            value = ws.cell(r, 2, line.value)
            value.number_format = MONEY
            ws.cell(r, 3, line.formula).font = Font(color="76746D")
            if line.strong:
                ws.cell(r, 1).font = BOLD
                value.font = BOLD
            r += 1
        r += 1
        columns = [("Безубыточная", view.break_even)]
        if view.target is not None:
            columns.append((report.target_title, view.target))
        heads = ["Цена и прибыль " + view.per] + [title for title, _ in columns]
        for c, h in enumerate(heads, 1):
            cell = ws.cell(r, c, h)
            cell.font = BOLD
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
        for line in view.break_even.lines:
            ws.cell(r, 1, line.label).font = BOLD if line.strong else Font()
            for c, (_, price) in enumerate(columns, 2):
                match = {x.label: x for x in price.lines}.get(line.label)
                if match is not None:
                    cell = ws.cell(r, c, match.value)
                    cell.number_format = MONEY
                    if match.strong:
                        cell.font = BOLD
            r += 1
        r += 2
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 62
    ws.column_dimensions["D"].width = 22


def write_xlsx(report: Report, path: Path) -> None:
    wb = Workbook()
    s = report.shipment

    _write_unit_sheet(wb.active, report)
    wb.active.title = "За единицу"

    ws = wb.create_sheet("Итоги")
    ws.cell(1, 1, s.name).font = TITLE
    ws.cell(
        2,
        1,
        f"Дата расчёта {s.calc_date:%d.%m.%Y} · {s.delivery_mode.label} · "
        f"{report.cost.tax_regime.label}",
    )
    r = 4
    for w in report.cost.warnings:
        ws.cell(r, 1, f"⚠ {w}").font = Font(color="9C4A00")
        r += 1
    r += 1 if report.cost.warnings else 0
    for section in (
        report.batch_section(),
        report.cost_section(),
        report.break_even_section(),
        report.target_section(),
    ):
        r = _write_rows(ws, r, section.title, section.rows)
    r = _write_rows(ws, r, "Курсы и методика", report.rates_rows() + report.method_rows())
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 60

    items = wb.create_sheet("Позиции")
    head = [
        "Позиция",
        "Количество",
        "Ед.",
        "Товар, руб.",
        "Расходы в ТС",
        "Прочие расходы",
        "Таможенная стоимость",
        "Таможенный сбор",
        "Пошлина",
        "Акциз",
        "НДС при ввозе",
        "НДС по услугам",
        "Себестоимость без НДС",
        "НДС к вычету",
        "Себестоимость ед. без НДС",
        "Себестоимость ед. с НДС",
        "Безубыточная цена ед. без НДС",
        "Безубыточная цена ед. с НДС",
    ]
    for c, h in enumerate(head, 1):
        cell = items.cell(1, c, h)
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        items.column_dimensions[cell.column_letter].width = 16
    items.column_dimensions["A"].width = 30
    for n, (i, p) in enumerate(zip(report.cost.items, report.break_even.items, strict=True), 2):
        values = [
            i.name,
            i.quantity,
            i.unit.label,
            i.goods,
            i.costs_in_cv,
            i.costs_other,
            i.customs_value,
            i.customs_fee,
            i.duty,
            i.excise,
            i.import_vat,
            i.services_vat,
            i.cost_net,
            i.deductible_vat,
            i.unit_cost_net,
            i.unit_cost_gross,
            p.unit_price_net,
            p.unit_price_gross,
        ]
        for c, v in enumerate(values, 1):
            cell = items.cell(n, c, v)
            if c >= 4:
                cell.number_format = MONEY
    items.freeze_panes = "B2"

    steps = wb.create_sheet("Детализация")
    steps.column_dimensions["A"].width = 44
    steps.column_dimensions["B"].width = 110
    r = 1
    for title, group in report.step_groups():
        steps.cell(r, 1, title).font = SECTION
        r += 1
        for st in group:
            steps.cell(r, 1, st.title)
            steps.cell(r, 2, st.formula)
            r += 1
        r += 1

    wb.save(path)
