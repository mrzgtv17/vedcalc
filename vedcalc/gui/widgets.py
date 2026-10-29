"""Общие виджеты: ввод чисел в Decimal, выпадающие списки, вывод строк отчёта."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from vedcalc.core.report import Row
from vedcalc.gui.theme import FIELD_ERROR, set_tone


def parse_decimal(text: str) -> Decimal:
    """«1 234,5» → Decimal('1234.5'). Пробелы между разрядами и запятая допустимы."""
    clean = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not clean:
        raise ValueError("пустое значение")
    try:
        value = Decimal(clean)
    except InvalidOperation:
        raise ValueError(f"«{text}» — не число") from None
    if not value.is_finite():
        raise ValueError(f"«{text}» — не число")
    return value


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def mark_error(widget: QWidget, message: str | None) -> None:
    """Подсвечивает поле с ошибкой и показывает текст ошибки во всплывающей подсказке."""
    base = widget.property("baseToolTip")
    if base is None:
        base = widget.toolTip()
        widget.setProperty("baseToolTip", base)
    set_tone(widget, FIELD_ERROR if message else None)
    widget.setToolTip(message or base)


class DecimalEdit(QLineEdit):
    """Поле ввода числа. Значение читается в Decimal, без float."""

    def __init__(
        self,
        value: Decimal | None = None,
        *,
        placeholder: str = "",
        optional: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.optional = optional
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setPlaceholderText(placeholder)
        self.set_value(value)

    def value(self) -> Decimal | None:
        """None — поле пустое и необязательное. ValueError — ввод не разобран."""
        if not self.text().strip():
            if self.optional:
                return None
            raise ValueError("заполните поле")
        return parse_decimal(self.text())

    def set_value(self, value: Decimal | None) -> None:
        self.setText(format_decimal(value))


class EnumCombo(QComboBox):
    """Выпадающий список значений (value, подпись).

    Значения хранятся в Python: Qt превращает StrEnum в userData в обычную строку.
    """

    def __init__(self, options: list[tuple[object, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self._values: list[object] = []
        for value, label in options:
            self._values.append(value)
            self.addItem(label)

    def value(self):
        return self._values[self.currentIndex()]

    def set_value(self, value) -> None:
        try:
            idx = self._values.index(value)
        except ValueError:
            # Значение не из списка (например, ставка из старого файла) — добавляем.
            self._values.append(value)
            self.addItem(str(value))
            idx = len(self._values) - 1
        self.setCurrentIndex(idx)


class VatCombo(EnumCombo):
    def __init__(self, rates: tuple[Decimal, ...], parent: QWidget | None = None):
        super().__init__([(r, f"{r}%") for r in rates], parent)


class CurrencyCombo(QComboBox):
    """Валюта: список по умолчанию + можно ввести любой код ЦБ (например, KZT)."""

    def __init__(self, currencies: tuple[str, ...], parent: QWidget | None = None):
        super().__init__(parent)
        self.setEditable(True)
        self.addItems(list(currencies))
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

    def value(self) -> str:
        code = self.currentText().strip().upper()
        if not (len(code) == 3 and code.isalpha()):
            raise ValueError("код валюты — три латинские буквы")
        return code

    def set_value(self, code: str) -> None:
        if self.findText(code) < 0:
            self.addItem(code)
        self.setCurrentText(code)


class CenteredCheck(QWidget):
    """Флажок по центру ячейки таблицы."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.box = QCheckBox()
        self.box.setChecked(checked)
        self.box.toggled.connect(self.toggled)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(tooltip)

    def isChecked(self) -> bool:
        return self.box.isChecked()

    def setChecked(self, value: bool) -> None:
        self.box.setChecked(value)


class SectionView(QGroupBox):
    """Блок результатов: строки «показатель — сумма», подсказки во всплывающих окнах."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.grid = QGridLayout(self)
        self.grid.setColumnStretch(0, 1)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(5)

    def set_rows(self, rows: tuple[Row, ...]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                widget = item.widget()
                widget.setParent(None)  # сразу убираем из дерева, а не на следующем цикле событий
                widget.deleteLater()
        for n, row in enumerate(rows):
            label = QLabel(row.label + (" ⓘ" if row.hint else ""))
            value = QLabel(row.text)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setObjectName(f"value:{row.label}")
            if row.hint:
                label.setToolTip(row.hint)
                value.setToolTip(row.hint)
            if row.strong:
                font = value.font()
                font.setBold(True)
                value.setFont(font)
                label.setFont(font)
            # Явная минимальная высота: во вложенной рамке Qt иначе сжимает строки
            # и обрезает запятые и нижние выносные элементы букв.
            for widget in (label, value):
                widget.setMinimumHeight(widget.fontMetrics().height() + 4)
            self.grid.addWidget(label, n, 0)
            self.grid.addWidget(value, n, 1)

    def value_text(self, label: str) -> str | None:
        """Для тестов: текст суммы по подписи строки."""
        widget = self.findChild(QLabel, f"value:{label}")
        return widget.text() if widget else None
