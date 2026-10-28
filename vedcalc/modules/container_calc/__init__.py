"""Модуль «Контейнер»: укладка грузовых мест в морской контейнер.

Ядро — packing.py (без GUI), вкладка — gui.py. С расчётом импорта не связан.
"""

from __future__ import annotations

from vedcalc.modules.registry import ModuleSpec


def module_spec(*, threaded: bool = True) -> ModuleSpec:
    def factory():
        from vedcalc.modules.container_calc.gui import ContainerTab

        return ContainerTab(threaded=threaded)

    return ModuleSpec("container", "Контейнер", factory, order=30)
