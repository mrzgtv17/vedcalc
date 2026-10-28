"""Коммерческое предложение: суммы и НДС, условия, реквизиты, HTML/PDF/DOCX, окно КП."""

import struct
import zlib
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest
from conftest import run
from test_costing import two_items

from vedcalc.core.basis import basis_from_cost, basis_from_manual
from vedcalc.core.fees import load_fee_scale
from vedcalc.core.models import (
    Financing,
    FinancingMode,
    ManualCost,
    PriceMode,
    SaleParams,
    TaxRegime,
)
from vedcalc.core.offer import (
    OfferInput,
    OfferStyle,
    Requisites,
    build_offer,
    days_text,
    load_requisites,
    save_requisites,
    suggested_delivery_time,
    suggested_payment_terms,
)
from vedcalc.core.report import build_cost_report, build_price_report
from vedcalc.core.units import Unit
from vedcalc.reports.offer_html import render_offer_html

# Внутренние показатели, которых не должно быть в КП
INTERNAL = ("себестоим", "наценк", "маржа", "прибыл", "стоимость денег", "закупочн")


def png() -> bytes:
    """Настоящий PNG 1×1 — для логотипа, подписи и печати."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(bytes(5))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def price_report(financing=None):
    s = two_items()
    s.items[0].name = "Насос центробежный"
    s.items[0].tnved_code = "8413702100"
    s.sale.target_mode = PriceMode.MARKUP
    s.sale.target_value = D(25)
    cost = run(s, load_fee_scale())
    basis = basis_from_cost(cost, s.sale)
    return build_price_report(basis, financing or Financing(), "Партия", build_cost_report(s, cost))


def manual_report(regime=TaxRegime.OSN, quantity="3", unit_cost="1000"):
    manual = ManualCost("Кресло", D(quantity), Unit.PCS, D(unit_cost), D(0), regime)
    sale = SaleParams(target_mode=PriceMode.UNIT_PRICE, target_value=D("1500"))
    if regime is TaxRegime.USN_INCOME_MINUS_EXPENSES:
        sale.vat_rate = D(0)
    return build_price_report(basis_from_manual(manual, sale), Financing(), "Кресла")


@pytest.fixture
def requisites(tmp_path):
    for name in ("logo.png", "sig.png", "stamp.png"):
        (tmp_path / name).write_bytes(png())
    return Requisites(
        company="ООО «Трейд»", inn="7701234567", kpp="770101001", ogrn="1207700123456",
        address="Москва", phone="+7 495 000-00-00", bank_name="ПАО «Банк»",
        bik="044525225", account="40702810900000012345", signer_name="И. И. Петров",
        signer_position="Генеральный директор", logo_path=str(tmp_path / "logo.png"),
        signature_path=str(tmp_path / "sig.png"), stamp_path=str(tmp_path / "stamp.png"),
    )  # fmt: skip


# --- суммы и НДС ------------------------------------------------------------------------


def test_sums_from_rounded_unit_price_and_vat_is_separated():
    report = price_report()
    offer = build_offer(report, OfferInput(), Requisites(), ["8413702100", None])
    for line, view in zip(offer.lines, report.unit_views(), strict=True):
        assert line.unit_price == view.price.gross  # цена с НДС
        assert line.amount == (line.unit_price * line.quantity).quantize(D("0.01"))
        assert line.vat_amount == (line.amount * 22 / 122).quantize(D("0.01"))
    assert offer.total == sum(x.amount for x in offer.lines)
    assert offer.vat_total == sum(x.vat_amount for x in offer.lines)
    assert offer.lines[0].tnved == "8413702100" and offer.lines[1].tnved == ""
    assert offer.vat_text.startswith("В том числе НДС 22%: ")
    assert offer.price_header == "Цена за ед. с НДС, ₽"


def test_manual_cost_unit_price():
    offer = build_offer(manual_report(), OfferInput(), Requisites())
    (line,) = offer.lines
    assert (line.unit_price, line.amount, line.vat_amount) == (D("1830.00"), D("5490.00"),
                                                                D("990.00"))  # fmt: skip
    assert line.quantity_text == "3 шт"


def test_usn_has_no_vat():
    offer = build_offer(
        manual_report(TaxRegime.USN_INCOME_MINUS_EXPENSES), OfferInput(), Requisites()
    )
    assert not offer.vat_payer and offer.vat_total == 0
    assert offer.vat_text == "НДС не облагается (упрощённая система налогообложения)"
    assert offer.price_header == "Цена за ед., ₽"
    html = render_offer_html(offer)
    assert "НДС не облагается" in html and "с НДС" not in html


def test_no_price_no_offer():
    report = replace(manual_report(), target=None)
    with pytest.raises(ValueError):
        build_offer(report, OfferInput(), Requisites())


# --- условия ----------------------------------------------------------------------------


def test_days_plural():
    assert [days_text(n) for n in (1, 2, 5, 11, 21, 22, 82, 111)] == [
        "1 календарный день", "2 календарных дня", "5 календарных дней",
        "11 календарных дней", "21 календарный день", "22 календарных дня",
        "82 календарных дня", "111 календарных дней",
    ]  # fmt: skip


def test_suggested_terms_from_schedule():
    fin = Financing(FinancingMode.SCHEDULE, buyer_prepay_pct=D(30), buyer_deferral_days=30)
    assert suggested_payment_terms(fin) == (
        "Предоплата 30%, остаток 70% в течение 30 календарных дней после поставки"
    )
    days = fin.supplier_balance_day + fin.transit_days + fin.customs_days + fin.delivery_days
    assert suggested_delivery_time(fin) == f"{days_text(days)} с даты подтверждения заказа"
    full = Financing(FinancingMode.SCHEDULE, buyer_prepay_pct=D(100))
    assert suggested_payment_terms(full) == "100% предоплата"
    post = Financing(FinancingMode.SCHEDULE, buyer_prepay_pct=D(0), buyer_deferral_days=0)
    assert suggested_payment_terms(post) == "Оплата в день поставки"
    assert suggested_payment_terms(Financing()) == suggested_delivery_time(Financing()) == ""


def test_validity_and_terms_rows():
    inp = OfferInput(offer_date=date(2026, 10, 28), validity_days=10, payment_terms="100%")
    offer = build_offer(manual_report(), inp, Requisites())
    assert offer.valid_until == date(2026, 11, 7)
    assert offer.terms() == [
        ("Условия оплаты", "100%"), ("Предложение действительно до", "07.11.2026"),
    ]  # fmt: skip


# --- реквизиты --------------------------------------------------------------------------


def test_requisites_saved_with_image_copies(tmp_path, requisites):
    store = tmp_path / "store"
    saved = save_requisites(requisites, store)
    assert saved.logo_path == str(store / "logo.png") and (store / "stamp.png").is_file()
    (tmp_path / "logo.png").unlink()  # исходник удалили — копия осталась
    loaded = load_requisites(store)
    assert loaded == saved and loaded.image("logo") == store / "logo.png"
    assert loaded.tax_ids == "ИНН 7701234567 · КПП 770101001 · ОГРН 1207700123456"
    assert "р/с 40702810900000012345" in loaded.bank
    assert load_requisites(tmp_path / "missing") == Requisites()


def test_ip_ogrnip_label():
    assert Requisites(inn="770123456789", ogrn="320774600123456").tax_ids == (
        "ИНН 770123456789 · ОГРНИП 320774600123456"
    )


# --- вёрстка ----------------------------------------------------------------------------


@pytest.mark.parametrize("style", list(OfferStyle))
def test_html_has_prices_but_no_internal_metrics(style, requisites):
    inp = OfferInput(client_company="ООО «Покупатель»", style=style, comment="Гарантия 1 год")
    html = render_offer_html(build_offer(price_report(), inp, requisites))
    for word in INTERNAL:
        assert word not in html.lower()
    assert "ООО «Покупатель»" in html and "Итого" in html and "В том числе НДС 22%" in html
    assert "Гарантия 1 год" in html and "Генеральный директор" in html


def test_html_options(requisites):
    report = price_report()
    plain = render_offer_html(build_offer(report, OfferInput(use_logo=False), requisites))
    assert "<img" not in plain and "Код ТН ВЭД" not in plain
    full = OfferInput(use_logo=True, use_signature=True, use_stamp=True, show_tnved=True)
    html = render_offer_html(build_offer(report, full, requisites, ["8413702100", None]))
    assert html.count("<img") == 3 and "8413702100" in html and "Код ТН ВЭД" in html
    # Логотип отмечен, но файла нет — просто без картинки
    requisites.logo_path = "/нет/такого.png"
    assert "<img" not in render_offer_html(build_offer(report, OfferInput(), requisites))


@pytest.mark.parametrize("style", list(OfferStyle))
def test_docx(style, requisites, tmp_path):
    docx = pytest.importorskip("docx")
    inp = OfferInput(
        client_company="ООО «Покупатель»", style=style, show_tnved=True, use_signature=True,
        use_stamp=True, payment_terms="100% предоплата",
    )  # fmt: skip
    offer = build_offer(price_report(), inp, requisites, ["8413702100", None])
    path = tmp_path / "kp.docx"
    from vedcalc.reports.offer_docx import write_offer_docx

    write_offer_docx(offer, path)
    doc = docx.Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    everything = (text + "\n" + "\n".join(cells)).lower()
    for word in INTERNAL:
        assert word not in everything
    assert "8413702100" in cells and "Итого" in cells and "100% предоплата" in cells
    assert len(doc.inline_shapes) == 3  # логотип, печать, подпись


# --- окно КП ----------------------------------------------------------------------------


def test_offer_dialog(qtbot, tmp_path, requisites):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings

    from vedcalc.gui.offer import OfferDialog, RequisitesDialog

    store = tmp_path / "store"
    save_requisites(requisites, store)
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    fin = Financing(FinancingMode.SCHEDULE, buyer_prepay_pct=D(30), buyer_deferral_days=30)
    dialog = OfferDialog(price_report(fin), directory=store, settings=settings)
    qtbot.addWidget(dialog)
    assert dialog.payment_terms.text().startswith("Предоплата 30%")
    assert dialog.show_tnved.isEnabled()  # у позиции из импорта есть код
    dialog.client_company.setText("ООО «Покупатель»")
    dialog.style_combo.set_value(OfferStyle.MONO)
    dialog.use_stamp.setChecked(True)
    dialog.refresh()
    assert "ООО «Покупатель»" in dialog.preview.toPlainText()
    assert dialog.offer.inp.style is OfferStyle.MONO and dialog.offer.inp.use_stamp

    pdf = dialog.save("pdf", tmp_path / "kp.pdf")
    docx_path = dialog.save("docx", tmp_path / "kp.docx")
    assert pdf.read_bytes()[:5] == b"%PDF-" and docx_path.stat().st_size > 1000
    assert settings.value("offer/style") == "mono"  # выбор запоминается

    again = OfferDialog(price_report(), directory=store, settings=settings)
    qtbot.addWidget(again)
    assert again.style_combo.value() is OfferStyle.MONO and again.use_stamp.isChecked()

    # Без загруженной печати галочка недоступна и в КП не попадает
    req_dialog = RequisitesDialog(store)
    qtbot.addWidget(req_dialog)
    req_dialog.images["stamp"].set_path("")
    req_dialog.accept()
    again.requisites = req_dialog.requisites
    again.refresh()
    assert not again.use_stamp.isEnabled() and not again.offer.inp.use_stamp
    assert "печать" in again.images_hint.text()


def test_offer_from_main_window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from conftest import TEST_RATES
    from PySide6.QtWidgets import QMessageBox

    from vedcalc.gui.main_window import MainWindow
    from vedcalc.gui.offer import OfferDialog

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    opened = []
    monkeypatch.setattr(OfferDialog, "exec", lambda self: opened.append(self.offer) or 0)
    w = MainWindow(TEST_RATES, threaded=False, extra_modules=[])
    qtbot.addWidget(w)
    w.open_example()
    w.tabs.setCurrentWidget(w.pricing)
    w.pricing.recalc()
    assert w.pricing.offer_button.isEnabled() and w.offer_action.isEnabled()
    w.pricing.offer_button.click()
    (offer,) = opened
    assert offer.total == D("244000.00") and offer.vat_total == D("44000.00")
