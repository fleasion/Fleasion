import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication, QDialog, QTextEdit

from fleasion.gui.delete_cache import DeleteCacheWindow


@pytest.fixture(scope='module')
def qt_app():
    app = QApplication.instance() or QApplication([])
    return app


def test_delete_cache_window_closes_after_done(qt_app, monkeypatch):
    dialog = DeleteCacheWindow.__new__(DeleteCacheWindow)
    QDialog.__init__(dialog)
    dialog.status_text = QTextEdit()

    scheduled = {}
    monkeypatch.setattr(
        'fleasion.gui.delete_cache.QTimer.singleShot',
        lambda delay, callback: scheduled.update(delay=delay, callback=callback),
    )
    accepted = []
    monkeypatch.setattr(dialog, 'accept', lambda: accepted.append(True))

    dialog._on_done()

    assert dialog.status_text.toPlainText().endswith('Done.')
    assert scheduled['delay'] == DeleteCacheWindow._CLOSE_AFTER_DONE_MS
    scheduled['callback']()
    assert accepted == [True]
    dialog.deleteLater()
    qt_app.processEvents()
