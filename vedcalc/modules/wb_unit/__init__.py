"""Модуль «WB»: юнит-экономика Wildberries для ИП на УСН.

Ядро — calc.py (без GUI), вкладка — gui.py. С расчётом импорта не связан.
"""

from __future__ import annotations

from vedcalc.modules.registry import ModuleSpec


def module_spec() -> ModuleSpec:
    def factory():
        from vedcalc.modules.wb_unit.gui import WbTab

        return WbTab()

    return ModuleSpec("wb", "WB", factory, order=40)
