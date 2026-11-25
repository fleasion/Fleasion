import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from typing import cast

from PySide6.QtWidgets import QApplication, QWidget

from fleasion.gui.proxy_gate import ProxyGate


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def _overlay(gate: ProxyGate) -> QWidget:
    return cast('QWidget', gate.__dict__['_overlay'])


def test_proxy_gate_dismisses_for_session_without_proxy() -> None:
    app = _qapp()
    content = QWidget()
    gate = ProxyGate(content, compact=True)
    gate.resize(320, 160)
    gate.show()
    app.processEvents()

    gate.set_proxy_enabled(False)
    app.processEvents()

    assert not content.isEnabled()
    assert _overlay(gate).isVisible()

    gate.dismiss_for_session()

    assert content.isEnabled()
    assert not _overlay(gate).isVisible()

    gate.set_proxy_enabled(False)

    assert content.isEnabled()
    assert not _overlay(gate).isVisible()
    assert app is not None
