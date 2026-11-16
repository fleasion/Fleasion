"""The macOS Rosemary startup warning must be read and explicitly accepted."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsDropShadowEffect, QLabel

from fleasion.app.dialogs import macos_rosemary_notice as notice
from fleasion.localization import tr

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager


@pytest.fixture(scope='module')
def app() -> QApplication:
    current = QApplication.instance()
    return cast('QApplication', current) if current else QApplication([])


class FakeClock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def isValid(self) -> bool:
        return True

    def elapsed(self) -> int:
        return self.ms


def test_timed_modal_cannot_be_skipped_or_dismissed_before_ack(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = notice.MacOSRosemaryNoticeDialog()
    dialog.show()
    app.processEvents()

    assert dialog.isModal()
    assert dialog._elapsed.isValid()
    assert dialog._countdown.isActive()
    assert not dialog.ok_button.isEnabled()
    assert dialog.ok_button.text() == tr('macos_rosemary.read_countdown', seconds=20)
    assert dialog.findChild(QLabel, 'MacOSRosemaryNoticeHeadline') is not None

    QTest.keyClick(dialog, Qt.Key.Key_Return)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    dialog.accept()
    dialog.reject()
    dialog.close()
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.result() != QDialog.DialogCode.Accepted

    clock = FakeClock(19_999)
    monkeypatch.setattr(dialog, '_elapsed', clock)
    dialog._update_countdown()
    assert dialog.ok_button.text() == tr('macos_rosemary.read_countdown', seconds=1)
    assert not dialog.ok_button.isEnabled()
    dialog.accept()
    assert dialog.isVisible()

    clock.ms = 20_000
    dialog._update_countdown()
    assert not dialog._countdown.isActive()
    assert dialog.ok_button.text() == 'I READ THE ABOVE'
    assert dialog.ok_button.isEnabled()
    assert dialog.ok_button.minimumHeight() >= 54
    assert isinstance(dialog.ok_button.graphicsEffect(), QGraphicsDropShadowEffect)
    assert dialog._attention.state() == dialog._attention.State.Running

    # It does not disappear when the countdown reaches zero
    assert dialog.isVisible()
    dialog.close()
    app.processEvents()
    assert dialog.isVisible()

    dialog.ok_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


@pytest.mark.parametrize('accepted', [False, True])
def test_existing_one_time_flag_is_saved_only_after_button_acknowledgment(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, accepted: bool
) -> None:
    del app
    saved: list[dict[str, object]] = []
    state: dict[str, object] = {}
    config = SimpleNamespace(settings=state, save=lambda: saved.append(dict(state)))
    code = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(notice.MacOSRosemaryNoticeDialog, 'exec', lambda _self: code)

    result = notice.show_macos_rosemary_notice(cast('ConfigManager', config))

    assert result is accepted
    assert state.get('macos_rosemary_notice_shown') is (True if accepted else None)
    assert saved == ([{'macos_rosemary_notice_shown': True}] if accepted else [])
    assert 'macos_rosemary_launch_notice_ack_v2' not in state
