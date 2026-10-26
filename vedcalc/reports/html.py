"""HTML-отчёт для вкладки «Детализация» и PDF.

Разметка рассчитана на QTextDocument (подмножество HTML 4 / CSS 2.1): без скруглений и
flex-вёрстки, поэтому «карточки» сделаны таблицами с фоном. Оформление — в духе Claude.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from vedcalc import __version__
from vedcalc.core.explain import fmt_money, fmt_number
from vedcalc.core.report import Report, Row
from vedcalc.core.unit_economics import UnitLine, UnitPrice, UnitView
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


def _unit_section(view: UnitView, report: Report) -> str:
    osn = report.vat_payer
    qty = fmt_number(view.quantity)

    def vat_sub(price: UnitPrice | None, value: Decimal | None = None) -> str:
        if not osn:
            return ""
        return f"с НДС {fmt_money(value if value is not None else price.gross)}"

    metrics = [
        _metric(
            "Себестоимость без НДС" if osn else "Себестоимость (УСН)",
            view.cost_net,
            vat_sub(None, view.cost_gross),
        ),
        _metric("Безубыточная цена", view.break_even.net, vat_sub(view.break_even)),
    ]
    if view.target is not None:
        profit = f"прибыль {fmt_money(view.target.net_profit)}"
        sub = vat_sub(view.target)
        metrics.append(
            _metric(
                "Цена по сценарию",
                view.target.net,
                f"{sub} · {profit}" if sub else profit,
                accent=True,
            )
        )
    spacer = '<td width="1%">&nbsp;</td>'
    parts = [
        f"<h2>{escape(view.name or 'Позиция')} "
        f'<span class="chip" style="font-size:10pt">&nbsp;{escape(view.per)}&nbsp;</span></h2>',
        f'<p class="muted small">В партии {qty} {escape(view.unit_label)}. Все суммы ниже — '
        f"{escape(view.per)}, в рублях.</p>",
        f'<table width="100%" cellspacing="0" cellpadding="8"><tr>{spacer.join(metrics)}</tr>'
        "</table>",
        f"<h3>Себестоимость {escape(view.per)}</h3>",
        _lines_table(view.cost_lines),
        f"<h3>Безубыточная цена {escape(view.per)}</h3>",
        _lines_table(view.break_even.lines),
    ]
    if view.target is not None:
        parts += [
            f"<h3>{escape(report.target_title)} — {escape(view.per)}</h3>",
            _lines_table(view.target.lines),
        ]
    return "".join(parts)


def _steps(report: Report) -> str:
    out = [
        "<h2>Расчёт по партии</h2>",
        '<p class="muted small">Таможенные платежи начисляются на позицию целиком; суммы '
        "на единицу выше получены делением на количество и округлены до копеек.</p>",
    ]
    for title, steps in report.step_groups():
        out.append(f"<h3>{escape(title)}</h3>")
        out.append('<table width="100%" cellspacing="0" cellpadding="4">')
        for s in steps:
            out.append(
                f'<tr><td width="32%" class="muted">{escape(s.title)}</td>'
                f"<td>{_code(s.formula)}</td></tr>"
            )
        out.append("</table>")
    return "".join(out)


def render_html(report: Report, *, details: bool = True, font_family: str | None = None) -> str:
    s = report.shipment
    warnings = "".join(
        f'<table width="100%" cellpadding="8" cellspacing="0"><tr><td class="warn">'
        f"⚠ {escape(w)}</td></tr></table>"
        for w in report.cost.warnings
    )
    parts = [
        f"<html><head><meta charset='utf-8'><style>{_style(font_family)}</style></head><body>",
        f"<h1>{escape(s.name)}</h1>",
        f'<p class="muted">Дата расчёта {s.calc_date:%d.%m.%Y} · {escape(s.delivery_mode.label)}'
        f" · {escape(report.cost.tax_regime.label)}</p>",
        warnings,
    ]
    parts += [_unit_section(view, report) for view in report.unit_views()]
    batch = report.batch_section()
    parts += [f"<h2>{escape(batch.title)}</h2>", _rows_table(batch.rows)]
    if details:
        parts.append(_steps(report))
    parts += [
        "<h2>Курсы и методика</h2>",
        _rows_table(report.rates_rows() + report.method_rows()),
        f'<p class="muted small"><br>Сформировано {report.generated:%d.%m.%Y %H:%M} · '
        f"Калькулятор ВЭД {__version__}. Оценка для управленческого решения; не заменяет "
        "консультацию таможенного брокера и бухгалтера.</p></body></html>",
    ]
    return "".join(parts)
