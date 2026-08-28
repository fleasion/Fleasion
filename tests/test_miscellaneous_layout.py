import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QEventLoop, QPoint, QTimer
from PyQt6.QtWidgets import QApplication, QGroupBox

import fleasion.gui.rando_stuff_tab as rando
from fleasion.gui.modifications_tab import CollapsibleSection


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _build_tab(monkeypatch):
    monkeypatch.setattr(
        rando,
        '_load_accounts',
        lambda: [
            {'username': 'GullibleProkiller1', 'cookie': ''},
            {'username': 'KeepItComingBack0', 'cookie': ''},
        ],
    )
    monkeypatch.setattr(rando.RandoStuffTab, '_check_cookies_on_boot', lambda self: None)
    monkeypatch.setattr(rando.RandoStuffTab, '_resolve_current_user', lambda self: None)
    tab = rando.RandoStuffTab()
    tab.resize(1200, 800)
    tab.show()
    _qapp().processEvents()
    return tab


def test_miscellaneous_uses_modifications_style_sections(monkeypatch):
    _qapp()
    tab = _build_tab(monkeypatch)

    sections = (
        tab._rejoin_section,
        tab._multi_instance_section,
        tab._account_manager_section,
        tab._username_spoofer_section,
        tab._animation_converter_section,
        tab._subplace_blacklist_section,
    )
    assert all(isinstance(section, CollapsibleSection) for section in sections)
    assert not tab.findChildren(QGroupBox)

    # Proxy-only utilities keep the same gates, but those gates now wrap the
    # matching collapsible card rather than a differently styled QGroupBox.
    assert tab._rejoin_proxy_gate._content is tab._rejoin_section
    assert tab._username_spoofer_proxy_gate._content is tab._username_spoofer_section
    assert tab._subplace_blacklist_proxy_gate._content is tab._subplace_blacklist_section

    tab.close()
    tab.deleteLater()
    _qapp().processEvents()


def test_account_manager_is_compact_two_column_layout(monkeypatch):
    _qapp()
    tab = _build_tab(monkeypatch)
    section = tab._account_manager_section

    list_pos = tab._account_list.mapTo(section, QPoint(0, 0))
    details_pos = tab._selected_label.mapTo(section, QPoint(0, 0))

    assert tab._account_list.count() == 2
    assert details_pos.x() > list_pos.x() + tab._account_list.width()
    assert 180 <= tab._account_list.width() <= 300
    assert tab._private_server_input.width() > tab._account_list.width()

    # The card should stay near its content size instead of consuming the
    # remainder of the tab just because QListWidget has an Expanding policy.
    assert section.height() < 350

    tab.close()
    tab.deleteLater()
    _qapp().processEvents()


def test_collapsed_misc_sections_shrink_to_header_height(monkeypatch):
    app = _qapp()
    tab = _build_tab(monkeypatch)
    tab.resize(1450, 1000)
    app.processEvents()

    sections = (
        tab._rejoin_section,
        tab._multi_instance_section,
        tab._account_manager_section,
        tab._username_spoofer_section,
        tab._animation_converter_section,
    )
    for section in sections:
        section.toggle()

    wait = QEventLoop()
    QTimer.singleShot(260, wait.quit)
    wait.exec()
    app.processEvents()

    # A collapsed card must occupy only its header/outline size.  In the buggy
    # layout, the viewport's spare height was redistributed evenly across all
    # Preferred cards, leaving ~130 px empty bodies under 37 px headers.
    for section in sections:
        assert section._content.height() == 0
        assert section.height() == section.sizeHint().height()
        assert section.height() < 50

    tab.close()
    tab.deleteLater()
    app.processEvents()
