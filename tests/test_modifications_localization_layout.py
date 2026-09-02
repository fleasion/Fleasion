import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QPropertyAnimation, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fleasion import localization
from fleasion.gui.modifications_tab import CollapsibleSection, ModificationsTab, ModRowWidget
from fleasion.localization import get_language, set_language, tr
from fleasion.translations.pt import PORTUGUESE

type SourceTuple = tuple[str, str]


_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    app = QApplication.instance()
    _app = cast('QApplication', app) if app is not None else QApplication([])
    return _app


def _translations() -> dict[str, dict[str, str]]:
    return cast('dict[str, dict[str, str]]', localization.__dict__['_TRANSLATIONS'])


def _make_row(
    manager: object, display_name: str, target_path: str, **kwargs: object
) -> ModRowWidget:
    factory = cast('Callable[..., ModRowWidget]', ModRowWidget)
    return factory(manager, display_name, target_path, **kwargs)


def _make_tab(manager: object) -> ModificationsTab:
    factory = cast('Callable[..., ModificationsTab]', ModificationsTab)
    return factory(manager)


def _detect_source(row: ModRowWidget, text: str) -> SourceTuple:
    callback = cast('Callable[[str], SourceTuple]', getattr(row, '_detect_source_from_text'))
    return callback(text)


def _source_edit(row: ModRowWidget) -> QLineEdit:
    return cast('QLineEdit', row.__dict__['_source_edit'])


def _preview_button(row: ModRowWidget) -> QPushButton:
    return cast('QPushButton', row.__dict__['_preview_btn'])


def _update_row_status(row: ModRowWidget, status: str) -> None:
    callback = cast('Callable[[str], None]', getattr(row, '_update_status'))
    callback(status)


def _status_label(row: ModRowWidget) -> QLabel:
    return cast('QLabel', row.__dict__['_status_label'])


def _row_is_font(row: ModRowWidget) -> bool:
    return cast('bool', row.__dict__['_is_font'])


def _row_file_filter(row: ModRowWidget) -> str:
    return cast('str', row.__dict__['_file_filter'])


def _browse_row(row: ModRowWidget) -> None:
    callback = cast('Callable[[], None]', getattr(row, '_on_browse'))
    callback()


def _framerate_value(tab: ModificationsTab) -> int:
    fflag_widget = cast('object', tab.__dict__['_fflag_widget'])
    spinbox = cast('QSpinBox', fflag_widget.__dict__['_framerate_cap'])
    return spinbox.value()


def _section_content(section: CollapsibleSection) -> QWidget:
    return cast('QWidget', section.__dict__['_content'])


def _section_layout(section: CollapsibleSection) -> QVBoxLayout:
    return cast('QVBoxLayout', section.__dict__['_content_layout'])


def _section_animation(section: CollapsibleSection) -> QPropertyAnimation:
    animation = cast('QPropertyAnimation | None', section.__dict__['_animation'])
    assert animation is not None
    return animation


def _header_widget(section: CollapsibleSection) -> QWidget:
    return cast('QWidget', section.__dict__['_header_widget'])


_TRANSLATED_LANGUAGES: list[str] = [
    code
    for code, _name in localization.available_languages()
    if code != localization.DEFAULT_LANGUAGE
]


class _PendingQueue:
    def enqueue_framerate_cap(self, *_args: object) -> None:
        pass

    def enqueue_fast_flags(self, *_args: object) -> None:
        pass


class _FakeModificationManager(QObject):
    entry_status_changed = Signal(str, str, str)
    apply_finished = Signal(str)
    restore_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[dict[str, object]] = []
        self.roblox_dirs: list[Path] = []
        self.fast_flags_enabled: bool = False
        self.fast_flags: dict[str, str] = {}
        self.framerate_cap: int | str = 0
        self.pending_modifications_queue = _PendingQueue()

    def reset_framerate_cap(self) -> None:
        pass

    def sync_saved_global_settings(self) -> None:
        pass

    def write_fast_flags(self, *_args: object) -> None:
        pass


def test_remove_source_accepts_every_registered_language_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _qapp()
    pseudo = dict(localization.ENGLISH)
    pseudo['replacer.action.remove'] = 'Supprimer'
    monkeypatch.setitem(_translations(), 'fr-test', pseudo)

    previous_language = get_language()
    set_language('es')
    row = None
    try:
        row = _make_row(
            _FakeModificationManager(),
            'Prueba',
            'content/textures/example.tex',
        )
        expected = ('bundled', 'bundled:empty.tex')
        portuguese_remove = PORTUGUESE['replacer.action.remove']
        for token in (
            'remove',
            'REMOVE',
            'eliminar',
            'ELIMINAR',
            portuguese_remove,
            portuguese_remove.upper(),
            'supprimer',
            'SUPPRIMER',
        ):
            assert _detect_source(row, token) == expected
        assert _detect_source(row, '"eliminar"') == expected
        assert _detect_source(row, f'"{portuguese_remove}"') == expected
        assert '"eliminar"' in _source_edit(row).placeholderText()
    finally:
        if row is not None:
            row.close()
            row.deleteLater()
        set_language(previous_language)
        app.processEvents()


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_modifications_tab_does_not_clip_visible_controls(language: str) -> None:
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = _make_tab(_FakeModificationManager())
        # Narrow enough to catch the fixed English-width regressions from the
        # skybox/texture rows while still being a usable application width.
        tab.resize(669, 2500)
        tab.show()
        app.processEvents()

        clipped: list[tuple[str, str, int, int]] = []
        for label in tab.findChildren(QLabel):
            if (
                label.isVisible()
                and label.text()
                and not label.wordWrap()
                and label.sizeHint().width() > label.width()
            ):
                clipped.append(('label', label.text(), label.width(), label.sizeHint().width()))

        for button in tab.findChildren(QPushButton):
            if button.isVisible() and button.text() and button.sizeHint().width() > button.width():
                clipped.append(('button', button.text(), button.width(), button.sizeHint().width()))

        for line_edit in tab.findChildren(QLineEdit):
            placeholder = line_edit.placeholderText()
            if not line_edit.isVisible() or not placeholder:
                continue
            required_width = line_edit.fontMetrics().horizontalAdvance(placeholder) + 12
            if required_width > line_edit.width():
                clipped.append(('line edit', placeholder, line_edit.width(), required_width))

        assert clipped == []
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        set_language(previous_language)
        app.processEvents()


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_buttons_fit_content_without_consuming_surplus_width(language: str) -> None:
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = _make_tab(_FakeModificationManager())
        tab.resize(1700, 800)
        tab.show()
        app.processEvents()

        apply_all_text = tr('ui.gui.modifications_tab.apply_to_all_sky_faces')
        apply_all = next(
            button for button in tab.findChildren(QPushButton) if button.text() == apply_all_text
        )
        assert apply_all.width() == max(180, apply_all.sizeHint().width())
        assert apply_all.maximumWidth() == apply_all.width()
        assert apply_all.width() < tab.width() // 2

        row = tab.findChild(ModRowWidget)
        assert row is not None
        assert _preview_button(row).width() == max(82, _preview_button(row).sizeHint().width())
        assert _preview_button(row).maximumWidth() == _preview_button(row).width()
        assert _preview_button(row).width() < 120
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        set_language(previous_language)
        app.processEvents()


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_modification_statuses_resize_when_text_changes(language: str) -> None:
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = _make_tab(_FakeModificationManager())
        tab.resize(669, 2500)
        tab.show()
        app.processEvents()

        row = tab.findChild(ModRowWidget)
        assert row is not None
        for status in ('not_set', 'pending', 'applied', 'orphaned_stash'):
            _update_row_status(row, status)
            app.processEvents()
            assert _status_label(row).sizeHint().width() <= _status_label(row).width()
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        set_language(previous_language)
        app.processEvents()


def test_custom_font_browse_uses_filter_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _qapp()
    tab = _make_tab(_FakeModificationManager())
    captured: dict[str, str] = {}

    def fake_get_open_file_name(
        _parent: object, _caption: object, _initial_dir: object, file_filter: str
    ) -> tuple[str, str]:
        captured['filter'] = file_filter
        return '', ''

    monkeypatch.setattr(
        'fleasion.gui.modifications_tab.QFileDialog.getOpenFileName',
        fake_get_open_file_name,
    )
    try:
        font_row = next(row for row in tab.findChildren(ModRowWidget) if _row_is_font(row))
        assert _row_file_filter(font_row) == 'modifications.filter.font_files'
        _browse_row(font_row)
        assert captured['filter'] == tr('modifications.filter.font_files')
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    'framerate_cap,expected',
    [
        (2**63, 999_999_999),
        (-(2**63), 0),
        ('not-a-number', 0),
    ],
)
def test_framerate_cap_load_clamps_values_before_qspinbox(
    framerate_cap: int | str, expected: int
) -> None:
    app = _qapp()
    manager = _FakeModificationManager()
    manager.framerate_cap = framerate_cap
    tab = None
    try:
        tab = _make_tab(manager)
        assert _framerate_value(tab) == expected
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        app.processEvents()


def test_collapsing_section_clips_content_without_reflowing_children() -> None:
    app = _qapp()
    host = QWidget()
    host.resize(900, 700)
    host_layout = QVBoxLayout(host)
    section = CollapsibleSection('Fast Flags', expanded=True)

    # Model the Fast Flags section: one tall child with a complex internal
    # layout.  Its geometry must stay stable while the outer content viewport
    # closes, otherwise Qt visibly reflows it on every animation frame.
    inner = QWidget()
    inner_layout = QVBoxLayout(inner)
    for index in range(20):
        inner_layout.addWidget(QLabel(f'row {index}'))
    section.add_widget(inner)
    host_layout.addWidget(section)
    host_layout.addStretch()

    host.show()
    app.processEvents()
    initial_inner_height = inner.height()
    initial_content_height = _section_content(section).height()
    assert initial_inner_height > 0
    assert initial_content_height > 0

    section.toggle()
    _section_animation(section).setCurrentTime(90)
    app.processEvents()

    assert 0 < _section_content(section).height() < initial_content_height
    assert inner.height() == initial_inner_height
    assert not _section_layout(section).isEnabled()

    _section_animation(section).setCurrentTime(_section_animation(section).duration())
    app.processEvents()
    assert _section_content(section).height() == 0
    assert _section_layout(section).isEnabled()

    host.close()
    host.deleteLater()
    app.processEvents()


def test_collapsible_header_does_not_absorb_transient_collapse_space() -> None:
    app = _qapp()
    section = CollapsibleSection('Fast Flags', expanded=True)
    inner = QWidget()
    inner.setMinimumHeight(600)
    section.add_widget(inner)

    # Reproduce the ordering that caused the one-frame bounce: the outer
    # section still has its previous tall geometry while the animated content
    # maximum has already shrunk.  The header must not consume that spare
    # height while the parent layout catches up.
    section.resize(900, 700)
    section.show()
    app.processEvents()
    header_y = _header_widget(section).y()
    header_height = _header_widget(section).height()

    layout = section.layout()
    assert layout is not None
    for content_height in (400, 200, 50, 0):
        _section_content(section).setMaximumHeight(content_height)
        layout.activate()
        assert _header_widget(section).y() == header_y
        assert _header_widget(section).height() == header_height

    section.close()
    section.deleteLater()
    app.processEvents()


def test_collapsed_sections_do_not_absorb_parent_spare_height() -> None:
    app = _qapp()
    host = QWidget()
    host.resize(900, 700)
    host_layout = QVBoxLayout(host)
    sections: list[CollapsibleSection] = []
    for index in range(5):
        section = CollapsibleSection(f'Section {index}', expanded=False)
        section.add_widget(QLabel('body'))
        host_layout.addWidget(section)
        sections.append(section)
    # Deliberately use the zero-stretch form that previously let Qt
    # redistribute spare height back into Preferred section widgets.
    host_layout.addStretch()

    host.show()
    app.processEvents()

    for section in sections:
        assert section.height() == section.sizeHint().height()
        assert section.height() < 50

    host.close()
    host.deleteLater()
    app.processEvents()
