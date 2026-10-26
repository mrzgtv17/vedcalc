"""Экспорт отчёта в PDF средствами Qt (QTextDocument → QPdfWriter)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from vedcalc.core.report import Report
from vedcalc.reports.html import render_html


def write_pdf(report: Report, path: Path) -> None:
    writer = QPdfWriter(str(path))
    writer.setTitle(report.shipment.name)
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
    doc.setHtml(render_html(report, font_family=QGuiApplication.font().family()))
    doc.print_(writer)
