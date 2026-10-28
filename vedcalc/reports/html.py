"""HTML-отчёт для вкладки «Детализация» и PDF.

Разметка рассчитана на QTextDocument (подмножество HTML 4 / CSS 2.1): без скруглений и
flex-вёрстки, поэтому «карточки» сделаны таблицами с фоном. Оформление — в духе Claude.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from html import escape

from vedcalc import __version__
from vedcalc.core.explain import fmt_money, fmt_number, fmt_pct
from vedcalc.core.report import CostReport, PriceReport, Row
from vedcalc.core.unit_economics import UnitCostView, UnitLine, UnitPriceView
from vedcalc.core.whatif import WhatIf
from vedcalc.reports.style import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_SOFT,
    BORDER,
    CODE_BG,
    MONO_FONTS,
    NEGATIVE,
    POSITIVE,
    SURFACE_ALT,
    TEXT,
    TEXT_MUTED,
    UI_FONTS,
    WARN,
    WARN_SOFT,
)


def _style(font_family: str | None) -> str:
    fonts = f"'{font_family}', {UI_FONTS}" if font_family else UI_FONTS
    return f"""
body {{ font-family: {fonts}; font-size: 10.5pt; color: {TEXT}; }}
h1 {{ font-size: 17pt; font-weight: 600; margin: 0 0 2px 0; }}
h2 {{ font-size: 13pt; font-weight: 600; margin: 22px 0 6px 0; }}
h3 {{ font-size: 10.5pt; font-weight: 600; margin: 14px 0 4px 0; color: {TEXT}; }}
p {{ margin: 0 0 6px 0; }}
.muted {{ color: {TEXT_MUTED}; }}
.small {{ font-size: 9pt; }}
.chip {{ background-color: {ACCENT_SOFT}; color: {ACCENT_HOVER}; font-size: 9pt;
         font-weight: 600; }}
.code {{ font-family: {MONO_FONTS}; font-size: 9pt; background-color: {CODE_BG}; }}
.warn {{ background-color: {WARN_SOFT}; color: {WARN}; }}
td {{ vertical-align: top; }}
td.num {{ text-align: right; white-space: nowrap; }}
td.text {{ text-align: right; }}
td.metric {{ background-color: {SURFACE_ALT}; }}
.metric-caption {{ color: {TEXT_MUTED}; font-size: 9pt; }}
.metric-value {{ font-size: 15pt; font-weight: 600; }}
.metric-accent {{ color: {ACCENT}; }}
tr.strong td {{ font-weight: 600; background-color: {SURFACE_ALT}; }}
tr.info td {{ color: {TEXT_MUTED}; }}
th {{ text-align: right; color: {TEXT_MUTED}; font-weight: 500; font-size: 9pt; }}
th.left {{ text-align: left; }}
.pos {{ color: {POSITIVE}; }}
.neg {{ color: {NEGATIVE}; }}
.rule {{ background-color: {BORDER}; }}
"""


def _rows_table(rows: tuple[Row, ...]) -> str:
    out = ['<table width="100%" cellspacing="0" cellpadding="5">']
    for row in rows:
        cls = ' class="strong"' if row.strong else ""
        hint = f'<br><span class="muted small">{escape(row.hint)}</span>' if row.hint else ""
        # Суммы не переносим; длинный текст (методика, курсы) переносим, иначе Qt
        # сжимает колонку подписей до одной буквы.
        value_cls = "num" if isinstance(row.value, Decimal) else "text"
        out.append(
            f'<tr{cls}><td width="50%">{escape(row.label)}{hint}</td>'
            f'<td class="{value_cls}" width="50%">{escape(row.text)}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def _code(text: str) -> str:
    return f'<span class="code">&nbsp;{escape(text)}&nbsp;</span>'


def _lines_table(lines: tuple[UnitLine, ...]) -> str:
    """Строки на единицу: подпись · формула с числами · сумма."""
    out = ['<table width="100%" cellspacing="0" cellpadding="5">']
    for line in lines:
        cls = ' class="strong"' if line.strong else (' class="info"' if line.informational else "")
        hint = f'<br><span class="muted small">{escape(line.hint)}</span>' if line.hint else ""
        value = escape(line.text)
        if line.label == "Чистая прибыль" and line.value:
            value = f'<span class="{"pos" if line.value > 0 else "neg"}">{value}</span>'
        out.append(
            f'<tr{cls}><td width="34%">{escape(line.label)}{hint}</td>'
            f'<td width="44%">{_code(line.formula)}</td>'
            f'<td class="num" width="22%">{value}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def _metric(caption: str, value: Decimal, sub: str, *, accent: bool = False) -> str:
    value_cls = "metric-value metric-accent" if accent else "metric-value"
    return (
        f'<td class="metric" width="33%"><span class="metric-caption">{escape(caption)}</span>'
        f'<br><span class="{value_cls}">{fmt_money(value)}</span>'
        f'<br><span class="metric-caption">{escape(sub) or "&nbsp;"}</span></td>'
    )


def _metrics_row(metrics: list[str]) -> str:
    spacer = '<td width="1%">&nbsp;</td>'
    return (
        f'<table width="100%" cellspacing="0" cellpadding="8"><tr>{spacer.join(metrics)}</tr>'
        "</table>"
    )


def _item_heading(name: str, per: str, quantity: Decimal, unit_label: str) -> str:
    return (
        f"<h2>{escape(name or 'Позиция')} "
        f'<span class="chip" style="font-size:10pt">&nbsp;{escape(per)}&nbsp;</span></h2>'
        f'<p class="muted small">В партии {fmt_number(quantity)} {escape(unit_label)}. '
        f"Все суммы ниже — {escape(per)}, в рублях.</p>"
    )


def _steps(groups, intro: str = "") -> str:
    out = []
    if intro:
        out.append(f'<p class="muted small">{escape(intro)}</p>')
    for title, steps in groups:
        out.append(f"<h3>{escape(title)}</h3>")
        out.append('<table width="100%" cellspacing="0" cellpadding="4">')
        for s in steps:
            out.append(
                f'<tr><td width="32%" class="muted">{escape(s.title)}</td>'
                f"<td>{_code(s.formula)}</td></tr>"
            )
        out.append("</table>")
    return "".join(out)


def _page(title: str, subtitle: str, body: list[str], font_family: str | None) -> str:
    return "".join(
        [
            f"<html><head><meta charset='utf-8'><style>{_style(font_family)}</style></head><body>",
            f"<h1>{escape(title)}</h1>",
            f'<p class="muted">{escape(subtitle)}</p>',
            *body,
            '<p class="muted small"><br>Сформировано ',
            f"{datetime.now():%d.%m.%Y %H:%M} · Калькулятор ВЭД {__version__}. Оценка для "
            "управленческого решения; не заменяет консультацию таможенного брокера и "
            "бухгалтера.</p></body></html>",
        ]
    )


def _warnings(warnings) -> str:
    return "".join(
        f'<table width="100%" cellpadding="8" cellspacing="0"><tr><td class="warn">'
        f"⚠ {escape(w)}</td></tr></table>"
        for w in warnings
    )


# --- себестоимость импорта ----------------------------------------------------------------


def _cost_unit_section(view: UnitCostView, osn: bool) -> str:
    metrics = [
        _metric(
            "Себестоимость без НДС" if osn else "Себестоимость (УСН)",
            view.cost_net,
            f"с НДС {fmt_money(view.cost_gross)}" if osn else "",
            accent=True,
        )
    ]
    if osn:
        metrics.append(_metric("НДС к вычету", view.deductible_vat, "возмещается из бюджета"))
    return "".join(
        [
            _item_heading(view.name, view.per, view.quantity, view.unit_label),
            _metrics_row(metrics),
            f"<h3>Себестоимость {escape(view.per)}</h3>",
            _lines_table(view.cost_lines),
        ]
    )


def load_warning(report: CostReport) -> str | None:
    load = report.load
    if load is None or load.fits:
        return None
    return (
        f"Груз не помещается в один контейнер {load.spec.title}: ограничивает {load.binding}, "
        f"нужно контейнеров: {load.containers_needed}."
    )


def render_cost_html(
    report: CostReport, *, details: bool = True, font_family: str | None = None
) -> str:
    s = report.shipment
    warnings = list(report.cost.warnings)
    if w := load_warning(report):
        warnings.append(w)
    body = [_warnings(warnings)]
    body += [_cost_unit_section(v, report.vat_payer) for v in report.unit_views()]
    batch = report.batch_section()
    body += [f"<h2>{escape(batch.title)}</h2>", _rows_table(batch.rows)]
    if report.load is not None:
        body += [
            f"<h2>Загрузка контейнера {escape(report.load.spec.title)}</h2>",
            '<p class="muted small">'
            + (
                "По укладке грузовых мест (алгоритм модуля «Контейнер»)."
                if report.load.packed
                else "По сумме объёмов и весов позиций, без учёта укладки."
            )
            + "</p>",
            _rows_table(report.load_rows()),
        ]
    if details:
        body += [
            "<h2>Расчёт по партии</h2>",
            _steps(
                report.step_groups(),
                "Таможенные платежи начисляются на позицию целиком; суммы на единицу выше "
                "получены делением на количество и округлены до копеек.",
            ),
        ]
    body += ["<h2>Курсы и методика</h2>", _rows_table(report.rates_rows() + report.method_rows())]
    subtitle = (
        f"Себестоимость импорта · дата расчёта {s.calc_date:%d.%m.%Y} · "
        f"{s.delivery_mode.label} · {report.cost.tax_regime.label}"
    )
    return _page(s.name, subtitle, body, font_family)


# --- цена продажи -------------------------------------------------------------------------


def _price_unit_section(view: UnitPriceView, report: PriceReport) -> str:
    osn = report.vat_payer
    p = view.price
    vat = (lambda x: f"с НДС {fmt_money(x)}") if osn else (lambda x: "")
    metrics = [
        _metric(
            "Себестоимость" + (" без НДС" if osn else ""),
            view.cost_net,
            f"+ стоимость денег {fmt_money(view.financing)}" if view.financing else "",
        ),
        _metric("Цена без НДС" if osn else "Цена", p.net, vat(p.gross), accent=True),
        _metric(
            "Чистая прибыль",
            p.net_profit,
            f"маржа {fmt_pct(p.gross_profit / p.net) if p.net else '—'}",
        ),
    ]
    return "".join(
        [
            _item_heading(view.name, view.per, view.quantity, view.unit_label),
            _metrics_row(metrics),
            f"<h3>{escape(report.target_title)} — {escape(view.per)}</h3>",
            _lines_table(p.lines),
        ]
    )


def _periods_table(report: PriceReport) -> str:
    rows = report.period_rows()
    if not rows:
        return ""
    out = [
        '<table width="100%" cellspacing="0" cellpadding="4"><tr>'
        '<th class="left">Дни</th><th>Длительность</th><th>Вложено</th><th>Проценты</th></tr>'
    ]
    for days, length, balance, interest in rows:
        out.append(
            f"<tr><td>{days}</td><td class=num>{length}</td><td class=num>{balance}</td>"
            f"<td class=num>{interest}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


def _whatif_section(whatif: WhatIf) -> list[str]:
    body = [
        "<h2>Что если</h2>",
        f'<p class="muted">Цена продажи не меняется: {fmt_money(whatif.price_net)} ₽ без НДС '
        f"за партию. {escape(whatif.scope_text)}</p>",
    ]
    for table in whatif.tables:
        out = [f"<h3>{escape(table.title)}</h3>",
               '<table width="100%" cellspacing="0" cellpadding="4"><tr>']  # fmt: skip
        out += [f'<td class="{"num " if c else ""}muted small">{escape(h)}</td>'
                for c, h in enumerate(whatif.heads)]  # fmt: skip
        out.append("</tr>")
        for row in table.rows:
            cost = row.unit_cost if row.unit_cost is not None else row.cost_net
            delta = (
                "—"
                if not row.profit_delta
                else (("+" if row.profit_delta > 0 else "−") + fmt_money(abs(row.profit_delta)))
            )
            sign = "pos" if row.net_profit >= 0 else "neg"
            cls = ' class="strong"' if row.scenario.is_base else ""
            out.append(
                f"<tr{cls}><td>{escape(row.scenario.label)}</td>"
                f'<td class="num">{fmt_money(cost)}</td>'
                f'<td class="num"><span class="{sign}">{fmt_money(row.net_profit)}</span></td>'
                f'<td class="num">{fmt_pct(row.margin)}</td><td class="num">{delta}</td></tr>'
            )  # fmt: skip
        out.append(f'</table><p class="muted small">{escape(table.note)}</p>')
        body += out
    body += [f'<p class="muted small">{escape(n)}</p>' for n in whatif.notes]
    return body


def render_price_html(
    report: PriceReport,
    *,
    details: bool = True,
    font_family: str | None = None,
    whatif: WhatIf | None = None,
) -> str:
    body: list[str] = []
    if report.target is None:
        body.append(_warnings([report.target_error or "Цену рассчитать не удалось"]))
    body += [_price_unit_section(v, report) for v in report.unit_views()]
    batch = report.batch_section()
    body += [f"<h2>{escape(batch.title)}</h2>", _rows_table(batch.rows)]
    if fin_rows := report.financing_rows():
        body += ["<h2>Стоимость денег</h2>", _rows_table(fin_rows), _periods_table(report)]
    if whatif is not None and whatif.tables:
        body += _whatif_section(whatif)
    if details and report.step_groups():
        body += ["<h2>Расчёт по партии</h2>", _steps(report.step_groups())]
    body += ["<h2>Методика</h2>", _rows_table(report.method_rows())]
    subtitle = f"Цена продажи · {report.target_title} · {report.basis.source_label}"
    return _page(report.title, subtitle, body, font_family)
