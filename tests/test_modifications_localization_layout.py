import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from fleasion import localization
from fleasion.gui.modifications_tab import ModificationsTab, ModRowWidget
from fleasion.localization import get_language, set_language, tr


_APP = None


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
        for token in ('remove', 'REMOVE', 'eliminar', 'ELIMINAR', 'supprimer', 'SUPPRIMER'):
            assert row._detect_source_from_text(token) == expected
        assert row._detect_source_from_text('"eliminar"') == expected
        assert '"eliminar"' in row._source_edit.placeholderText()
    finally:
        if row is not None:
            row.close()
            row.deleteLater()
        set_language(previous_language)
        app.processEvents()


def test_spanish_modifications_tab_does_not_clip_visible_controls():
    app = _qapp()
    previous_language = get_language()
    set_language('es')
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


def test_spanish_buttons_fit_content_without_consuming_surplus_width():
    app = _qapp()
    previous_language = get_language()
    set_language('es')
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


def test_spanish_modification_statuses_resize_when_text_changes():
    app = _qapp()
    previous_language = get_language()
    set_language('es')
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
