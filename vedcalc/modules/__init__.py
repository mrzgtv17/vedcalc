"""Подключаемые модули (вкладки главного окна): регистрация без правки ядра."""

from __future__ import annotations

from vedcalc.modules.registry import ModuleSpec


def builtin_modules() -> list[ModuleSpec]:
    """Встроенные модули — вкладки после «Импорта» и «Цены продажи»."""
    from vedcalc.modules.container_calc import module_spec as container

    return [container()]
