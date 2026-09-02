from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication, QDialog, QTextEdit

from fleasion.gui.delete_cache import DeleteCacheWindow


def _on_done(dialog: DeleteCacheWindow) -> None:
    callback = cast('Callable[[], None]', getattr(dialog, '_on_done'))
    callback()


def _on_finished(dialog: DeleteCacheWindow) -> None:
    callback = cast('Callable[[int], None]', getattr(dialog, '_on_finished'))
    callback(0)


def _release_if_finished(dialog: DeleteCacheWindow) -> None:
    callback = cast('Callable[[], None]', getattr(dialog, '_release_if_finished'))
    callback()


def _live_windows() -> set[DeleteCacheWindow]:
    return cast('set[DeleteCacheWindow]', DeleteCacheWindow.__dict__['_live_windows'])


def _close_delay() -> int:
    return cast('int', DeleteCacheWindow.__dict__['_CLOSE_AFTER_DONE_MS'])


def _record_accept(values: list[bool]) -> Callable[[], None]:
    def accept() -> None:
        values.append(True)

    return accept


@pytest.fixture(scope='module')
def qt_app() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def test_delete_cache_window_closes_after_done(
    qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = DeleteCacheWindow.__new__(DeleteCacheWindow)
    QDialog.__init__(dialog)
    dialog.status_text = QTextEdit()
    setattr(dialog, '_worker_done', False)
    setattr(dialog, '_dialog_finished', False)

    scheduled: dict[str, object] = {}

    def schedule(delay: int, callback: Callable[[], None]) -> None:
        scheduled.update(delay=delay, callback=callback)

    monkeypatch.setattr('fleasion.gui.delete_cache.QTimer.singleShot', schedule)
    accepted: list[bool] = []
    monkeypatch.setattr(dialog, 'accept', _record_accept(accepted))

    _on_done(dialog)

    assert dialog.status_text.toPlainText().endswith('Done.')
    assert scheduled['delay'] == _close_delay()
    callback = cast('Callable[[], None]', scheduled['callback'])
    callback()
    assert accepted == [True]
    dialog.deleteLater()
    qt_app.processEvents()


def test_delete_cache_window_keepalive_waits_for_worker_and_dialog(
    qt_app: QApplication,
) -> None:
    dialog = DeleteCacheWindow.__new__(DeleteCacheWindow)
    QDialog.__init__(dialog)
    setattr(dialog, '_worker_done', False)
    setattr(dialog, '_dialog_finished', False)
    live_windows = _live_windows()
    live_windows.add(dialog)

    _on_finished(dialog)
    assert dialog in live_windows

    setattr(dialog, '_worker_done', True)
    _release_if_finished(dialog)
    assert dialog not in live_windows

    dialog.deleteLater()
    qt_app.processEvents()
