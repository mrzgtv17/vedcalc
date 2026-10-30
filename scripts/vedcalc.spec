# PyInstaller: сборка для macOS («Калькулятор ВЭД.app», scripts/build_macos.sh)
# и Windows (папка dist/vedcalc, scripts/build_windows.ps1).
# ruff: noqa
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "vedcalc" / "__init__.py").read_text("utf-8"))[1]

KEYRING = "keyring.backends.Windows" if sys.platform == "win32" else "keyring.backends.macOS"

a = Analysis(
    [str(ROOT / "scripts" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "vedcalc" / "data" / "*.json"), "vedcalc/data"),
        (str(ROOT / "vedcalc" / "gui" / "assets" / "*.svg"), "vedcalc/gui/assets"),
    ],
    hiddenimports=["openpyxl", "certifi", "docx", KEYRING],
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
if sys.platform == "darwin":
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
