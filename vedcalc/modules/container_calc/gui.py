"""Вкладка «Контейнер»: загрузка грузовых мест в морской контейнер (отдельно от импорта)."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.containers import ContainerSpec, load_containers
from vedcalc.core.explain import fmt_money, fmt_pct
from vedcalc.gui.form import Column, RowTable
from vedcalc.gui.theme import ERROR_BOX, WARN_BOX
from vedcalc.gui.widgets import CenteredCheck, DecimalEdit, EnumCombo, mark_error
from vedcalc.modules.container_calc.packing import (
    MAX_KINDS,
    CargoKind,
    LoadedContainer,
    PackingInputError,
    PackingResult,
    best_choice,
    compare,
    pack,
)
from vedcalc.reports.style import BORDER_STRONG, NEGATIVE, SURFACE_ALT, TEXT_MUTED

RECALC_DELAY_MS = 250
# Мягкие цвета видов мест (до 20; повторяются по кругу)
PALETTE = [
    "#E9B9A4", "#B8D4C2", "#B9C8E6", "#E8D39B", "#D3B9E0", "#A9D6D8", "#F0C4C9", "#C9D6A3",
    "#D9C2A7", "#A7BFD9", "#E3A9A9", "#B5D9B0", "#CDB4D9", "#D9D2A7", "#A7D9C8", "#E0BFA0",
    "#B0B9E0", "#D9A7C2", "#BFD9A7", "#A7C2D9",
]  # fmt: skip

COLUMNS = [
    Column("name", "Грузовое место", 160),
    Column("length", "Длина, см", 80),
    Column("width", "Ширина, см", 80),
    Column("height", "Высота, см", 80),
    Column("weight", "Вес 1 места, кг", 100, "Вес брутто одного места"),
    Column("quantity", "Кол-во", 70),
    Column("tip", "Кантовать", 72, "Можно класть на бок — любая ориентация"),
    Column("stack", "Штабель", 64, "Можно ставить друг на друга"),
    Column("layers", "Ярусов ≤", 70, "Не более N ярусов, выше ничего не ставят. Пусто — по высоте"),
]


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _bar(share: Decimal, text: str) -> QProgressBar:
    bar = QProgressBar()
    bar.setObjectName("loadBar")
    bar.setRange(0, 100)
    bar.setValue(min(100, int(share * 100)))
    bar.setFormat(f"{fmt_pct(share, 0)} · {text}")
    bar.setProperty("over", share > 1)
    return bar


class LoadDrawing(QWidget):
    """Схема раскладки: вид сверху (длина × ширина) и сбоку (длина × высота)."""

    def __init__(self, loaded: LoadedContainer, colors: dict[str, str]):
        super().__init__()
        self.loaded, self.colors = loaded, colors
        self.setMinimumHeight(260)

    def paintEvent(self, event) -> None:
        spec = self.loaded.spec
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width() - 2
        scale = w / spec.length_mm
        top_h = spec.width_mm * scale
        side_h = spec.height_mm * scale
        gap = 26
        font = QFont(self.font())
        font.setPointSize(10)
        p.setFont(font)
        p.setPen(QColor(TEXT_MUTED))
        p.drawText(QRectF(1, 0, w, 16), "Вид сверху (двери справа)")
        top = QRectF(1, 18, w, top_h)
        p.drawText(QRectF(1, 18 + top_h + 6, w, 16), "Вид сбоку")
        p.setPen(QColor(TEXT_MUTED))
        side = QRectF(1, 18 + top_h + gap, w, side_h)
        self.setMinimumHeight(int(side.bottom()) + 4)
        for frame in (top, side):
            p.setPen(QPen(QColor(BORDER_STRONG), 1))
            p.setBrush(QColor(SURFACE_ALT))
            p.drawRect(frame)
        # Ниже лежащие блоки рисуем первыми — сверху видно верхние
        for b in sorted(self.loaded.blocks, key=lambda b: (b.z_mm, b.y_mm)):
            color = QColor(self.colors.get(b.name, "#CCCCCC"))
            p.setBrush(color)
            p.setPen(QPen(color.darker(130), 0.8))
            p.drawRect(QRectF(top.x() + b.x_mm * scale, top.y() + b.y_mm * scale,
                              b.depth_mm * scale, b.width_mm * scale))  # fmt: skip
            # Вид сбоку: ближние к зрителю (y меньше) поверх дальних
        for b in sorted(self.loaded.blocks, key=lambda b: -b.y_mm):
            color = QColor(self.colors.get(b.name, "#CCCCCC"))
            p.setBrush(color)
            p.setPen(QPen(color.darker(130), 0.8))
            y = side.bottom() - (b.z_mm + b.height_mm) * scale
            p.drawRect(QRectF(side.x() + b.x_mm * scale, y, b.depth_mm * scale,
                              b.height_mm * scale))  # fmt: skip
        p.end()


class ContainerCard(QFrame):
    def __init__(self, n: int, loaded: LoadedContainer, colors: dict[str, str]):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(
            _label(f"Контейнер {n} · {loaded.spec.title} · {loaded.count} мест", "cardTitle")
        )
        spec = loaded.spec
        self.volume_bar = _bar(
            loaded.volume_share, f"{fmt_money(loaded.volume_m3)} из {fmt_money(spec.volume_m3)} м³"
        )
        self.weight_bar = _bar(
            loaded.weight_share,
            f"{fmt_money(loaded.weight_kg / 1000)} из {fmt_money(spec.max_cargo_kg / 1000)} т",
        )
        for caption, bar in (("Объём", self.volume_bar), ("Вес", self.weight_bar)):
            row = QHBoxLayout()
            lbl = QLabel(caption)
            lbl.setMinimumWidth(52)
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            v.addLayout(row)
        v.addWidget(LoadDrawing(loaded, colors))
        legend = QGridLayout()
        legend.setHorizontalSpacing(12)
        for k, (name, count) in enumerate(loaded.counts().items()):
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(f"background:{colors.get(name)}; border-radius:3px;")
            legend.addWidget(swatch, k // 2, (k % 2) * 3)
            legend.addWidget(QLabel(escape(name)), k // 2, (k % 2) * 3 + 1)
            num = QLabel(f"{count} шт.")
            num.setObjectName("muted")
            legend.addWidget(num, k // 2, (k % 2) * 3 + 2)
        v.addLayout(legend)


class _Signals(QObject):
    compared = Signal(int, object)  # номер запроса, список PackingResult или ошибка


class ContainerTab(QWidget):
    """Отдельный модуль: не связан с расчётом импорта."""

    def __init__(self, *, threaded: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.specs = load_containers()
        self.result: PackingResult | None = None
        self.comparison: list[PackingResult] | None = None
        self.threaded = threaded
        self._generation = 0
        self._signals = _Signals()
        self._signals.compared.connect(self._on_compared)

        form = QWidget()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(0, 0, 0, 0)
        box, bv = self._card("Контейнер")
        self.spec_combo = EnumCombo([(s.code, s.title) for s in self.specs.values()])
        self.spec_combo.set_value("40HC")
        bv.addWidget(self.spec_combo)
        self.spec_info = _label("", "muted")
        bv.addWidget(self.spec_info)
        fv.addWidget(box)

        box, bv = self._card("Грузовые места")
        self.table = RowTable(COLUMNS)
        # Название — фиксированной ширины: колонок много, «растягивать» его некуда
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 150)
        self.table.changed.connect(self._on_changed)
        bv.addWidget(self.table)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+ Место")
        self.add_button.clicked.connect(lambda: self.add_kind())
        buttons.addWidget(self.add_button)
        buttons.addWidget(
            _label(f"До {MAX_KINDS} видов. Размеры и вес — одного места.", "muted"), 1
        )
        bv.addLayout(buttons)
        fv.addWidget(box)
        note = _label(
            "Места ставятся поперечными «стенками» вдоль контейнера; пустоты сверху и сбоку "
            "заполняются другими видами. Результат для смешанного груза — приближённый.",
            "caption",
        )
        fv.addWidget(note)
        fv.addStretch(1)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.Shape.NoFrame)
        left.setWidget(form)
        left.setMinimumWidth(560)

        page = QWidget()
        page.setObjectName("page")
        self.results = QVBoxLayout(page)
        self.results.setContentsMargins(2, 2, 6, 2)
        self.results.setSpacing(12)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        self.results.addWidget(self.status)
        self.summary = _label("", "cardTitle")
        self.results.addWidget(self.summary)
        self.cards_host = QWidget()
        self.cards = QVBoxLayout(self.cards_host)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(12)
        self.results.addWidget(self.cards_host)
        self.compare_box, cv = self._card("Сравнение типов контейнеров")
        self.compare_grid = QGridLayout()
        cv.addLayout(self.compare_grid)
        self.results.addWidget(self.compare_box)
        self.results.addStretch(1)
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.Shape.NoFrame)
        right.setWidget(page)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([700, 620])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECALC_DELAY_MS)
        self._timer.timeout.connect(self.recalc)
        self.spec_combo.currentIndexChanged.connect(self._on_changed)

        self.add_kind(CargoKind("Коробка", 600, 400, 400, Decimal(18), 100))
        self.recalc()

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(_label(title, "cardTitle"))
        return frame, v

    # --- ввод -------------------------------------------------------------------------

    def add_kind(self, kind: CargoKind | None = None) -> None:
        if len(self.table.rows) >= MAX_KINDS:
            return
        kind = kind or CargoKind("", 0, 0, 0, Decimal(0), 1)
        cm = lambda mm: Decimal(mm) / 10 if mm else None  # noqa: E731
        name = QLineEdit(kind.name)
        name.setPlaceholderText("Название")
        self.table.append({
            "name": name,
            "length": DecimalEdit(cm(kind.length_mm)),
            "width": DecimalEdit(cm(kind.width_mm)),
            "height": DecimalEdit(cm(kind.height_mm)),
            "weight": DecimalEdit(kind.weight_kg or None),
            "quantity": DecimalEdit(Decimal(kind.quantity)),
            "tip": CenteredCheck(kind.can_tip, COLUMNS[6].tooltip),
            "stack": CenteredCheck(kind.stackable, COLUMNS[7].tooltip),
            "layers": DecimalEdit(
                Decimal(kind.max_layers) if kind.max_layers else None, optional=True
            ),
        })  # fmt: skip
        self.add_button.setEnabled(len(self.table.rows) < MAX_KINDS)

    def read_kinds(self) -> tuple[list[CargoKind], list[str]]:
        kinds, errors = [], []
        for n, row in enumerate(self.table.rows, 1):
            name = row["name"].text().strip() or f"Место {n}"
            values = {}
            for key, label in (("length", "длина"), ("width", "ширина"), ("height", "высота"),
                               ("weight", "вес"), ("quantity", "количество")):  # fmt: skip
                widget = row[key]
                try:
                    values[key] = widget.value()
                    mark_error(widget, None)
                except ValueError as exc:
                    errors.append(f"«{name}», {label}: {exc}")
                    mark_error(widget, str(exc))
            try:
                layers = row["layers"].value()
                mark_error(row["layers"], None)
            except ValueError as exc:
                errors.append(f"«{name}», ярусы: {exc}")
                mark_error(row["layers"], str(exc))
                layers = None
            if len(values) < 5:
                continue
            q = values["quantity"]
            if q != q.to_integral_value():
                errors.append(f"«{name}»: количество — целое число")
                continue
            kinds.append(
                CargoKind(
                    name,
                    int((values["length"] * 10).to_integral_value()),
                    int((values["width"] * 10).to_integral_value()),
                    int((values["height"] * 10).to_integral_value()),
                    values["weight"],
                    int(q),
                    can_tip=row["tip"].isChecked(),
                    stackable=row["stack"].isChecked(),
                    max_layers=int(layers) if layers else None,
                )
            )
        return kinds, errors

    # --- расчёт -----------------------------------------------------------------------

    def _on_changed(self, *_args) -> None:
        self.add_button.setEnabled(len(self.table.rows) < MAX_KINDS)
        self._timer.start()

    def spec(self) -> ContainerSpec:
        return self.specs[self.spec_combo.value()]

    def recalc(self) -> PackingResult | None:
        self._timer.stop()
        spec = self.spec()
        self.spec_info.setText(
            f"Внутри {spec.dims_label} · {fmt_money(spec.volume_m3)} м³ · "
            f"груз до {fmt_money(spec.max_cargo_kg / 1000)} т"
        )
        kinds, errors = self.read_kinds()
        self._generation += 1
        try:
            if errors:
                raise PackingInputError("; ".join(errors))
            self.result = pack(kinds, spec)
        except PackingInputError as exc:
            self.result = None
            self._show_status("<b>Проверьте ввод:</b><br>" + escape(str(exc)).replace("; ", "<br>"),
                              ERROR_BOX)  # fmt: skip
            self.summary.hide()
            self._replace_cards([])
            self.compare_box.hide()
            return None
        self._show_result(self.result)
        self._start_compare(kinds)
        return self.result

    def _show_status(self, html: str | None, style: str = WARN_BOX) -> None:
        self.status.setVisible(bool(html))
        if html:
            self.status.setStyleSheet(style)
            self.status.setText(html)

    def _replace_cards(self, cards: list[QWidget]) -> None:
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for c in cards:
            self.cards.addWidget(c)

    def colors(self) -> dict[str, str]:
        names = [row["name"].text().strip() or f"Место {n}" for n, row in
                 enumerate(self.table.rows, 1)]  # fmt: skip
        return {name: PALETTE[n % len(PALETTE)] for n, name in enumerate(names)}

    def _show_result(self, r: PackingResult) -> None:
        problems = list(r.problems)
        self._show_status("<br>".join(escape(p) for p in problems) if problems else None,
                          ERROR_BOX)  # fmt: skip
        need = r.containers_needed
        tail = f" · ограничивает {r.binding}" if need else ""
        self.summary.setText(
            f"Нужно контейнеров: {need} × {r.spec.title} · уложено {r.placed_pieces} из "
            f"{r.total_pieces} мест · {fmt_money(r.total_volume_m3)} м³, "
            f"{fmt_money(r.total_weight_kg / 1000)} т{tail}"
        )
        self.summary.show()
        colors = self.colors()
        self._replace_cards([ContainerCard(n, c, colors) for n, c in enumerate(r.containers, 1)])

    # --- сравнение типов (в фоне) -----------------------------------------------------

    def _start_compare(self, kinds: list[CargoKind]) -> None:
        generation = self._generation
        signals = self._signals

        class Job(QRunnable):
            def run(self) -> None:
                try:
                    signals.compared.emit(generation, compare(kinds))
                except Exception as exc:  # pragma: no cover
                    signals.compared.emit(generation, exc)

        if self.threaded:
            QThreadPool.globalInstance().start(Job())
        else:
            Job().run()

    def _on_compared(self, generation: int, results) -> None:
        if generation != self._generation or isinstance(results, Exception):
            return  # ввод уже изменился — результат устарел
        self.comparison = results
        best = best_choice(results)
        grid = self.compare_grid
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for c, head in enumerate(("Тип", "Контейнеров", "Загрузка 1-го: объём / вес", "")):
            h = QLabel(head)
            h.setObjectName("caption")
            grid.addWidget(h, 0, c)
        for n, r in enumerate(results, 1):
            first = r.containers[0] if r.containers else None
            load = (f"{fmt_pct(first.volume_share, 0)} / {fmt_pct(first.weight_share, 0)}"
                    if first else "—")  # fmt: skip
            need = str(r.containers_needed) if r.complete else f"{r.containers_needed} + не влезло"
            mark = "★ лучший" if best is not None and r.spec.code == best.spec.code else ""
            cells = [r.spec.title, need, load, mark]
            for c, text in enumerate(cells):
                lbl = QLabel(text)
                lbl.setObjectName(f"cmp:{r.spec.code}:{c}")
                if mark:
                    font = lbl.font()
                    font.setBold(True)
                    lbl.setFont(font)
                if r.spec.code == self.spec().code:
                    lbl.setStyleSheet(f"background:{SURFACE_ALT}; padding:2px 4px;")
                elif not r.complete:
                    lbl.setStyleSheet(f"color:{NEGATIVE};")
                grid.addWidget(lbl, n, c)
        self.compare_box.show()

    def best_text(self) -> str | None:
        best = best_choice(self.comparison or [])
        return best.spec.code if best else None
