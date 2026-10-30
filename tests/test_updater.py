"""Обновление из приложения: версии, разбор релиза, загрузка с SHA-256, подмена .app."""

import hashlib
import io
import json
import plistlib
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

from vedcalc import updater
from vedcalc.updater import Asset, UpdateError

DMG = b"fake dmg payload" * 1000
SHA = hashlib.sha256(DMG).hexdigest()


def release(tag="v0.3.0", *, digest=True, assets=("arm64", "x86_64"), **extra):
    data = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/mrzgtv17/vedcalc/releases/tag/{tag}",
        "body": "### Добавлено\n- что-то полезное",
        "assets": [
            {
                "name": f"vedcalc-{tag[1:]}-macos-{arch}.dmg",
                "size": len(DMG),
                "browser_download_url": f"https://example.test/{arch}.dmg",
                **({"digest": f"sha256:{SHA}"} if digest else {}),
            }
            for arch in assets
        ],
    }
    data.update(extra)
    return data


def fake_fetch(payloads: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        if url not in payloads:
            raise OSError(f"нет сети: {url}")
        return payloads[url]

    return fetch


# --- версии и релиз ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.10.0", "0.9.9", True),
        ("v1.0", "0.9.9", True),
        ("v0.2.0", "0.2.0", False),
        ("v0.2.0", "0.2.0.dev0", False),
        ("v0.1.9", "0.2.0", False),
        ("v0.2.1", "0.2", True),
    ],
)
def test_is_newer(candidate, current, newer):
    assert updater.is_newer(candidate, current) is newer


def test_check_finds_newer_release_for_arch():
    fetch = fake_fetch({updater.LATEST_URL: json.dumps(release()).encode()})
    info = updater.check_for_update("0.2.0", fetch, arch="x86_64")
    assert info.version == "0.3.0"
    assert info.asset.name == "vedcalc-0.3.0-macos-x86_64.dmg"
    assert info.asset.sha256 == SHA
    assert "полезное" in info.notes


def test_check_returns_none_when_up_to_date_or_draft():
    same = fake_fetch({updater.LATEST_URL: json.dumps(release("v0.2.0")).encode()})
    assert updater.check_for_update("0.2.0", same, arch="arm64") is None
    draft = fake_fetch({updater.LATEST_URL: json.dumps(release(draft=True)).encode()})
    assert updater.check_for_update("0.2.0", draft, arch="arm64") is None


def test_check_network_error_is_friendly():
    with pytest.raises(UpdateError, match="Не удалось проверить обновления"):
        updater.check_for_update("0.2.0", fake_fetch({}), arch="arm64")


def test_checksum_falls_back_to_sha256sums_file():
    data = release(digest=False)
    data["assets"].append(
        {"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "https://example.test/sums"}
    )
    sums = f"{SHA}  vedcalc-0.3.0-macos-arm64.dmg\n{'0' * 64}  other.dmg\n".encode()
    fetch = fake_fetch(
        {updater.LATEST_URL: json.dumps(data).encode(), "https://example.test/sums": sums}
    )
    assert updater.check_for_update("0.2.0", fetch, arch="arm64").asset.sha256 == SHA


def test_missing_build_for_arch():
    fetch = fake_fetch({updater.LATEST_URL: json.dumps(release(assets=("arm64",))).encode()})
    assert updater.check_for_update("0.2.0", fetch, arch="x86_64").asset is None


# --- загрузка ----------------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    headers: ClassVar[dict[str, str]] = {"Content-Length": str(len(DMG))}


def opener(payload=DMG):
    return lambda url: FakeResponse(payload)


def test_download_verifies_checksum(tmp_path):
    seen = []
    asset = Asset("x.dmg", "https://example.test/x.dmg", len(DMG), SHA)
    path = updater.download(asset, tmp_path, lambda d, t: seen.append((d, t)) or True, opener())
    assert path.read_bytes() == DMG
    assert seen[-1] == (len(DMG), len(DMG))


def test_download_rejects_corrupted_file(tmp_path):
    asset = Asset("x.dmg", "u", len(DMG), SHA)
    with pytest.raises(UpdateError, match="Контрольная сумма"):
        updater.download(asset, tmp_path, None, opener(DMG[:-1] + b"!"))
    assert not (tmp_path / "x.dmg").exists()


def test_download_can_be_cancelled(tmp_path):
    asset = Asset("x.dmg", "u", len(DMG), SHA)
    with pytest.raises(UpdateError, match="отменена"):
        updater.download(asset, tmp_path, lambda d, t: False, opener())
    assert not (tmp_path / "x.dmg").exists()


def test_download_requires_checksum(tmp_path):
    with pytest.raises(UpdateError, match="контрольной суммы"):
        updater.download(Asset("x.dmg", "u", 1, None), tmp_path, None, opener())


# --- подмена приложения ------------------------------------------------------------------


def make_app(path: Path, version: str, bundle_id: str = updater.BUNDLE_ID) -> Path:
    (path / "Contents" / "MacOS").mkdir(parents=True)
    with open(path / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, f)
    (path / "Contents" / "MacOS" / "vedcalc").write_text(f"version {version}")
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="скрипт подмены .app — только для macOS")
def test_swap_script_replaces_bundle_after_process_exits(tmp_path):
    apps = tmp_path / "Applications"
    old = make_app(apps / "Калькулятор ВЭД.app", "0.2.0")
    staged = make_app(apps / ".Калькулятор ВЭД-update.app", "0.3.0")
    waiter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    # Забираем код выхода сразу: иначе процесс останется «зомби» и kill -0 будет его видеть
    threading.Thread(target=waiter.wait, daemon=True).start()
    flag = tmp_path / "relaunched"
    script = updater.write_swap_script(
        old, staged, waiter.pid, relaunch=f"touch '{flag}'", directory=tmp_path
    )
    subprocess.run(["/bin/bash", str(script)], check=True, timeout=30)
    assert (old / "Contents" / "MacOS" / "vedcalc").read_text() == "version 0.3.0"
    assert not staged.exists()
    assert not (apps / ".Калькулятор ВЭД-old.app").exists()
    assert flag.exists()
    assert not script.exists()  # скрипт удаляет себя


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("hdiutil"), reason="нужен macOS")
class TestStageFromDmg:
    def make_dmg(self, tmp_path: Path, version: str, bundle_id: str = updater.BUNDLE_ID) -> Path:
        src = tmp_path / "src"
        make_app(src / "Калькулятор ВЭД.app", version, bundle_id)
        (src / "Программы").symlink_to("/Applications")
        dmg = tmp_path / "update.dmg"
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-quiet",
                "-srcfolder",
                str(src),
                "-format",
                "UDZO",
                "-ov",
                str(dmg),
            ],
            check=True,
        )
        return dmg

    def test_stages_new_bundle_next_to_current(self, tmp_path):
        current = make_app(tmp_path / "apps" / "Калькулятор ВЭД.app", "0.2.0")
        staged = updater.stage_from_dmg(self.make_dmg(tmp_path, "0.3.0"), current, "0.3.0")
        assert staged.parent == current.parent
        assert (staged / "Contents" / "MacOS" / "vedcalc").read_text() == "version 0.3.0"

    def test_rejects_foreign_app_and_wrong_version(self, tmp_path):
        current = make_app(tmp_path / "apps" / "Калькулятор ВЭД.app", "0.2.0")
        with pytest.raises(UpdateError, match=r"версия 0\.3\.0, ожидалась 0\.4\.0"):
            updater.stage_from_dmg(self.make_dmg(tmp_path, "0.3.0"), current, "0.4.0")
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(UpdateError, match="чужое приложение"):
            updater.stage_from_dmg(self.make_dmg(other, "0.3.0", "com.evil"), current, "0.3.0")


def test_running_from_source_is_not_installable():
    assert updater.current_app_bundle() is None
    assert not updater.can_install_in_place(None)


# --- запасной путь без API ---------------------------------------------------------------

CHANGELOG = """# История версий

## [Не выпущено]

## [0.3.0] — 2026-10-28

### Добавлено
- Вкладка «Цена продажи».

## [0.2.0] — 2026-10-26

- старое

[0.3.0]: ../../compare/v0.2.0...v0.3.0
"""


def rate_limited_fetch(extra: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        if url == updater.LATEST_URL:
            raise OSError("HTTP Error 403: rate limit exceeded")
        if url not in extra:
            raise OSError(f"нет сети: {url}")
        return extra[url]

    return fetch


def resolve_to(tag: str):
    return lambda url: f"https://github.com/mrzgtv17/vedcalc/releases/tag/{tag}"


def test_fallback_when_api_rate_limited():
    base = "https://github.com/mrzgtv17/vedcalc/releases/download/v0.3.0/"
    fetch = rate_limited_fetch(
        {
            base + "SHA256SUMS.txt": f"{SHA}  vedcalc-0.3.0-macos-arm64.dmg\n".encode(),
            "https://raw.githubusercontent.com/mrzgtv17/vedcalc/v0.3.0/CHANGELOG.md": (
                CHANGELOG.encode()
            ),
        }
    )
    info = updater.check_for_update("0.2.0", fetch, "arm64", resolve_to("v0.3.0"))
    assert info.version == "0.3.0"
    assert info.asset.url == base + "vedcalc-0.3.0-macos-arm64.dmg"
    assert info.asset.sha256 == SHA
    assert info.notes == "### Добавлено\n- Вкладка «Цена продажи»."


def test_fallback_without_changelog_and_up_to_date():
    base = "https://github.com/mrzgtv17/vedcalc/releases/download/v0.3.0/"
    fetch = rate_limited_fetch({base + "SHA256SUMS.txt": f"{SHA}  other.dmg\n".encode()})
    info = updater.check_for_update("0.2.0", fetch, "arm64", resolve_to("v0.3.0"))
    assert info.notes == "" and info.asset is None  # нет сборки → только «скачать вручную»
    assert updater.check_for_update("0.3.0", fetch, "arm64", resolve_to("v0.3.0")) is None


def test_fallback_failure_is_friendly():
    def broken(url):
        raise OSError("нет сети")

    with pytest.raises(UpdateError, match="Не удалось проверить обновления"):
        updater.check_for_update("0.2.0", rate_limited_fetch({}), "arm64", broken)


def test_changelog_section():
    assert updater.changelog_section(CHANGELOG, "0.2.0") == "- старое"
    assert updater.changelog_section(CHANGELOG, "9.9.9") == ""


def test_attach_retries_temporary_failure(tmp_path):
    calls, pauses = [], []

    def flaky(cmd):
        calls.append(cmd)
        if len(calls) < 3:
            raise UpdateError("hdiutil: attach failed - Resource temporarily unavailable")
        return ""

    updater._attach(tmp_path / "a.dmg", tmp_path / "m", flaky, pauses.append)
    assert len(calls) == 3 and pauses == [1, 2]

    def broken(cmd):
        raise UpdateError("hdiutil: attach failed - image not recognized")

    with pytest.raises(UpdateError, match="not recognized"):
        updater._attach(tmp_path / "a.dmg", tmp_path / "m", broken, lambda s: None)


# --- Windows -----------------------------------------------------------------------------


def test_windows_asset_name_and_release_lookup():
    assert updater.asset_name("0.16.0", "x64") == "vedcalc-0.16.0-windows-x64-setup.exe"
    assert updater.asset_name("0.16.0", "arm64") == "vedcalc-0.16.0-macos-arm64.dmg"
    release = {
        "tag_name": "v0.16.0",
        "assets": [
            {"name": "vedcalc-0.16.0-macos-arm64.dmg", "browser_download_url": "u1", "size": 1},
            {
                "name": "vedcalc-0.16.0-windows-x64-setup.exe",
                "browser_download_url": "u2",
                "size": 2,
                "digest": f"sha256:{SHA}",
            },
        ],
    }
    win = updater.parse_release(release, "x64")
    assert win.asset.url == "u2" and win.asset.sha256 == SHA
    assert updater.parse_release(release, "arm64").asset.url == "u1"  # Mac выбирает своё


def test_windows_arch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert updater.machine_arch() == "x64"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/vedcalc/vedcalc.exe")
    assert updater.windows_install_dir().name == "vedcalc"
    assert updater.current_app_bundle() is None


def test_windows_install_command_waits_for_exit():
    cmd = updater.windows_install_command(Path("C:/Temp/it's/setup.exe"), 4242)
    assert cmd[0] == "powershell"
    script = cmd[-1]
    assert script.index("Wait-Process -Id 4242") < script.index("Start-Process")
    assert "'/VERYSILENT'" in script and "it''s" in script
