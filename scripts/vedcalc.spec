# PyInstaller: сборка «Калькулятор ВЭД.app». Запуск — scripts/build_macos.sh.
# ruff: noqa
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "vedcalc" / "__init__.py").read_text())[1]

a = Analysis(
    [str(ROOT / "scripts" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "vedcalc" / "data" / "*.json"), "vedcalc/data"),
        (str(ROOT / "vedcalc" / "gui" / "assets" / "*.svg"), "vedcalc/gui/assets"),
    ],
    hiddenimports=["openpyxl", "certifi", "docx", "keyring.backends.macOS"],
    excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vedcalc",
    console=False,
    target_arch=None,  # архитектура интерпретатора: arm64 или x86_64
)
coll = COLLECT(exe, a.binaries, a.datas, name="vedcalc")
app = BUNDLE(
    coll,
    name="Калькулятор ВЭД.app",
    bundle_identifier="ru.vedcalc.app",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Калькулятор ВЭД",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleDevelopmentRegion": "ru",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© 2026. All rights reserved.",
    },
)
