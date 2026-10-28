"""Обновление приложения из GitHub Releases.

Поток: проверить последний релиз → скачать .dmg для своей архитектуры → сверить SHA-256 →
смонтировать образ и скопировать новое .app рядом с текущим → после выхода из программы
вспомогательный скрипт подменяет бандл и запускает новую версию.

Модуль не зависит от Qt. Сеть и запуск процессов передаются параметрами — для тестов.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vedcalc import __version__

REPO = "mrzgtv17/vedcalc"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Запасной путь без API (у анонимного API лимит 60 запросов в час на IP-адрес):
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/download/{{tag}}/{{name}}"
CHANGELOG_URL = f"https://raw.githubusercontent.com/{REPO}/{{tag}}/CHANGELOG.md"
CHECKSUMS_ASSET = "SHA256SUMS.txt"
BUNDLE_ID = "ru.vedcalc.app"
CHUNK = 256 * 1024


class UpdateError(Exception):
    """Понятная пользователю ошибка обновления."""


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None  # из поля digest API GitHub или из SHA256SUMS.txt


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    notes: str  # описание релиза (Markdown)
    page_url: str
    asset: Asset | None  # .dmg для текущей архитектуры; None — нет сборки


# --- версии и выбор файла ---------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.2.0' → (0, 2, 0). Суффиксы вроде '.dev0' и '-rc1' отбрасываются."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        raise ValueError(f"Не версия: {text!r}")
    return tuple(int(x) for x in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def machine_arch() -> str:
    """arm64 или x86_64 — как в имени .dmg. Под Rosetta честно вернёт x86_64."""
    return platform.machine() or "arm64"


def parse_release(data: dict, arch: str, fetch: Callable[[str], bytes] | None = None) -> UpdateInfo:
    """Разбирает ответ GitHub API /releases/latest."""
    version = data["tag_name"].lstrip("v")
    assets = {a["name"]: a for a in data.get("assets", [])}
    wanted = f"vedcalc-{version}-macos-{arch}.dmg"
    asset = None
    if wanted in assets:
        raw = assets[wanted]
        digest = raw.get("digest") or ""
        sha = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
        if sha is None and fetch is not None and CHECKSUMS_ASSET in assets:
            sums = fetch(assets[CHECKSUMS_ASSET]["browser_download_url"]).decode("utf-8")
            sha = parse_checksums(sums).get(wanted)
        asset = Asset(wanted, raw["browser_download_url"], int(raw.get("size", 0)), sha)
    return UpdateInfo(version, data.get("body") or "", data.get("html_url") or RELEASES_PAGE, asset)


def parse_checksums(text: str) -> dict[str, str]:
    """Формат shasum: «<hex>  <имя файла>»."""
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            result[parts[1].lstrip("*")] = parts[0].lower()
    return result


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    import urllib.request

    from vedcalc.core.fx import _ssl_context

    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"vedcalc/{__version__}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.read()


def _resolve_url(url: str, timeout: float = 15.0) -> str:
    """Адрес после переадресаций: …/releases/latest → …/releases/tag/vX.Y.Z."""
    import urllib.request

    from vedcalc.core.fx import _ssl_context

    request = urllib.request.Request(url, headers={"User-Agent": f"vedcalc/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.geturl()


def changelog_section(text: str, version: str) -> str:
    """Раздел «## [X.Y.Z] …» из CHANGELOG.md — до следующего раздела или ссылок."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## ["):
            if inside:
                break
            inside = line.startswith(f"## [{version}]")
            continue
        if inside and re.match(r"^\[[^\]]+\]: ", line):
            break
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def _check_without_api(
    current: str, fetch: Callable[[str], bytes], resolve: Callable[[str], str], arch: str
) -> UpdateInfo | None:
    page = resolve(RELEASES_PAGE)
    match = re.search(r"/releases/tag/(v[\w.\-]+)$", page)
    if not match:
        raise UpdateError("На GitHub ещё нет опубликованных релизов")
    tag = match.group(1)
    if not is_newer(tag, current):
        return None
    version = tag.lstrip("v")
    name = f"vedcalc-{version}-macos-{arch}.dmg"
    sums = parse_checksums(
        fetch(DOWNLOAD_URL.format(tag=tag, name=CHECKSUMS_ASSET)).decode("utf-8")
    )
    asset = (
        Asset(name, DOWNLOAD_URL.format(tag=tag, name=name), 0, sums[name])
        if name in sums
        else None
    )
    try:
        notes = changelog_section(fetch(CHANGELOG_URL.format(tag=tag)).decode("utf-8"), version)
    except Exception:  # описание изменений — не главное, обновиться можно и без него
        notes = ""
    return UpdateInfo(version, notes, page, asset)


def check_for_update(
    current: str = __version__,
    fetch: Callable[[str], bytes] = _http_get,
    arch: str | None = None,
    resolve: Callable[[str], str] = _resolve_url,
) -> UpdateInfo | None:
    """Новая версия или None, если установлена последняя.

    Сначала — API GitHub (там и описание релиза). Если API недоступен (в т.ч. исчерпан
    лимит 60 запросов в час на IP), версия берётся из переадресации страницы релизов,
    контрольные суммы — из SHA256SUMS.txt, описание — из CHANGELOG.md.
    """
    arch = arch or machine_arch()
    try:
        data = json.loads(fetch(LATEST_URL))
    except Exception as api_error:
        try:
            return _check_without_api(current, fetch, resolve, arch)
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(f"Не удалось проверить обновления: {exc}") from api_error
    if data.get("draft") or data.get("prerelease") or "tag_name" not in data:
        return None
    if not is_newer(data["tag_name"], current):
        return None
    return parse_release(data, arch, fetch)


# --- загрузка ---------------------------------------------------------------------------


def download(
    asset: Asset,
    dest_dir: Path,
    progress: Callable[[int, int], bool] | None = None,
    opener: Callable[[str], object] | None = None,
) -> Path:
    """Скачивает .dmg и сверяет SHA-256. progress(получено, всего) → False отменяет загрузку."""
    if not asset.sha256:
        raise UpdateError("У релиза нет контрольной суммы — установка из приложения небезопасна")
    if opener is None:
        import urllib.request

        from vedcalc.core.fx import _ssl_context

        def opener(url: str):
            req = urllib.request.Request(url, headers={"User-Agent": f"vedcalc/{__version__}"})
            return urllib.request.urlopen(req, timeout=30, context=_ssl_context())

    path = dest_dir / asset.name
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(asset.url) as response, open(path, "wb") as out:
            total = int(getattr(response, "headers", {}).get("Content-Length") or asset.size or 0)
            while chunk := response.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None and progress(received, total) is False:
                    raise UpdateError("Загрузка отменена")
    except UpdateError:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: {exc}") from exc
    if digest.hexdigest() != asset.sha256.lower():
        path.unlink(missing_ok=True)
        raise UpdateError("Контрольная сумма скачанного файла не совпала — файл повреждён")
    return path


# --- установка (macOS) ------------------------------------------------------------------


def current_app_bundle() -> Path | None:
    """Путь к «Калькулятор ВЭД.app», если программа запущена из собранного приложения."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    exe = Path(sys.executable).resolve()  # …/X.app/Contents/MacOS/vedcalc
    bundle = exe.parents[2]
    return bundle if bundle.suffix == ".app" else None


def can_install_in_place(bundle: Path | None) -> bool:
    return bundle is not None and os.access(bundle.parent, os.W_OK)


Runner = Callable[[list[str]], str]


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise UpdateError(f"Команда {cmd[0]} завершилась с ошибкой: {result.stderr.strip()}")
    return result.stdout


def _bundle_version(app: Path) -> tuple[str, str]:
    import plistlib

    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    return info.get("CFBundleIdentifier", ""), info.get("CFBundleShortVersionString", "")


ATTACH_ATTEMPTS = 5


def _attach(
    dmg: Path, mount: Path, run: Runner, sleep: Callable[[float], None] = time.sleep
) -> None:
    """hdiutil attach с повторами: сразу после отключения другого образа macOS иногда
    отвечает «Resource temporarily unavailable»."""
    cmd = ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)]
    for attempt in range(1, ATTACH_ATTEMPTS + 1):
        try:
            run(cmd)
            return
        except UpdateError:
            if attempt == ATTACH_ATTEMPTS:
                raise
            sleep(attempt)


def stage_from_dmg(dmg: Path, bundle: Path, expected_version: str, run: Runner = _run) -> Path:
    """Монтирует образ, проверяет и копирует новое .app рядом с текущим (ещё не подменяет)."""
    mount = Path(tempfile.mkdtemp(prefix="vedcalc-mount-"))
    staged = bundle.parent / f".{bundle.stem}-update.app"
    shutil.rmtree(staged, ignore_errors=True)
    _attach(dmg, mount, run)
    try:
        apps = [p for p in mount.iterdir() if p.suffix == ".app" and not p.is_symlink()]
        if len(apps) != 1:
            raise UpdateError("В образе обновления не найдено приложение")
        bundle_id, version = _bundle_version(apps[0])
        if bundle_id != BUNDLE_ID:
            raise UpdateError(f"Образ содержит чужое приложение ({bundle_id})")
        if version != expected_version:
            raise UpdateError(f"В образе версия {version}, ожидалась {expected_version}")
        run(["ditto", str(apps[0]), str(staged)])
    finally:
        run(["hdiutil", "detach", "-quiet", str(mount)])
        shutil.rmtree(mount, ignore_errors=True)
    return staged


SWAP_SCRIPT = """#!/bin/bash
# Подмена «Калькулятор ВЭД.app» после выхода из программы (создано vedcalc.updater).
PID={pid}
OLD={old}
NEW={new}
BACKUP={backup}
for _ in $(seq 1 300); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
if kill -0 "$PID" 2>/dev/null; then
  rm -rf "$NEW"  # программа так и не закрылась — подменять на ходу нельзя
  rm -f "$0"
  exit 1
fi
rm -rf "$BACKUP"
mv "$OLD" "$BACKUP" || exit 1
if mv "$NEW" "$OLD"; then
  rm -rf "$BACKUP"
else
  mv "$BACKUP" "$OLD"  # откат: возвращаем старую версию
fi
xattr -dr com.apple.quarantine "$OLD" 2>/dev/null
{relaunch}
rm -f "$0"
"""


def write_swap_script(
    bundle: Path, staged: Path, pid: int, relaunch: str | None = None, directory: Path | None = None
) -> Path:
    """Скрипт, который дождётся выхода процесса pid, подменит бандл и запустит новую версию."""
    q = shlex.quote
    backup = bundle.parent / f".{bundle.stem}-old.app"
    script = SWAP_SCRIPT.format(
        pid=pid,
        old=q(str(bundle)),
        new=q(str(staged)),
        backup=q(str(backup)),
        relaunch=relaunch if relaunch is not None else f"open {q(str(bundle))}",
    )
    fd, name = tempfile.mkstemp(prefix="vedcalc-update-", suffix=".sh", dir=directory)
    with os.fdopen(fd, "w") as f:
        f.write(script)
    os.chmod(name, 0o700)
    return Path(name)


def launch_detached(script: Path) -> None:
    """Запускает скрипт в отдельной сессии: он переживёт выход из программы."""
    subprocess.Popen(
        ["/bin/bash", str(script)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
