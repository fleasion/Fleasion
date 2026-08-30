from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDir, QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QLineEdit

from fleasion.gui.file_drop import FileDropLineEdit, local_file_path_from_mime_data
from fleasion.gui import modifications_tab as modifications_tab_module


def _relative_target_path_for_resource_file(path: Path, roblox_dirs: list[Path]) -> str | None:
    callback = cast(
        'Callable[[Path, list[Path]], str | None]',
        modifications_tab_module.__dict__['_relative_target_path_for_resource_file'],
    )
    return callback(path, roblox_dirs)


def test_local_file_path_from_mime_data_uses_native_path(tmp_path: Path) -> None:
    dropped_file = tmp_path / 'dropped file.txt'
    dropped_file.write_text('content', encoding='utf-8')

    mime_data = QMimeData()
    mime_data.setUrls([QUrl('https://example.com/file.txt'), QUrl.fromLocalFile(str(dropped_file))])

    assert local_file_path_from_mime_data(mime_data) == QDir.toNativeSeparators(str(dropped_file))


def test_local_file_path_from_mime_data_ignores_non_local_urls() -> None:
    mime_data = QMimeData()
    mime_data.setUrls([QUrl('https://example.com/file.txt')])

    assert local_file_path_from_mime_data(mime_data) is None


def test_file_drop_line_edit_keeps_cooperative_mro_initialization() -> None:
    app = QApplication.instance()
    qapp = cast(QApplication, app) if app is not None else QApplication([])

    class TrackedLineEdit(QLineEdit):
        tracked_init: bool

        def __init__(self) -> None:
            self.tracked_init = True
            super().__init__()

    class CombinedLineEdit(FileDropLineEdit, TrackedLineEdit):
        pass

    widget = CombinedLineEdit()
    try:
        assert widget.tracked_init
        assert widget.acceptDrops()
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_relative_target_path_for_resource_file_requires_known_roblox_root(tmp_path: Path) -> None:
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    target = resources / 'content' / 'sounds' / 'oof.ogg'
    target.parent.mkdir(parents=True)
    target.write_text('content', encoding='utf-8')
    outside = tmp_path / 'outside.ogg'
    outside.write_text('content', encoding='utf-8')

    assert _relative_target_path_for_resource_file(target, [resources]) == 'content/sounds/oof.ogg'
    assert _relative_target_path_for_resource_file(outside, [resources]) is None
