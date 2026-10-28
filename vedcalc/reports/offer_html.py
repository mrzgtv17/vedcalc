"""Вёрстка КП в HTML для QTextDocument (предпросмотр и PDF). Три стиля оформления."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from vedcalc.core.explain import fmt_money
from vedcalc.core.offer import Offer, OfferStyle
from vedcalc.reports import style as app


@dataclass(frozen=True, slots=True)
class Palette:
    font: str
    heading_font: str
    text: str
    muted: str
    accent: str  # заголовки, линия под шапкой
    head_bg: str  # шапка таблицы
    head_text: str
    zebra: str  # чередование строк
    total_bg: str
    rule: str


PALETTES = {
    OfferStyle.PROGRAM: Palette(
        font=app.UI_FONTS, heading_font=app.UI_FONTS, text=app.TEXT, muted=app.TEXT_MUTED,
        accent=app.ACCENT, head_bg=app.ACCENT_SOFT, head_text=app.ACCENT_HOVER,
        zebra=app.BG, total_bg=app.SURFACE_ALT, rule=app.ACCENT,
    ),
    OfferStyle.NEUTRAL: Palette(
        font="'Helvetica Neue', Arial, sans-serif", heading_font="'Helvetica Neue', Arial",
        text="#1A1F27", muted="#5F6B7A", accent="#1F3A5F", head_bg="#1F3A5F",
        head_text="#FFFFFF", zebra="#F4F6F9", total_bg="#E8EDF4", rule="#1F3A5F",
    ),
    OfferStyle.MONO: Palette(
        font="'Times New Roman', Georgia, serif", heading_font="'Times New Roman', Georgia",
        text="#000000", muted="#555555", accent="#000000", head_bg="#FFFFFF",
        head_text="#000000", zebra="#FFFFFF", total_bg="#FFFFFF", rule="#000000",
    ),
}  # fmt: skip


def _css(p: Palette, style: OfferStyle) -> str:
    bordered = style is OfferStyle.MONO
    cell_border = "border: 1px solid #000000;" if bordered else ""
    return f"""
body {{ font-family: {p.font}; font-size: 10.5pt; color: {p.text}; }}
h1 {{ font-family: {p.heading_font}; font-size: 18pt; font-weight: 600; color: {p.accent};
      margin: 14px 0 0 0; }}
.muted {{ color: {p.muted}; }}
.small {{ font-size: 8.5pt; }}
.company {{ font-size: 13pt; font-weight: 600; }}
table.items {{ border-collapse: collapse; {"border: 1px solid #000000;" if bordered else ""} }}
table.items th {{ background-color: {p.head_bg}; color: {p.head_text}; font-weight: 600;
                  font-size: 9pt; text-align: left; {cell_border} }}
table.items td {{ {cell_border} }}
tr.zebra td {{ background-color: {p.zebra}; }}
tr.total td {{ background-color: {p.total_bg}; font-weight: 600; }}
td.num, th.num {{ text-align: right; white-space: nowrap; }}
.terms td {{ padding: 3px 0; }}
"""


def _img(path, width: int) -> str:
    return f'<img src="{path.as_uri()}" width="{width}">' if path else ""


def render_offer_html(offer: Offer) -> str:
    inp, req = offer.inp, offer.requisites
    p = PALETTES[inp.style]
    logo = req.image("logo") if inp.use_logo else None

    company = escape(req.company or "Ваша компания")
    details = "<br>".join(escape(x) for x in (req.tax_ids, req.address, req.contacts) if x)
    header = (
        '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
        + (f'<td width="32%" valign="middle">{_img(logo, 150)}</td>' if logo else "")
        + f'<td valign="middle" align="{"right" if logo else "left"}">'
        f'<span class="company">{company}</span><br>'
        f'<span class="muted small">{details}</span></td></tr></table>'
        f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="background-color:{p.rule}; font-size:1pt">&nbsp;</td></tr></table>'
    )

    to = ", ".join(escape(x) for x in (inp.client_company, inp.client_person) if x.strip())
    title = (
        f"<h1>{escape(inp.subject or 'Коммерческое предложение')}</h1>"
        f'<p class="muted">от {inp.offer_date:%d.%m.%Y}'
        + (f" · для: <b>{to}</b>" if to else "")
        + "</p><p>Предлагаем к поставке следующие товары:</p>"
    )

    heads = ["№", "Наименование"]
    if inp.show_tnved:
        heads.append("Код ТН ВЭД")
    heads += ["Кол-во", offer.price_header, "Сумма, ₽"]
    num_cols = {len(heads) - 3, len(heads) - 2, len(heads) - 1}
    rows = ['<table class="items" width="100%" cellspacing="0" cellpadding="6"><tr>']
    widths = {0: ' width="4%"', 1: ' width="40%"'}  # наименованию — больше места
    rows += [
        f"<th{widths.get(n, '')}{' class=num' if n in num_cols else ''}>{escape(h)}</th>"
        for n, h in enumerate(heads)
    ]
    rows.append("</tr>")
    for n, line in enumerate(offer.lines, 1):
        cells = [str(n), escape(line.name)]
        if inp.show_tnved:
            cells.append(escape(line.tnved or "—"))
        cells += [escape(line.quantity_text), fmt_money(line.unit_price), fmt_money(line.amount)]
        zebra = ' class="zebra"' if n % 2 == 0 else ""
        rows.append(
            f"<tr{zebra}>"
            + "".join(
                f"<td{' class=num' if k in num_cols else ''}>{c}</td>" for k, c in enumerate(cells)
            )
            + "</tr>"
        )
    span = len(heads) - 1
    rows.append(
        f'<tr class="total"><td colspan="{span}">Итого</td>'
        f'<td class="num">{fmt_money(offer.total)}</td></tr>'
    )
    if offer.vat_payer:
        rows.append(
            f'<tr><td colspan="{span}">В том числе НДС {offer.vat_rate.normalize():f}%</td>'
            f'<td class="num">{fmt_money(offer.vat_total)}</td></tr>'
        )
    else:
        rows.append(
            f'<tr><td colspan="{span + 1}" class="muted">{escape(offer.vat_text)}</td></tr>'
        )
    rows.append("</table>")

    terms = offer.terms()
    terms_html = ""
    if terms:
        body = "".join(
            f'<tr><td width="34%" class="muted">{escape(k)}</td><td>{escape(v)}</td></tr>'
            for k, v in terms
        )
        terms_html = (
            '<p></p><table class="terms" width="100%" cellspacing="0" cellpadding="0">'
            f"{body}</table>"
        )
    comment = (
        f"<p>{escape(inp.comment).replace(chr(10), '<br>')}</p>" if inp.comment.strip() else ""
    )

    sign_img = _img(req.image("signature"), 110) if inp.use_signature else ""
    stamp_img = _img(req.image("stamp"), 120) if inp.use_stamp else ""
    signer = escape(req.signer_name or "")
    position = escape(req.signer_position or "")
    # Печать и подпись — в одной средней ячейке (наложить картинки QTextDocument не умеет)
    signature = (
        '<p></p><table width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td width="38%" valign="bottom">{position}<br>{company}</td>'
        f'<td width="37%" valign="bottom" align="center">{stamp_img}&nbsp;{sign_img}</td>'
        f'<td width="25%" valign="bottom" align="right">{signer}</td>'
        "</tr></table>"
        if signer or position
        else ""
    )
    bank = (
        f'<p class="muted small"><br>Банковские реквизиты: {escape(req.bank)}</p>'
        if req.bank
        else ""
    )
    return (
        f"<html><head><meta charset='utf-8'><style>{_css(p, inp.style)}</style></head><body>"
        + header
        + title
        + "".join(rows)
        + terms_html
        + comment
        + signature
        + bank
        + "</body></html>"
    )
