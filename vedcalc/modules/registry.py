"""Реестр модулей. Модуль регистрирует себя, GUI строит по реестру вкладки.

Фабрика вызывается лениво и возвращает виджет; ядро про GUI ничего не знает.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    id: str
    title: str
    factory: Callable[[], Any]
    order: int = 100
    enabled: bool = True


_REGISTRY: dict[str, ModuleSpec] = {}


def register(spec: ModuleSpec) -> None:
    if spec.id in _REGISTRY:
        raise ValueError(f"Модуль {spec.id!r} уже зарегистрирован")
    _REGISTRY[spec.id] = spec


def modules() -> list[ModuleSpec]:
    return sorted((s for s in _REGISTRY.values() if s.enabled), key=lambda s: (s.order, s.title))


def clear() -> None:
    """Для тестов."""
    _REGISTRY.clear()
