import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from Fleasion.gui.proxy_gate import ProxyGate


def _qapp():
    return QApplication.instance() or QApplication([])


def test_proxy_gate_dismisses_for_session_without_proxy():
    app = _qapp()
    content = QWidget()
    gate = ProxyGate(content, compact=True)

    gate.set_proxy_enabled(False)

    assert not content.isEnabled()
    assert gate._overlay.isVisible()

    gate.dismiss_for_session()

    assert content.isEnabled()
    assert not gate._overlay.isVisible()

    gate.set_proxy_enabled(False)

    assert content.isEnabled()
    assert not gate._overlay.isVisible()
    assert app is not None
