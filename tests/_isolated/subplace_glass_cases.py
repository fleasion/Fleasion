"""Subplace card dimensions must accommodate Glass padding and theme changes."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fleasion.gui.subplace_joiner_tab import SubplaceGameCard, SubplaceJoinerTab
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    before = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    effective_before = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    try:
        yield application
    finally:
        ThemeManager.apply_theme(before)
        ThemeManager._effective_theme = effective_before  # pyright: ignore[reportPrivateUsage]
        application.processEvents()


@pytest.mark.parametrize('theme', ['System', 'Glass', 'Dark', 'Light'])
def test_subplace_card_controls_never_clip_on_theme_changes(
    app: QApplication, theme: str
) -> None:
    ThemeManager.apply_theme(theme)
    card = SubplaceGameCard()
    card.set_data('Test Place', created='today', updated='yesterday')
    card.show()
    try:
        QTest.qWait(50)
        for next_theme in (theme, 'Glass', 'System'):
            ThemeManager.apply_theme(next_theme)
            QTest.qWait(45)
            for control in (card.job_id_edit, card.join_btn, card.open_btn, card.fetch_jobs_btn):
                assert control.height() >= control.minimumSizeHint().height()
                assert control.width() >= control.minimumSizeHint().width()
                assert control.geometry().right() < card.width()
                assert control.geometry().bottom() < card.height()
            assert card.height() >= card.sizeHint().height()
    finally:
        card.close()
        card.deleteLater()
        app.processEvents()


def test_subplace_grid_handles_no_cards_and_card_minimum_width(app: QApplication) -> None:
    ThemeManager.apply_theme('Glass')
    tab = SubplaceJoinerTab(defer_setup=True)
    for _ in tab._build_ui():  # pyright: ignore[reportPrivateUsage]
        pass
    tab.resize(810, 670)
    tab.show()
    try:
        app.processEvents()
        assert tab._get_cols() >= 1  # pyright: ignore[reportPrivateUsage]
        card = SubplaceGameCard(tab.results_container)
        tab._cards.append(card)  # pyright: ignore[reportPrivateUsage]
        tab._place_cards([card])  # pyright: ignore[reportPrivateUsage]
        QTest.qWait(50)
        assert tab._get_cols() >= 1  # pyright: ignore[reportPrivateUsage]
        assert card.minimumWidth() >= (
            card.join_btn.sizeHint().width()
            + card.open_btn.sizeHint().width()
            + card.fetch_jobs_btn.sizeHint().width()
            + 8
            + 14
        )
        assert card.job_id_edit.height() >= card.job_id_edit.minimumSizeHint().height()
        tab.resize(1150, 700)
        QTest.qWait(50)
        assert tab._get_cols() >= 1  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


def test_subplace_buttons_fit_sidebar_and_cards_never_change_size(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QPushButton

    def use_local_names(
        _tab: SubplaceJoinerTab, _place_id: str, callback: Callable[[str], object]
    ) -> None:
        callback('The Backrooms with an extremely long name & description')

    monkeypatch.setattr(SubplaceJoinerTab, '_fetch_place_name', use_local_names)
    ThemeManager.apply_theme('Dark')
    tab = SubplaceJoinerTab(defer_setup=True)
    for _ in tab._build_ui():  # pyright: ignore[reportPrivateUsage]
        pass
    tab.recent_ids = [str(i) for i in range(15)]
    tab.favorites = ['16', '17', '18']
    tab._rebuild_recent_buttons()  # pyright: ignore[reportPrivateUsage]
    tab._rebuild_favorite_buttons()  # pyright: ignore[reportPrivateUsage]
    cards = [SubplaceGameCard(tab.results_container) for _ in range(5)]
    for i, card in enumerate(cards):
        card.set_data(f'A long example subplace name {i}', 'a month ago', 'yesterday')
    tab._cards.extend(cards)  # pyright: ignore[reportPrivateUsage]
    tab._place_cards(cards)  # pyright: ignore[reportPrivateUsage]
    tab.resize(950, 770)
    tab.show()
    try:
        for theme in ('Dark', 'Glass', 'Dark', 'Glass', 'System'):
            ThemeManager.apply_theme(theme)
            for width in (950, 1370, 730, 950):
                tab.resize(width, 770)
                QTest.qWait(85)
                for scroll, contents in (
                    (tab._recent_scroll, tab.recent_contents),  # pyright: ignore[reportPrivateUsage]
                    (tab._fav_scroll, tab.fav_contents),  # pyright: ignore[reportPrivateUsage]
                ):
                    viewport_width = scroll.viewport().width()
                    assert contents.width() <= viewport_width
                    for button in contents.findChildren(QPushButton):
                        assert button.x() >= 6
                        assert button.geometry().right() < viewport_width - 4
                        assert button.toolTip().startswith('The Backrooms')
                for card in cards:
                    assert card.width() == card.minimumWidth() == card.maximumWidth() == 286
                    assert card.height() == card.minimumHeight() == card.maximumHeight() == 324
                    for control in (
                        card.job_id_edit,
                        card.join_btn,
                        card.open_btn,
                        card.fetch_jobs_btn,
                    ):
                        assert control.geometry().right() < card.width()
                        assert control.geometry().bottom() < card.height()
                assert tab._last_cols == tab._get_cols()  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


def test_subplace_grid_shows_horizontal_scroll_when_narrow(app: QApplication) -> None:
    ThemeManager.apply_theme('Glass')
    tab = SubplaceJoinerTab(defer_setup=True)
    for _ in tab._build_ui():  # pyright: ignore[reportPrivateUsage]
        pass
    tab.resize(470, 620)
    card = SubplaceGameCard(tab.results_container)
    tab._cards.append(card)  # pyright: ignore[reportPrivateUsage]
    tab._place_cards([card])  # pyright: ignore[reportPrivateUsage]
    tab.show()
    try:
        QTest.qWait(100)
        assert tab.results_scroll.horizontalScrollBar().maximum() > 0
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


def test_deferred_sidebar_and_card_updates_stop_when_widgets_are_deleted(
    app: QApplication, capfd: pytest.CaptureFixture[str]
) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    from fleasion.gui.subplace_joiner_tab import _PlaceIdButton  # pyright: ignore[reportPrivateUsage]

    ThemeManager.apply_theme('Glass')
    for _ in range(30):
        button = _PlaceIdButton('123')
        button.set_display_name('A very long Roblox place title')
        QCoreApplication.sendEvent(button, QEvent(QEvent.Type.StyleChange))
        assert button._relabel_timer.isActive()  # pyright: ignore[reportPrivateUsage]
        button.deleteLater()
        card = SubplaceGameCard()
        QCoreApplication.sendEvent(card, QEvent(QEvent.Type.StyleChange))
        assert card._restyle_timer.isActive()  # pyright: ignore[reportPrivateUsage]
        card.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(button)
        assert not isValid(card)
    QTest.qWait(40)
    assert 'RuntimeError' not in capfd.readouterr().err
