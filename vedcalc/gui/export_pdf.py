"""Экспорт отчёта в PDF средствами Qt (QTextDocument → QPdfWriter)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from vedcalc.core.report import CostReport, PriceReport
from vedcalc.core.tiers import TierTable
from vedcalc.core.whatif import WhatIf
from vedcalc.reports.html import render_cost_html, render_price_html


def write_pdf(
    report: CostReport | PriceReport,
    path: Path,
    whatif: WhatIf | None = None,
    tiers: TierTable | None = None,
) -> None:
    """whatif — таблицы «Что если», tiers — ступенчатый прайс для отчёта о цене (как на экране)."""
    font = QGuiApplication.font().family()
    if isinstance(report, PriceReport):
        html = render_price_html(report, font_family=font, whatif=whatif, tiers=tiers)
        title = report.title
    else:
        html, title = render_cost_html(report, font_family=font), report.shipment.name
    writer = QPdfWriter(str(path))
    writer.setTitle(title)
    writer.setCreator("Калькулятор ВЭД")
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(15, 15, 15, 15),
            QPageLayout.Unit.Millimeter,
        )
    )
    doc = QTextDocument()
    doc.setHtml(html)
    doc.print_(writer)


def write_offer_pdf(offer, path: Path) -> None:
    """Коммерческое предложение в PDF (A4)."""
    from vedcalc.reports.offer_html import render_offer_html

    writer = QPdfWriter(str(path))
    writer.setTitle(f"{offer.inp.subject} от {offer.inp.offer_date:%d.%m.%Y}")
    writer.setCreator(offer.requisites.company or "Калькулятор ВЭД")
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(18, 15, 15, 15),
            QPageLayout.Unit.Millimeter,
        )
    )
    doc = QTextDocument()
    doc.setHtml(render_offer_html(offer))
    doc.print_(writer)
