#!/usr/bin/env bash
# Сборка .app и .dmg для текущей архитектуры (arm64 или x86_64).
# Использование: scripts/build_macos.sh [путь к python, по умолчанию .venv/bin/python]
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${1:-.venv/bin/python}"
VERSION="$("$PY" -c 'import vedcalc; print(vedcalc.__version__)')"
ARCH="$(uname -m)"
APP="dist/Калькулятор ВЭД.app"
DMG="dist/vedcalc-${VERSION}-macos-${ARCH}.dmg"

rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean scripts/vedcalc.spec

# Ad-hoc подпись: без неё приложение на Apple Silicon может не запуститься.
# Это не подпись Apple Developer — Gatekeeper всё равно спросит при первом запуске.
codesign --force --deep --sign - "$APP"

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Программы"
hdiutil create -volname "Калькулятор ВЭД ${VERSION}" -srcfolder "$STAGE" \
  -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$STAGE"

echo "Готово:"
du -sh "$APP" "$DMG"
