"""Проверка и установка обновлений из приложения (GUI-часть, логика — vedcalc.updater)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vedcalc import __version__, updater
from vedcalc.updater import UpdateError, UpdateInfo

SETTINGS_LAST_CHECK = "updates/last_check"
SETTINGS_SKIPPED = "updates/skipped_version"
STARTUP_DELAY_MS = 3000


class _Signals(QObject):
    checked = Signal(object, object)  # UpdateInfo | None, ошибка | None
    progress = Signal(int, int)
    prepared = Signal(object, object)  # Path | None, ошибка | None


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], None]):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class UpdateDialog(QDialog):
    """«Доступна версия X»: что нового и что сделать."""

    INSTALL, DOWNLOAD, SKIP, LATER = range(4)

    def __init__(self, info: UpdateInfo, can_install: bool, reason: str, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Обновление")
        self.setMinimumSize(520, 420)
        self.choice = self.LATER
        v = QVBoxLayout(self)
        v.setSpacing(10)
        title = QLabel(f"Доступна версия {info.version}")
        title.setObjectName("cardTitle")
        v.addWidget(title)
        current = QLabel(f"У вас установлена версия {__version__}.")
        current.setObjectName("muted")
        v.addWidget(current)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "Описание изменений не опубликовано.")
        v.addWidget(notes, 1)
        if reason:
            hint = QLabel(reason)
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            v.addWidget(hint)

        buttons = QHBoxLayout()
        skip = QPushButton("Пропустить эту версию")
        later = QPushButton("Позже")
        manual = QPushButton("Скачать вручную")
        self.install = QPushButton("Установить и перезапустить")
        self.install.setObjectName("primary")
        self.install.setDefault(True)
        self.install.setEnabled(can_install)
        for button in (skip, later, manual, self.install):
            # QSS-отступы Qt не всегда учитывает в размере — задаём ширину по тексту
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + 36)
        buttons.addWidget(skip)
        buttons.addStretch(1)
        buttons.addWidget(later)
        buttons.addWidget(manual)
        buttons.addWidget(self.install)
        v.addLayout(buttons)
        for button, choice in (
            (self.install, self.INSTALL),
            (manual, self.DOWNLOAD),
            (skip, self.SKIP),
            (later, self.LATER),
        ):
            button.clicked.connect(lambda _=False, c=choice: self._finish(c))

    def _finish(self, choice: int) -> None:
        self.choice = choice
        self.accept()


class UpdateController(QObject):
    """Проверка обновлений (вручную и раз в сутки при запуске) и установка."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        fetch: Callable[[str], bytes] | None = None,
        settings: QSettings | None = None,
        threaded: bool = True,
    ):
        super().__init__(window)
        self.window = window
        self.fetch = fetch or updater._http_get
        self.settings = settings or QSettings("vedcalc", "vedcalc")
        self.threaded = threaded
        self._signals = _Signals()
        self._signals.checked.connect(self._on_checked)
        self._manual = False
        self._busy = False

    # --- проверка ---------------------------------------------------------------------

    def schedule_startup_check(self) -> None:
        last = self.settings.value(SETTINGS_LAST_CHECK, "")
        if last == date.today().isoformat():
            return
        QTimer.singleShot(STARTUP_DELAY_MS, lambda: self.check(manual=False))

    def check(self, manual: bool = True) -> None:
        if self._busy:
            return
        self._busy = True
        self._manual = manual

        def work() -> None:
            try:
                info = updater.check_for_update(fetch=self.fetch)
                self._signals.checked.emit(info, None)
            except UpdateError as exc:
                self._signals.checked.emit(None, str(exc))

        self._start(work)

    def _start(self, fn: Callable[[], None]) -> None:
        if self.threaded:
            QThreadPool.globalInstance().start(_Job(fn))
        else:
            fn()

    def _on_checked(self, info: UpdateInfo | None, error: str | None) -> None:
        self._busy = False
        if error:
            if self._manual:
                QMessageBox.warning(self.window, "Обновление", error)
            return
        self.settings.setValue(SETTINGS_LAST_CHECK, date.today().isoformat())
        if info is None:
            if self._manual:
                QMessageBox.information(
                    self.window, "Обновление", f"У вас последняя версия — {__version__}."
                )
            return
        if not self._manual and self.settings.value(SETTINGS_SKIPPED, "") == info.version:
            return
        self.offer(info)

    # --- предложение и установка ------------------------------------------------------

    def install_status(self, info: UpdateInfo) -> tuple[bool, str]:
        bundle = updater.current_app_bundle()
        if info.asset is None:
            return False, "Для вашего Mac нет готовой сборки в этом релизе."
        if not info.asset.sha256:
            return False, "У релиза нет контрольной суммы — установите обновление вручную."
        if bundle is None:
            return False, "Программа запущена из исходников — обновите её через git pull."
        if not updater.can_install_in_place(bundle):
            return False, f"Нет прав на запись в папку «{bundle.parent}» — установите вручную."
        return True, ""

    def offer(self, info: UpdateInfo) -> int:
        can_install, reason = self.install_status(info)
        dialog = UpdateDialog(info, can_install, reason, self.window)
        dialog.exec()
        if dialog.choice == UpdateDialog.SKIP:
            self.settings.setValue(SETTINGS_SKIPPED, info.version)
        elif dialog.choice == UpdateDialog.DOWNLOAD:
            url = info.asset.url if info.asset else info.page_url
            QDesktopServices.openUrl(QUrl(url))
        elif dialog.choice == UpdateDialog.INSTALL:
            self.install(info)
        return dialog.choice

    def install(self, info: UpdateInfo) -> None:
        bundle = updater.current_app_bundle()
        assert bundle is not None and info.asset is not None
        progress = QProgressDialog("Загрузка обновления…", "Отменить", 0, 100, self.window)
        progress.setWindowTitle("Обновление")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        cancelled = {"value": False}
        progress.canceled.connect(lambda: cancelled.__setitem__("value", True))

        def on_progress(done: int, total: int) -> None:
            if total:
                progress.setValue(min(99, done * 100 // total))
                progress.setLabelText(
                    f"Загрузка обновления… {done / 1_048_576:.1f} из {total / 1_048_576:.1f} МБ"
                )

        signals = _Signals()
        signals.progress.connect(on_progress)
        result: dict[str, object] = {}

        def work() -> None:
            try:
                tmp = Path(tempfile.mkdtemp(prefix="vedcalc-download-"))
                dmg = updater.download(
                    info.asset,
                    tmp,
                    lambda d, t: (signals.progress.emit(d, t), not cancelled["value"])[1],
                )
                staged = updater.stage_from_dmg(dmg, bundle, info.version)
                dmg.unlink(missing_ok=True)
                signals.prepared.emit(staged, None)
            except UpdateError as exc:
                signals.prepared.emit(None, str(exc))
            except Exception as exc:  # не должно случаться; не роняем приложение
                signals.prepared.emit(None, f"Неожиданная ошибка: {exc}")

        def on_prepared(staged: Path | None, error: str | None) -> None:
            result["staged"], result["error"] = staged, error
            progress.close()

        signals.prepared.connect(on_prepared)
        self._keep = signals  # сигналы должны жить, пока идёт загрузка
        self._start(work)
        if "error" not in result:
            progress.exec()
        if result.get("error"):
            if not cancelled["value"]:
                QMessageBox.warning(self.window, "Обновление", str(result["error"]))
            return
        self._restart_into(bundle, result["staged"])

    def _restart_into(self, bundle: Path, staged: Path) -> None:
        answer = QMessageBox.question(
            self.window,
            "Обновление",
            "Обновление загружено. Перезапустить программу сейчас?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            QMessageBox.information(
                self.window,
                "Обновление",
                "Обновление не установлено. Установить его можно позже: "
                "Справка → Проверить обновления.",
            )
            _remove_tree(staged)
            return
        # Сначала закрываем окно: если пользователь отменит выход из-за несохранённого
        # расчёта, подмена не начнётся.
        if not self.window.close():
            _remove_tree(staged)
            return
        script = updater.write_swap_script(bundle, staged, os.getpid())
        updater.launch_detached(script)
        QApplication.quit()


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
