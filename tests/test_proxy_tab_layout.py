import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QPushButton

from fleasion.gui.proxy_tab import ProxyTrafficTab


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_proxy_action_buttons_share_native_height():
    app = _qapp()
    tab = ProxyTrafficTab(None)
    tab.show()
    app.processEvents()

    buttons = []
    for index in range(tab.ui.horizontalLayout_3.count()):
        widget = tab.ui.horizontalLayout_3.itemAt(index).widget()
        if isinstance(widget, QPushButton):
            buttons.append(widget)

    assert len(buttons) == 5
    assert {tab.ui.clearButton, tab.ui.autoReplace, tab.ui.forwardButton, tab.ui.dropButton} < set(buttons)
    # Do not clamp button height.  Native Qt styles (especially Windows) can
    # paint menu-bearing and plain buttons differently when forced to 22 px.
    # Let the style choose one native row height, as elsewhere in the GUI.
    assert all(button.minimumHeight() == 0 for button in buttons)
    assert all(button.maximumHeight() == 16777215 for button in buttons)
    assert len({button.height() for button in buttons}) == 1

    tab.close()
    tab.deleteLater()
    app.processEvents()
