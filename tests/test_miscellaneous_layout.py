import os
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QPropertyAnimation
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QLineEdit, QListWidget, QWidget

import fleasion.gui.rando_stuff_tab as rando
from fleasion.gui.modifications_tab import CollapsibleSection

_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    app = QApplication.instance()
    _app = cast('QApplication', app) if app is not None else QApplication([])
    return _app


def _load_accounts() -> list[dict[str, str]]:
    return [
        {'username': 'GullibleProkiller1', 'cookie': ''},
        {'username': 'KeepItComingBack0', 'cookie': ''},
    ]


def _no_cookie_check(_self: rando.RandoStuffTab) -> None:
    return None


def _no_user_resolution(_self: rando.RandoStuffTab) -> None:
    return None


def _section(tab: rando.RandoStuffTab, name: str) -> CollapsibleSection:
    return cast('CollapsibleSection', tab.__dict__[name])


def _section_content(section: CollapsibleSection) -> QWidget:
    return cast('QWidget', section.__dict__['_content'])


def _section_animation(section: CollapsibleSection) -> QPropertyAnimation:
    animation = cast('QPropertyAnimation | None', section.__dict__['_animation'])
    assert animation is not None
    return animation


def _gate_content(tab: rando.RandoStuffTab, name: str) -> QWidget:
    gate = cast('object', tab.__dict__[name])
    return cast('QWidget', gate.__dict__['_content'])


def _account_list(tab: rando.RandoStuffTab) -> QListWidget:
    return cast('QListWidget', tab.__dict__['_account_list'])


def _selected_label(tab: rando.RandoStuffTab) -> QLabel:
    return cast('QLabel', tab.__dict__['_selected_label'])


def _private_server_input(tab: rando.RandoStuffTab) -> QLineEdit:
    return cast('QLineEdit', tab.__dict__['_private_server_input'])


def _build_tab(monkeypatch: pytest.MonkeyPatch) -> rando.RandoStuffTab:
    monkeypatch.setattr(rando, '_load_accounts', _load_accounts)
    monkeypatch.setattr(rando.RandoStuffTab, '_check_cookies_on_boot', _no_cookie_check)
    monkeypatch.setattr(rando.RandoStuffTab, '_resolve_current_user', _no_user_resolution)
    tab = rando.RandoStuffTab()
    tab.resize(1200, 800)
    tab.show()
    _qapp().processEvents()
    return tab


def test_miscellaneous_uses_modifications_style_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    tab = _build_tab(monkeypatch)

    sections = (
        _section(tab, '_rejoin_section'),
        _section(tab, '_multi_instance_section'),
        _section(tab, '_account_manager_section'),
        _section(tab, '_username_spoofer_section'),
        _section(tab, '_animation_converter_section'),
        _section(tab, '_subplace_blacklist_section'),
    )
    assert all(isinstance(section, CollapsibleSection) for section in sections)
    assert not tab.findChildren(QGroupBox)

    # Proxy-only utilities keep the same gates, but those gates now wrap the
    # matching collapsible card rather than a differently styled QGroupBox.
    assert _gate_content(tab, '_rejoin_proxy_gate') is _section(tab, '_rejoin_section')
    assert _gate_content(tab, '_username_spoofer_proxy_gate') is _section(
        tab, '_username_spoofer_section'
    )
    assert _gate_content(tab, '_subplace_blacklist_proxy_gate') is _section(
        tab, '_subplace_blacklist_section'
    )

    tab.close()
    tab.deleteLater()
    _qapp().processEvents()


def test_account_manager_is_compact_two_column_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    tab = _build_tab(monkeypatch)
    section = _section(tab, '_account_manager_section')

    list_pos = _account_list(tab).mapTo(section, QPoint(0, 0))
    details_pos = _selected_label(tab).mapTo(section, QPoint(0, 0))

    assert _account_list(tab).count() == 2
    assert details_pos.x() > list_pos.x() + _account_list(tab).width()
    assert 180 <= _account_list(tab).width() <= 300
    assert _private_server_input(tab).width() > _account_list(tab).width()

    # The card should stay near its content size instead of consuming the
    # remainder of the tab just because QListWidget has an Expanding policy.
    assert section.height() < 350

    tab.close()
    tab.deleteLater()
    _qapp().processEvents()


def test_collapsed_misc_sections_shrink_to_header_height(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _qapp()
    tab = _build_tab(monkeypatch)
    tab.resize(1450, 1000)
    app.processEvents()

    sections = (
        _section(tab, '_rejoin_section'),
        _section(tab, '_multi_instance_section'),
        _section(tab, '_account_manager_section'),
        _section(tab, '_username_spoofer_section'),
        _section(tab, '_animation_converter_section'),
    )
    for section in sections:
        section.toggle()

    for section in sections:
        animation = _section_animation(section)
        animation.setCurrentTime(animation.duration())
    app.processEvents()

    # A collapsed card must occupy only its header/outline size.  In the buggy
    # layout, the viewport's spare height was redistributed evenly across all
    # Preferred cards, leaving ~130 px empty bodies under 37 px headers.
    for section in sections:
        assert _section_content(section).height() == 0
        assert section.height() == section.sizeHint().height()
        assert section.height() < 50

    tab.close()
    tab.deleteLater()
    app.processEvents()
