"""HTML-отчёт. Разметка рассчитана на QTextDocument (подмножество HTML 4 / CSS 2)."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from vedcalc import __version__
from vedcalc.core.explain import fmt_money
from vedcalc.core.report import Report, Row, Section

STYLE = """
body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 10pt; color: #1d1d1f; }
h1 { font-size: 16pt; margin: 0 0 4px 0; }
h2 { font-size: 12pt; margin: 14px 0 4px 0; color: #0b5394; }
h3 { font-size: 10.5pt; margin: 10px 0 2px 0; }
.muted { color: #6e6e73; }
.warn { color: #9c4a00; background-color: #fff4e5; }
td.num { text-align: right; white-space: nowrap; }
td.text { text-align: right; }
tr.strong td { font-weight: bold; }
th { text-align: left; background-color: #eef2f7; }
"""


def _rows_table(rows: tuple[Row, ...], with_hints: bool = True) -> str:
    out = ['<table width="100%" cellspacing="0" cellpadding="3">']
    for row in rows:
        cls = ' class="strong"' if row.strong else ""
        hint = (
            f'<br><span class="muted">{escape(row.hint)}</span>' if with_hints and row.hint else ""
        )
        # Суммы не переносим; длинный текст (методика, курсы) переносим, иначе Qt
        # сжимает колонку подписей до одной буквы.
        value_cls = "num" if isinstance(row.value, Decimal) else "text"
        out.append(
            f'<tr{cls}><td width="45%">{escape(row.label)}{hint}</td>'
            f'<td class="{value_cls}" width="55%">{escape(row.text)}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def _section(section: Section) -> str:
    return f"<h2>{escape(section.title)}</h2>{_rows_table(section.rows)}"


def _items_table(report: Report) -> str:
    """Себестоимость и безубыточная цена единицы по позициям.

    Таможенные платежи по позициям — в детализации; здесь только то, что нужно для цены.
    """
    head = [
        "Позиция",
        "Кол-во",
        "Себест. ед. без НДС",
        "Себест. ед. с НДС",
        "Безубыт. цена ед. с НДС",
    ]
    widths = ["30%", "12%", "", "", ""]
    out = ['<table width="100%" cellspacing="0" cellpadding="3"><tr>']
    out += [f"<th{f' width={w}' if w else ''}>{h}</th>" for h, w in zip(head, widths, strict=True)]
    out.append("</tr>")
    for i, p in zip(report.cost.items, report.break_even.items, strict=True):
        qty = f"{i.quantity.normalize():f}".replace(".", ",")
        cells = [
            escape(i.name),
            f"{qty} {i.unit.label}",
            fmt_money(i.unit_cost_net),
            fmt_money(i.unit_cost_gross),
            fmt_money(p.unit_price_gross),
        ]
        out.append(
            "<tr>"
            + "".join(f"<td{'' if n == 0 else ' class=num'}>{c}</td>" for n, c in enumerate(cells))
            + "</tr>"
        )
    out.append("</table>")
    return "".join(out)


def _steps(report: Report) -> str:
    out = ["<h2>Детализация расчёта</h2>"]
    for title, steps in report.step_groups():
        out.append(f"<h3>{escape(title)}</h3>")
        out.append('<table width="100%" cellspacing="0" cellpadding="2">')
        for s in steps:
            out.append(
                f'<tr><td width="34%" class="muted">{escape(s.title)}</td>'
                f"<td>{escape(s.formula)}</td></tr>"
            )
        out.append("</table>")
    return "".join(out)


def render_html(report: Report, *, details: bool = True) -> str:
    s = report.shipment
    warnings = "".join(f'<p class="warn">⚠ {escape(w)}</p>' for w in report.cost.warnings)
    parts = [
        f"<html><head><meta charset='utf-8'><style>{STYLE}</style></head><body>",
        f"<h1>{escape(s.name)}</h1>",
        f'<p class="muted">Дата расчёта {s.calc_date:%d.%m.%Y} · {escape(s.delivery_mode.label)}'
        f" · {escape(report.cost.tax_regime.label)}</p>",
        warnings,
        _section(report.cost_section()),
        _items_table(report),
        _section(report.break_even_section()),
        _section(report.target_section()),
        "<h2>Курсы и методика</h2>",
        _rows_table(report.rates_rows() + report.method_rows()),
    ]
    if details:
        parts.append(_steps(report))
    parts.append(
        f'<p class="muted">Сформировано {report.generated:%d.%m.%Y %H:%M} · Калькулятор ВЭД '
        f"{__version__}. Оценка для управленческого решения; не заменяет консультацию "
        "таможенного брокера и бухгалтера.</p></body></html>"
    )
    return "".join(parts)
