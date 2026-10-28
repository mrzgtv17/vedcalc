"""Окна «Коммерческое предложение» и «Мои реквизиты»."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.offer import (
    Offer,
    OfferInput,
    OfferStyle,
    Requisites,
    build_offer,
    load_requisites,
    save_requisites,
    suggested_delivery_time,
    suggested_payment_terms,
)
from vedcalc.core.report import PriceReport
from vedcalc.gui.prefs import app_settings
from vedcalc.gui.widgets import EnumCombo

IMAGE_FILTER = "Изображения (*.png *.jpg *.jpeg)"
DELIVERY_TERMS = (
    "Доставка до склада покупателя",
    "Самовывоз со склада продавца",
    "Доставка транспортной компанией до терминала в городе покупателя",
)

# Поля реквизитов: атрибут, подпись
REQUISITE_FIELDS = (
    ("company", "Компания / ИП"),
    ("inn", "ИНН"),
    ("kpp", "КПП"),
    ("ogrn", "ОГРН / ОГРНИП"),
    ("address", "Адрес"),
    ("phone", "Телефон"),
    ("email", "E-mail"),
    ("website", "Сайт"),
    ("bank_name", "Банк"),
    ("bik", "БИК"),
    ("account", "Расчётный счёт"),
    ("corr_account", "Корр. счёт"),
    ("signer_position", "Должность подписанта"),
    ("signer_name", "ФИО подписанта"),
)
IMAGES = (
    ("logo", "Логотип", "PNG или JPG, лучше на прозрачном фоне"),
    ("signature", "Подпись", "Скан подписи на прозрачном или белом фоне"),
    ("stamp", "Печать", "Скан печати на прозрачном или белом фоне"),
)


class ImagePicker(QWidget):
    """Путь к картинке: «Выбрать…» и «Убрать»."""

    def __init__(self, path: str, hint: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.path = path
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setObjectName("muted")
        self.label.setToolTip(hint)
        choose = QPushButton("Выбрать…")
        choose.clicked.connect(self.choose)
        self.clear_button = QPushButton("Убрать")
        self.clear_button.clicked.connect(lambda: self.set_path(""))
        h.addWidget(self.label, 1)
        h.addWidget(choose)
        h.addWidget(self.clear_button)
        self.set_path(path)

    def set_path(self, path: str) -> None:
        self.path = path
        self.label.setText(Path(path).name if path else "не выбрано")
        self.clear_button.setEnabled(bool(path))

    def choose(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Выберите изображение", "", IMAGE_FILTER)
        if name:
            self.set_path(name)


class RequisitesDialog(QDialog):
    """Реквизиты продавца — сохраняются на этом Mac и подставляются во все КП."""

    def __init__(self, directory: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Мои реквизиты")
        self.resize(560, 640)
        self.directory = directory
        self.requisites = load_requisites(directory)
        v = QVBoxLayout(self)
        note = QLabel("Реквизиты хранятся на этом Mac и подставляются во все КП.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.edits: dict[str, QLineEdit] = {}
        for name, label in REQUISITE_FIELDS:
            edit = QLineEdit(getattr(self.requisites, name))
            self.edits[name] = edit
            form.addRow(label, edit)
        self.images: dict[str, ImagePicker] = {}
        for kind, label, hint in IMAGES:
            picker = ImagePicker(getattr(self.requisites, f"{kind}_path"), hint)
            self.images[kind] = picker
            form.addRow(label, picker)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        v.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def accept(self) -> None:
        req = Requisites(**{n: e.text().strip() for n, e in self.edits.items()})
        for kind, picker in self.images.items():
            setattr(req, f"{kind}_path", picker.path)
        try:
            self.requisites = save_requisites(req, self.directory)
        except OSError as exc:
            QMessageBox.warning(self, "Мои реквизиты", f"Не удалось сохранить:\n{exc}")
            return
        super().accept()


class OfferDialog(QDialog):
    """КП для клиента: условия, оформление, живой предпросмотр, PDF и DOCX.

    Себестоимость, наценка и прибыль в КП не попадают — только цены и условия.
    """

    def __init__(
        self,
        report: PriceReport,
        *,
        base_name: str = "",
        directory: Path | None = None,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Коммерческое предложение")
        self.resize(1240, 820)
        self.report = report
        self.base_name = base_name or report.title
        self.directory = directory
        self.settings = settings or app_settings()
        self.requisites = load_requisites(directory)
        cost = report.cost_report
        self.tnved_codes = [i.tnved_code for i in cost.shipment.items] if cost else None
        self.offer: Offer | None = None

        form_page = QWidget()
        left = QVBoxLayout(form_page)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self._build_client_box())
        left.addWidget(self._build_terms_box())
        left.addWidget(self._build_look_box())
        left.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_page)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)

        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(scroll)
        splitter.addWidget(self.preview)
        splitter.setSizes([460, 780])

        buttons = QHBoxLayout()
        requisites = QPushButton("Мои реквизиты…")
        requisites.clicked.connect(self.edit_requisites)
        self.docx_button = QPushButton("Сохранить DOCX…")
        self.docx_button.clicked.connect(lambda: self.save("docx"))
        self.pdf_button = QPushButton("Сохранить PDF…")
        self.pdf_button.setObjectName("primary")
        self.pdf_button.setDefault(True)
        self.pdf_button.clicked.connect(lambda: self.save("pdf"))
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        buttons.addWidget(requisites)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.docx_button)
        buttons.addWidget(self.pdf_button)

        v = QVBoxLayout(self)
        v.addWidget(splitter, 1)
        v.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh)
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._timer.start)
        for w in self.findChildren(QPlainTextEdit):
            w.textChanged.connect(self._timer.start)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(self._timer.start)
        self.style_combo.currentIndexChanged.connect(self._timer.start)
        self.date_edit.dateChanged.connect(self._timer.start)
        self.validity.valueChanged.connect(self._timer.start)
        self.refresh()

    # --- форма ------------------------------------------------------------------------

    def _build_client_box(self) -> QGroupBox:
        box = QGroupBox("Клиент")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.client_company = QLineEdit()
        self.client_company.setPlaceholderText("ООО «Покупатель»")
        self.client_person = QLineEdit()
        self.client_person.setPlaceholderText("Кому: например, И. И. Иванову")
        self.subject = QLineEdit("Коммерческое предложение")
        form.addRow("Дата КП", self.date_edit)
        form.addRow("Компания", self.client_company)
        form.addRow("Контактное лицо", self.client_person)
        form.addRow("Заголовок", self.subject)
        return box

    def _build_terms_box(self) -> QGroupBox:
        box = QGroupBox("Условия")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fin = self.report.financing
        self.payment_terms = QLineEdit(suggested_payment_terms(fin))
        self.payment_terms.setPlaceholderText("Например: 100% предоплата")
        self.payment_terms.setToolTip("Подставлено из графика «Стоимость денег», можно изменить")
        self.delivery_terms = QComboBox()
        self.delivery_terms.setEditable(True)
        self.delivery_terms.addItems(DELIVERY_TERMS)
        self.delivery_terms.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.delivery_terms.setMinimumContentsLength(16)
        self.delivery_terms.setCurrentText(
            str(self.settings.value("offer/delivery_terms", DELIVERY_TERMS[0]))
        )
        self.delivery_time = QLineEdit(suggested_delivery_time(fin))
        self.delivery_time.setPlaceholderText("Например: 60 календарных дней")
        self.validity = QSpinBox()
        self.validity.setRange(1, 365)
        self.validity.setSuffix(" дн.")
        self.validity.setValue(int(self.settings.value("offer/validity_days", 10)))
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("Необязательно: гарантия, комплектация, примечания")
        self.comment.setFixedHeight(80)
        form.addRow("Оплата", self.payment_terms)
        form.addRow("Поставка", self.delivery_terms)
        form.addRow("Срок поставки", self.delivery_time)
        form.addRow("Действует", self.validity)
        form.addRow("Комментарий", self.comment)
        return box

    def _build_look_box(self) -> QGroupBox:
        box = QGroupBox("Оформление")
        form = QFormLayout(box)
        self.style_combo = EnumCombo([(s, s.label) for s in OfferStyle])
        saved = str(self.settings.value("offer/style", OfferStyle.NEUTRAL.value))
        self.style_combo.set_value(
            OfferStyle(saved) if saved in OfferStyle._value2member_map_ else OfferStyle.NEUTRAL
        )
        form.addRow("Стиль", self.style_combo)
        self.use_logo = QCheckBox("Логотип")
        self.use_signature = QCheckBox("Подпись")
        self.use_stamp = QCheckBox("Печать")
        self.show_tnved = QCheckBox("Колонка «Код ТН ВЭД»")
        self.show_tnved.setEnabled(bool(self.tnved_codes and any(self.tnved_codes)))
        for key, box_ in (
            ("use_logo", self.use_logo),
            ("use_signature", self.use_signature),
            ("use_stamp", self.use_stamp),
            ("show_tnved", self.show_tnved),
        ):
            default = key == "use_logo"
            box_.setChecked(self.settings.value(f"offer/{key}", default, type=bool))  # fmt: skip
        checks = QVBoxLayout()
        for w in (self.use_logo, self.use_signature, self.use_stamp, self.show_tnved):
            checks.addWidget(w)
        form.addRow("Показать", checks)
        self.images_hint = QLabel()
        self.images_hint.setObjectName("muted")
        self.images_hint.setWordWrap(True)
        form.addRow(self.images_hint)
        return box

    def _apply_requisites_ui(self) -> None:
        req = self.requisites
        missing = []
        for kind, check, label in (("logo", self.use_logo, "логотип"),
                                   ("signature", self.use_signature, "подпись"),
                                   ("stamp", self.use_stamp, "печать")):  # fmt: skip
            present = req.image(kind) is not None
            check.setEnabled(present)
            if not present:
                missing.append(label)
        hint = []
        if not req.company:
            hint.append("Заполните «Мои реквизиты» — компания и реквизиты попадут в шапку КП.")
        if missing:
            hint.append(f"Не загружены: {', '.join(missing)} — добавьте в «Мои реквизиты».")
        self.images_hint.setText(" ".join(hint))
        self.images_hint.setVisible(bool(hint))

    def offer_input(self) -> OfferInput:
        d = self.date_edit.date()
        return OfferInput(
            offer_date=date(d.year(), d.month(), d.day()),
            client_company=self.client_company.text().strip(),
            client_person=self.client_person.text().strip(),
            subject=self.subject.text().strip(),
            payment_terms=self.payment_terms.text().strip(),
            delivery_terms=self.delivery_terms.currentText().strip(),
            delivery_time=self.delivery_time.text().strip(),
            validity_days=self.validity.value(),
            comment=self.comment.toPlainText().strip(),
            show_tnved=self.show_tnved.isChecked() and self.show_tnved.isEnabled(),
            style=self.style_combo.value(),
            use_logo=self.use_logo.isChecked() and self.use_logo.isEnabled(),
            use_signature=self.use_signature.isChecked() and self.use_signature.isEnabled(),
            use_stamp=self.use_stamp.isChecked() and self.use_stamp.isEnabled(),
        )

    # --- действия ---------------------------------------------------------------------

    def refresh(self) -> None:
        from vedcalc.reports.offer_html import render_offer_html

        self._timer.stop()
        self._apply_requisites_ui()
        self.offer = build_offer(self.report, self.offer_input(), self.requisites,
                                 self.tnved_codes)  # fmt: skip
        bar = self.preview.verticalScrollBar()
        pos = bar.value()
        self.preview.setHtml(render_offer_html(self.offer))
        bar.setValue(pos)

    def edit_requisites(self) -> None:
        dialog = RequisitesDialog(self.directory, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.requisites = dialog.requisites
            self.refresh()

    def _remember(self) -> None:
        inp = self.offer_input()
        self.settings.setValue("offer/style", inp.style.value)
        self.settings.setValue("offer/delivery_terms", inp.delivery_terms)
        self.settings.setValue("offer/validity_days", inp.validity_days)
        for key in ("use_logo", "use_signature", "use_stamp", "show_tnved"):
            self.settings.setValue(f"offer/{key}", getattr(self, key).isChecked())

    def save(self, kind: str, path: Path | None = None) -> Path | None:
        self.refresh()
        if path is None:
            client = self.client_company.text().strip()
            suggested = f"КП {client or self.base_name} {self.offer.inp.offer_date:%d.%m.%Y}"
            suggested = "".join(c for c in suggested if c not in '/\\:*?"<>|')
            title = "Сохранить КП в PDF" if kind == "pdf" else "Сохранить КП в DOCX"
            file_filter = "PDF (*.pdf)" if kind == "pdf" else "Word (*.docx)"
            name, _ = QFileDialog.getSaveFileName(self, title, f"{suggested}.{kind}", file_filter)
            if not name:
                return None
            path = Path(name)
        try:
            if kind == "pdf":
                from vedcalc.gui.export_pdf import write_offer_pdf

                write_offer_pdf(self.offer, path)
            else:
                from vedcalc.reports.offer_docx import write_offer_docx

                write_offer_docx(self.offer, path)
        except OSError as exc:
            QMessageBox.warning(self, "Коммерческое предложение", f"Не удалось сохранить:\n{exc}")
            return None
        self._remember()
        return path
