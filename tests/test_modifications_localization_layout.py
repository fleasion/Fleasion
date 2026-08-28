import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fleasion import localization
from fleasion.gui.modifications_tab import CollapsibleSection, ModificationsTab, ModRowWidget
from fleasion.localization import get_language, set_language, tr
from fleasion.translations.pt import PORTUGUESE


_APP = None
_TRANSLATED_LANGUAGES = [
    code
    for code, _name in localization.available_languages()
    if code != localization.DEFAULT_LANGUAGE
]


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class _PendingQueue:
    def enqueue_framerate_cap(self, *_args):
        pass

    def enqueue_fast_flags(self, *_args):
        pass


class _FakeModificationManager(QObject):
    entry_status_changed = pyqtSignal(str, str, str)
    apply_finished = pyqtSignal(str)
    restore_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.entries = []
        self.roblox_dirs = []
        self.fast_flags_enabled = False
        self.fast_flags = {}
        self.framerate_cap = 0
        self.pending_modifications_queue = _PendingQueue()

    def reset_framerate_cap(self):
        pass

    def sync_saved_global_settings(self):
        pass

    def write_fast_flags(self, *_args):
        pass


def test_remove_source_accepts_every_registered_language_token(monkeypatch):
    app = _qapp()
    pseudo = dict(localization.ENGLISH)
    pseudo['replacer.action.remove'] = 'Supprimer'
    monkeypatch.setitem(localization._TRANSLATIONS, 'fr-test', pseudo)

    previous_language = get_language()
    set_language('es')
    row = None
    try:
        row = ModRowWidget(
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
            assert row._detect_source_from_text(token) == expected
        assert row._detect_source_from_text('"eliminar"') == expected
        assert row._detect_source_from_text(f'"{portuguese_remove}"') == expected
        assert '"eliminar"' in row._source_edit.placeholderText()
    finally:
        if row is not None:
            row.close()
            row.deleteLater()
        set_language(previous_language)
        app.processEvents()


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_modifications_tab_does_not_clip_visible_controls(language):
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = ModificationsTab(_FakeModificationManager())
        # Narrow enough to catch the fixed English-width regressions from the
        # skybox/texture rows while still being a usable application width.
        tab.resize(669, 2500)
        tab.show()
        app.processEvents()

        clipped = []
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
def test_translated_buttons_fit_content_without_consuming_surplus_width(language):
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = ModificationsTab(_FakeModificationManager())
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
        assert row._preview_btn.width() == max(82, row._preview_btn.sizeHint().width())
        assert row._preview_btn.maximumWidth() == row._preview_btn.width()
        assert row._preview_btn.width() < 120
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        set_language(previous_language)
        app.processEvents()


@pytest.mark.parametrize('language', _TRANSLATED_LANGUAGES)
def test_translated_modification_statuses_resize_when_text_changes(language):
    app = _qapp()
    previous_language = get_language()
    set_language(language)
    tab = None
    try:
        tab = ModificationsTab(_FakeModificationManager())
        tab.resize(669, 2500)
        tab.show()
        app.processEvents()

        row = tab.findChild(ModRowWidget)
        assert row is not None
        for status in ('not_set', 'pending', 'applied', 'orphaned_stash'):
            row._update_status(status)
            app.processEvents()
            assert row._status_label.sizeHint().width() <= row._status_label.width()
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        set_language(previous_language)
        app.processEvents()


def test_custom_font_browse_uses_filter_identifier(monkeypatch):
    app = _qapp()
    tab = ModificationsTab(_FakeModificationManager())
    captured = {}

    def fake_get_open_file_name(_parent, _caption, _initial_dir, file_filter):
        captured['filter'] = file_filter
        return '', ''

    monkeypatch.setattr(
        'fleasion.gui.modifications_tab.QFileDialog.getOpenFileName',
        fake_get_open_file_name,
    )
    try:
        font_row = next(
            row for row in tab.findChildren(ModRowWidget) if row._is_font
        )
        assert font_row._file_filter == 'modifications.filter.font_files'
        font_row._on_browse()
        assert captured['filter'] == tr('modifications.filter.font_files')
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ('framerate_cap', 'expected'),
    [
        (2**63, 999_999_999),
        (-(2**63), 0),
        ('not-a-number', 0),
    ],
)
def test_framerate_cap_load_clamps_values_before_qspinbox(framerate_cap, expected):
    app = _qapp()
    manager = _FakeModificationManager()
    manager.framerate_cap = framerate_cap
    tab = None
    try:
        tab = ModificationsTab(manager)
        assert tab._fflag_widget._framerate_cap.value() == expected
    finally:
        if tab is not None:
            tab.close()
            tab.deleteLater()
        app.processEvents()


def test_collapsing_section_clips_content_without_reflowing_children():
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
    initial_content_height = section._content.height()
    assert initial_inner_height > 0
    assert initial_content_height > 0

    section.toggle()
    wait = QEventLoop()
    QTimer.singleShot(90, wait.quit)
    wait.exec()

    assert 0 < section._content.height() < initial_content_height
    assert inner.height() == initial_inner_height
    assert not section._content_layout.isEnabled()

    wait = QEventLoop()
    QTimer.singleShot(180, wait.quit)
    wait.exec()
    assert section._content.height() == 0
    assert section._content_layout.isEnabled()

    host.close()
    host.deleteLater()
    app.processEvents()


def test_collapsible_header_does_not_absorb_transient_collapse_space():
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
    header_y = section._header_widget.y()
    header_height = section._header_widget.height()

    for content_height in (400, 200, 50, 0):
        section._content.setMaximumHeight(content_height)
        section.layout().activate()
        assert section._header_widget.y() == header_y
        assert section._header_widget.height() == header_height

    section.close()
    section.deleteLater()
    app.processEvents()
