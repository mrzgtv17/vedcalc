"""Поднять версию на патч (0.6.0 → 0.6.1) и добавить запись в CHANGELOG.md.

Использование: python scripts/bump_patch.py "Текст записи для раздела «Изменено»"
Печатает новую версию.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    note = sys.argv[1]
    init = ROOT / "vedcalc" / "__init__.py"
    old = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', init.read_text())
    major, minor, patch = map(int, old.groups())
    new = f"{major}.{minor}.{patch + 1}"
    prev = f"{major}.{minor}.{patch}"
    for path in (init, ROOT / "pyproject.toml"):
        path.write_text(path.read_text().replace(f'"{prev}"', f'"{new}"', 1))
    changelog = ROOT / "CHANGELOG.md"
    text = changelog.read_text()
    text = text.replace(
        "## [Не выпущено]\n",
        f"## [Не выпущено]\n\n## [{new}] — {date.today().isoformat()}\n\n### Изменено\n- {note}\n",
        1,
    )
    text = text.replace(
        f"[Не выпущено]: ../../compare/v{prev}...HEAD",
        f"[Не выпущено]: ../../compare/v{new}...HEAD\n[{new}]: ../../compare/v{prev}...v{new}",
    )
    changelog.write_text(text)
    print(new)


if __name__ == "__main__":
    main()
