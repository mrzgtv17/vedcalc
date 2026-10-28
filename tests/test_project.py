"""Файл проекта: все вкладки в одном файле, последние файлы, автосохранение и восстановление."""

import json

import pytest

pytest.importorskip("PySide6")

from conftest import TEST_RATES
from PySide6.QtWidgets import QMessageBox

from vedcalc.core.io import FileFormatError, load_project, load_shipment, project_from_dict
from vedcalc.gui import main_window
from vedcalc.gui.main_window import MainWindow
from vedcalc.modules import builtin_modules
from vedcalc.modules.container_calc import module_spec as container_spec
from vedcalc.modules.wb_unit import module_spec as wb_spec
from vedcalc.modules.wb_unit.calc import Scheme, TaxMode


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(main_window, "autosave_path", lambda: tmp_path / "rec" / "auto.json")
    w = MainWindow(
        TEST_RATES, threaded=False, extra_modules=[container_spec(threaded=False), wb_spec()]
    )
    w.settings.clear()
    qtbot.addWidget(w)
    w.open_example()
    return w


def tabs(w):
    return w.module_tabs["container"], w.module_tabs["wb"]


def edit_modules(w):
    container, wb = tabs(w)
    container.spec_combo.set_value("20DC")
    container.add_kind()
    row = container.table.rows[-1]
    for key, text in (("name", "Паллета"), ("length", "120"), ("width", "80"), ("height", "100"),
                      ("weight", "300"), ("quantity", "5"), ("layers", "2")):  # fmt: skip
        row[key].setText(text)
    row["tip"].setChecked(True)
    wb.name_edit.setText("Кружка")
    wb.scheme_combo.set_value(Scheme.FBS)
    wb.tax_combo.set_value(TaxMode.INCOME_MINUS_EXPENSES)
    wb._fields["cost_price"].setText("350")
    wb._fields["commission_fbs_pct"].setText("абв")  # ошибка ввода тоже сохраняется как есть


def test_all_tabs_saved_and_restored(window, tmp_path):
    edit_modules(window)
    assert window.dirty
    path = tmp_path / "p.vedcalc.json"
    assert window._save_to(path)
    data = json.loads(path.read_text("utf-8"))
    assert data["format"] == "vedcalc.shipment" and set(data["modules"]) == {"container", "wb"}
    assert not list(tmp_path.glob("*.tmp"))  # запись через временный файл

    window.new_file()
    container, wb = tabs(window)
    assert container.spec_combo.value() == "40HC" and len(container.table.rows) == 1
    assert wb.name_edit.text() == "Товар"

    window.open_file(path)
    assert not window.dirty
    assert container.spec_combo.value() == "20DC" and len(container.table.rows) == 2
    row = container.table.rows[-1]
    assert row["name"].text() == "Паллета" and row["layers"].text() == "2"
    assert row["tip"].isChecked() and row["stack"].isChecked()
    assert container.result is not None
    assert wb.name_edit.text() == "Кружка" and wb.scheme_combo.value() is Scheme.FBS
    assert wb.tax_combo.value() is TaxMode.INCOME_MINUS_EXPENSES
    assert wb._fields["cost_price"].text() == "350"
    assert wb._fields["commission_fbs_pct"].text() == "абв"
    assert window.calculator.report.cost.cost_net == 147731


def test_old_file_without_modules(window, tmp_path):
    """Файл 0.7.0 и раньше: вкладки-модули приходят со значениями по умолчанию."""
    edit_modules(window)
    path = tmp_path / "old.vedcalc.json"
    assert window._save_to(path)
    data = json.loads(path.read_text("utf-8"))
    del data["modules"]
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    window.open_file(path)
    container, wb = tabs(window)
    assert container.spec_combo.value() == "40HC" and wb.name_edit.text() == "Товар"
    # Старое API чтения расчёта тоже работает с новым файлом
    assert load_shipment(path).name == "Контрольный пример"


def test_bad_modules_section(tmp_path):
    with pytest.raises(FileFormatError):
        project_from_dict({"format": "vedcalc.shipment", "version": 1, "modules": [1]})
    path = tmp_path / "x.json"
    path.write_text("[1, 2]", "utf-8")
    with pytest.raises(FileFormatError):
        load_project(path)


def test_module_edits_mark_dirty(window):
    container, wb = tabs(window)
    assert not window.dirty
    wb._fields["ads_pct"].setText("5")
    assert window.dirty
    window.dirty = False
    container.table.rows[0]["quantity"].setText("50")
    assert window.dirty


def test_duplicate_keeps_modules(window):
    edit_modules(window)
    window.duplicate()
    container, wb = tabs(window)
    assert window.path is None and wb.name_edit.text() == "Кружка"
    assert container.spec_combo.value() == "20DC"


def test_recent_files(window, tmp_path):
    paths = [tmp_path / f"r{n}.vedcalc.json" for n in range(12)]
    for path in paths:
        assert window._save_to(path)
    recent = window.recent_files()
    assert len(recent) == main_window.MAX_RECENT and recent[0] == paths[-1].resolve()
    window.open_file(paths[5])
    assert window.recent_files()[0] == paths[5].resolve()

    paths[-1].unlink()
    window._fill_recent_menu()
    names = [a.text() for a in window.recent_menu.actions()]
    assert paths[-1].name not in names and names[0] == paths[5].name
    assert names[-1] == "Очистить список"
    window.recent_menu.actions()[-1].trigger()
    assert window.recent_files() == []
    window._fill_recent_menu()
    assert [a.text() for a in window.recent_menu.actions()] == ["Нет недавних файлов"]


def test_unreadable_file_leaves_recent(window, tmp_path):
    path = tmp_path / "broken.vedcalc.json"
    assert window._save_to(path)
    path.write_text("{", "utf-8")
    window.open_file(path)
    assert path.resolve() not in window.recent_files()


def test_autosave_and_recovery(window, tmp_path, monkeypatch, qtbot):
    auto = main_window.autosave_path()
    assert not window.autosave() and not auto.exists()  # нет изменений — нечего сохранять
    edit_modules(window)
    assert window.autosave() and auto.is_file()

    # «Сбой»: новое окно без сохранения; соглашаемся восстановить
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w2 = MainWindow(TEST_RATES, threaded=False, extra_modules=[container_spec(threaded=False),
                                                                wb_spec()])  # fmt: skip
    qtbot.addWidget(w2)
    assert w2.offer_recovery()
    assert w2.dirty and w2.path is None
    assert w2.calculator.form.name_edit.text() == "Контрольный пример"
    assert w2.module_tabs["wb"].name_edit.text() == "Кружка"
    assert auto.is_file()  # копия живёт, пока восстановленное не сохранено

    assert w2._save_to(tmp_path / "saved.vedcalc.json")
    assert not auto.exists()
    assert not w2.offer_recovery()


def test_recovery_declined_and_broken(window, monkeypatch):
    auto = main_window.autosave_path()
    edit_modules(window)
    window.autosave()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert not window.offer_recovery() and not auto.exists()
    auto.parent.mkdir(parents=True, exist_ok=True)
    auto.write_text("not json", "utf-8")
    assert not window.offer_recovery() and not auto.exists()


def test_autosave_skips_invalid_input(window):
    window.calculator.form.items_table.rows[0]["price"].setText("сто")
    window.dirty = True
    assert not window.autosave()


def test_recovery_keeps_original_path(window, tmp_path, monkeypatch):
    path = tmp_path / "orig.vedcalc.json"
    assert window._save_to(path)
    tabs(window)[1].name_edit.setText("Изменено")
    assert window.autosave()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert window.offer_recovery()
    assert window.path == path and window.dirty
    assert tabs(window)[1].name_edit.text() == "Изменено"


def test_builtin_modules_have_state():
    ids = {spec.id for spec in builtin_modules()}
    assert ids == {"container", "wb"}
