"""Тарифы WB по API: разбор ответов, кэш, токен, подстановка во вкладку «WB» (без сети)."""

from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from vedcalc.modules.wb_unit import api
from vedcalc.modules.wb_unit.api import (
    BOX_URL,
    COMMISSION_URL,
    TokenStore,
    WbApiError,
    WbData,
    load_cache,
    parse_box,
    parse_commissions,
    refresh,
)
from vedcalc.modules.wb_unit.calc import Scheme, WbInput, evaluate

COMMISSIONS = {
    "report": [
        {"parentName": "Посуда", "subjectName": "Кружки", "subjectID": 101,
         "paidStorageKgvp": 24.5, "kgvpMarketplace": 27.5, "kgvpSupplier": 22},
        {"parentName": "Дом", "subjectName": "Пледы", "subjectID": 102,
         "paidStorageKgvp": 23, "kgvpMarketplace": 26},
        {"subjectName": ""},
    ]
}  # fmt: skip
BOX = {
    "response": {
        "data": {
            "dtNextBox": "",
            "dtTillMax": "2026-11-30",
            "warehouseList": [
                {"warehouseName": "Коледино", "geoName": "Центральный федеральный округ",
                 "boxDeliveryBase": "78,2", "boxDeliveryLiter": "23,8",
                 "boxDeliveryCoefExpr": "170",
                 "boxDeliveryMarketplaceBase": "57,5", "boxDeliveryMarketplaceLiter": "17,5",
                 "boxDeliveryMarketplaceCoefExpr": "125", "boxStorageBase": "0,1",
                 "boxStorageLiter": "0,1", "boxStorageCoefExpr": "125"},
                {"warehouseName": "Свой склад", "geoName": "", "boxDeliveryBase": "-",
                 "boxDeliveryLiter": "-", "boxDeliveryCoefExpr": "-",
                 "boxDeliveryMarketplaceBase": "40", "boxDeliveryMarketplaceLiter": "11",
                 "boxDeliveryMarketplaceCoefExpr": "100", "boxStorageBase": "-",
                 "boxStorageLiter": "-", "boxStorageCoefExpr": "-"},
            ],
        }
    }
}  # fmt: skip


class FakeApi:
    def __init__(self, error: WbApiError | None = None):
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def __call__(self, url: str, token: str) -> dict:
        self.calls.append((url, token))
        if self.error:
            raise self.error
        return COMMISSIONS if "commission" in url else BOX


def test_parse_commissions():
    subjects = parse_commissions(COMMISSIONS)
    assert [s.title for s in subjects] == ["Кружки (Посуда)", "Пледы (Дом)"]
    assert (subjects[0].fbo_pct, subjects[0].fbs_pct) == (D("24.5"), D("27.5"))
    with pytest.raises(WbApiError):
        parse_commissions({"data": []})


def test_parse_box_and_split_coefficient():
    koledino, own = parse_box(BOX)
    assert koledino.has_fbo() and koledino.has_fbs() and not own.has_fbo() and own.has_fbs()
    # Суммы WB уже с коэффициентом: раскладываем на базовый тариф и коэффициент
    assert koledino.tariff_fields(fbs=False) == {
        "first_liter": D("46.00"), "extra_liter": D("14.00"), "warehouse_coef_pct": D(170),
        "storage_first_liter": D("0.0800"), "storage_per_liter_day": D("0.0800"),
        "storage_coef_pct": D(125),
    }  # fmt: skip
    assert koledino.tariff_fields(fbs=True) == {
        "first_liter": D("46.00"), "extra_liter": D("14.00"), "warehouse_coef_pct": D(125),
    }  # fmt: skip
    assert own.tariff_fields(fbs=False) == {}
    with pytest.raises(WbApiError):
        parse_box({"response": None})


def test_refresh_saves_cache_and_uses_token():
    fake = FakeApi()
    data = refresh(" token-123 ", fake, today=date(2026, 10, 28))
    assert fake.calls == [
        (COMMISSION_URL, "token-123"), (BOX_URL.format(date="2026-10-28"), "token-123"),
    ]  # fmt: skip
    assert len(data.subjects) == 2 and len(data.warehouses) == 2 and not data.stale()
    cached = load_cache()
    assert cached.subjects == data.subjects and cached.warehouses == data.warehouses
    assert cached.fetched_at == data.fetched_at
    assert data.stale(data.fetched_at + timedelta(days=2))
    with pytest.raises(WbApiError):
        refresh("  ", fake)


def test_broken_cache_is_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{oops", "utf-8")
    assert load_cache(path) == WbData()
    assert WbData().stale()


def test_http_errors_are_readable(monkeypatch):
    import urllib.error
    import urllib.request

    def fail(*a, **k):
        raise urllib.error.HTTPError("u", 401, "x", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(WbApiError, match="Тарифы"):
        api.http_fetch("https://common-api.wildberries.ru/x", "t")


def test_token_store_in_memory_for_tests():
    store = TokenStore()
    assert store.get() == ""
    store.set("abc")
    assert store.get() == "abc"
    store.delete()
    assert store.get() == ""


def test_storage_with_first_liter_and_own_coefficient():
    inp = WbInput(length_cm=D(20), width_cm=D(20), height_cm=D(10), storage_days=30,
                  storage_per_liter_day=D("0.08"), storage_first_liter=D("0.1"),
                  storage_coef_pct=D(125), warehouse_coef_pct=D(170))  # fmt: skip
    r = evaluate(inp)
    # 4 л: (0,1 + 0,08 × 3) × 125% × 30 дн. = 12,75
    assert r.storage == D("12.75")
    (line,) = [x for x in r.lines if x.label == "Хранение"]
    assert line.formula == "(0,10 + 0,08 × 3 л) × 125% × 30 дн."
    # Без тарифов склада — как раньше: литры × тариф × коэффициент склада
    plain = evaluate(WbInput(length_cm=D(20), width_cm=D(20), height_cm=D(10),
                             storage_days=30, storage_per_liter_day=D("0.08"),
                             warehouse_coef_pct=D(170)))  # fmt: skip
    assert plain.storage == D("16.32")


# --- вкладка «WB» -------------------------------------------------------------------------


@pytest.fixture
def tab(qtbot):
    pytest.importorskip("PySide6")
    from vedcalc.modules.wb_unit.gui import WbTab

    api.cache_path().unlink(missing_ok=True)
    store = TokenStore()
    fake = FakeApi()
    w = WbTab(token_store=store, fetch=fake, threaded=False)
    qtbot.addWidget(w)
    w.fake = fake
    return w


def test_without_token(tab):
    box = tab.api_box
    assert not box.refresh_button.isEnabled() and not box.subject_combo.isEnabled()
    assert "вручную" in box.status.text()
    box.auto_refresh()
    assert tab.fake.calls == []


def test_refresh_and_pick(tab):
    box = tab.api_box
    box.store.set("tok")
    box.start_refresh()
    assert len(tab.fake.calls) == 2 and "2 предметов, 2 складов" in box.status.text()
    box.subject_combo.setCurrentText("Кружки (Посуда)")
    box.subject_combo.activated.emit(box.subject_combo.currentIndex())
    assert tab._fields["commission_fbo_pct"].value() == D("24.5")
    assert tab._fields["commission_fbs_pct"].value() == D("27.5")
    box.warehouse_combo.setCurrentText("Коледино")
    box.warehouse_combo.activated.emit(box.warehouse_combo.currentIndex())
    assert tab._fields["first_liter"].value() == D("46.00")
    assert tab._fields["warehouse_coef_pct"].value() == D(170)
    assert tab._fields["storage_coef_pct"].value() == D(125)
    # FBS — свои тарифы логистики склада
    tab.scheme_combo.set_value(Scheme.FBS)
    assert tab._fields["warehouse_coef_pct"].value() == D(125)
    assert tab.recalc() is not None

    state = tab.save_state()
    assert state["wb_subject"] == "Кружки (Посуда)" and state["wb_warehouse"] == "Коледино"
    tab.load_state(None)
    assert box.selected_subject_title() == "" and box.selected_warehouse_name() == ""
    tab.load_state(state)
    assert box.selected_warehouse_name() == "Коледино"
    assert tab._fields["warehouse_coef_pct"].value() == D(125)  # из файла, без подстановки


def test_refresh_error_is_shown(tab):
    tab.api_box.store.set("tok")
    tab.fake.error = WbApiError("Токен не подходит")
    tab.api_box.start_refresh()
    assert "Токен не подходит" in tab.api_box.status.text()
    assert tab.api_box.refresh_button.isEnabled()


def test_auto_refresh_once_when_stale(tab):
    box = tab.api_box
    box.store.set("tok")
    box.data = WbData(datetime.now() - timedelta(days=3))
    box.auto_refresh()
    box.auto_refresh()
    assert len(tab.fake.calls) == 2


def test_token_dialog(tab, monkeypatch):
    from vedcalc.modules.wb_unit.api_gui import TokenDialog

    dialog = TokenDialog(tab.api_box.store)
    dialog.edit.setText("  abc def ")
    dialog.accept()
    assert tab.api_box.store.get() == "abcdef"
    assert dialog.edit.echoMode() == dialog.edit.EchoMode.Password
    again = TokenDialog(tab.api_box.store)
    assert again.delete_button.isEnabled()
    again.delete()
    assert tab.api_box.store.get() == "" and again.deleted
