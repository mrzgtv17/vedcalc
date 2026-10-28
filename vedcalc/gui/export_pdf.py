"""Экспорт отчёта в PDF средствами Qt (QTextDocument → QPdfWriter)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from vedcalc.core.report import CostReport, PriceReport
from vedcalc.reports.html import render_cost_html, render_price_html


def write_pdf(report: CostReport | PriceReport, path: Path) -> None:
    font = QGuiApplication.font().family()
    if isinstance(report, PriceReport):
        html, title = render_price_html(report, font_family=font), report.title
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
