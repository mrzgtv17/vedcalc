"""Упаковка позиции импорта: грузовое место для укладки в контейнер."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money, fmt_number
from vedcalc.core.models import Packaging
from vedcalc.core.offer import plural
from vedcalc.core.units import Unit
from vedcalc.gui.widgets import DecimalEdit, mark_error


def summary(pk: Packaging | None, quantity: Decimal | None) -> str:
    if pk is None:
        return "Упаковка…"
    places = pk.places(quantity) if quantity and quantity > 0 else 0
    if not places:
        return "Упаковка ✓"
    return places_text(places)


def places_text(n: int) -> str:
    return f"{n} {plural(n, ('место', 'места', 'мест'))}"


class PackagingButton(QPushButton):
    """Кнопка в строке позиции: открывает окно упаковки, хранит Packaging."""

    changed = Signal()

    def __init__(self, packaging: Packaging | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.packaging = packaging
        self.quantity: Decimal | None = None
        self.unit: Unit = Unit.PCS
        self.setToolTip("Грузовое место: размеры, вес, сколько товара в месте — для укладки")
        self.clicked.connect(self.edit)
        self.refresh()

    def set_context(self, quantity: Decimal | None, unit: Unit) -> None:
        self.quantity, self.unit = quantity, unit
        self.refresh()

    def refresh(self) -> None:
        self.setText(summary(self.packaging, self.quantity))

    def edit(self) -> None:
        dialog = PackagingDialog(self.packaging, self.quantity, self.unit, self.window())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.packaging = dialog.packaging
            self.refresh()
            self.changed.emit()


class PackagingDialog(QDialog):
    def __init__(
        self,
        packaging: Packaging | None,
        quantity: Decimal | None,
        unit: Unit,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Упаковка позиции")
        self.packaging = packaging
        self.quantity, self.unit = quantity, unit
        pk = packaging
        v = QVBoxLayout(self)
        info = QLabel(
            "Грузовое место — коробка, мешок или паллета. По местам всех позиций программа "
            "укладывает груз в контейнер, как во вкладке «Контейнер»."
        )
        info.setWordWrap(True)
        info.setObjectName("muted")
        v.addWidget(info)
        form = QFormLayout()
        dims = QHBoxLayout()
        self.length = DecimalEdit(pk.length_cm if pk else None)
        self.width = DecimalEdit(pk.width_cm if pk else None)
        self.height = DecimalEdit(pk.height_cm if pk else None)
        for w in (self.length, self.width, self.height):
            dims.addWidget(w)
        form.addRow("Место Д × Ш × В, см", dims)
        self.gross = DecimalEdit(pk.gross_kg if pk else None)
        form.addRow("Вес брутто места, кг", self.gross)
        self.units = DecimalEdit(pk.units_per_place if pk else None)
        form.addRow(f"Товара в месте, {unit.label}", self.units)
        self.can_tip = QCheckBox("Можно класть на бок")
        self.can_tip.setChecked(pk.can_tip if pk else False)
        self.stackable = QCheckBox("Можно ставить в ярусы")
        self.stackable.setChecked(pk.stackable if pk else True)
        self.layers = DecimalEdit(
            Decimal(pk.max_layers) if pk and pk.max_layers else None,
            optional=True,
            placeholder="по высоте",
        )
        form.addRow(self.can_tip)
        form.addRow(self.stackable)
        form.addRow("Ярусов не больше", self.layers)
        v.addLayout(form)
        self.result = QLabel()
        self.result.setObjectName("muted")
        self.result.setWordWrap(True)
        v.addWidget(self.result)
        for w in (self.length, self.width, self.height, self.gross, self.units):
            w.textChanged.connect(self._preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Готово")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.remove_button = buttons.addButton(
            "Убрать упаковку", QDialogButtonBox.ButtonRole.ResetRole
        )
        self.remove_button.setEnabled(packaging is not None)
        self.remove_button.clicked.connect(self.remove)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._preview()

    def read(self, *, mark: bool = True) -> Packaging | None:
        """Упаковка из полей; None — поля не заполнены или с ошибками (mark — подсветить)."""

        def flag(widget: DecimalEdit, message: str | None) -> None:
            if mark:
                mark_error(widget, message)

        values, ok = [], True
        for w in (self.length, self.width, self.height, self.gross, self.units):
            try:
                value = w.value()
            except ValueError as exc:
                flag(w, str(exc))
                ok = False
                continue
            if value is None or value <= 0:
                flag(w, "Больше нуля")
                ok = False
                continue
            flag(w, None)
            values.append(value)
        try:
            layers = self.layers.value()
            flag(self.layers, None)
        except ValueError as exc:
            flag(self.layers, str(exc))
            ok, layers = False, None
        if layers is not None and (layers < 1 or layers != layers.to_integral_value()):
            flag(self.layers, "Целое число от 1")
            ok = False
        if not ok:
            return None
        return Packaging(*values, can_tip=self.can_tip.isChecked(),
                         stackable=self.stackable.isChecked(),
                         max_layers=int(layers) if layers else None)  # fmt: skip

    def _preview(self) -> None:
        pk = self.read(mark=False)
        if pk is None or not self.quantity:
            self.result.setText("")
            return
        places = pk.places(self.quantity)
        self.result.setText(
            f"{places_text(places)} · объём {fmt_money(pk.place_volume_m3 * places)} м³ · "
            f"брутто {fmt_money(pk.gross_kg * places)} кг "
            f"(на {fmt_number(self.quantity)} {self.unit.label})"
        )

    def remove(self) -> None:
        self.packaging = None
        super().accept()

    def accept(self) -> None:
        pk = self.read()
        if pk is None:
            return
        self.packaging = pk
        super().accept()
