from PyQt6.QtCore import QDir, QMimeData, QUrl

from Fleasion.gui.file_drop import local_file_path_from_mime_data
from Fleasion.gui.modifications_tab import _relative_target_path_for_resource_file


def test_local_file_path_from_mime_data_uses_native_path(tmp_path):
    dropped_file = tmp_path / 'dropped file.txt'
    dropped_file.write_text('content', encoding='utf-8')

    mime_data = QMimeData()
    mime_data.setUrls([QUrl('https://example.com/file.txt'), QUrl.fromLocalFile(str(dropped_file))])

    assert local_file_path_from_mime_data(mime_data) == QDir.toNativeSeparators(str(dropped_file))


def test_local_file_path_from_mime_data_ignores_non_local_urls():
    mime_data = QMimeData()
    mime_data.setUrls([QUrl('https://example.com/file.txt')])

    assert local_file_path_from_mime_data(mime_data) is None


def test_relative_target_path_for_resource_file_requires_known_roblox_root(tmp_path):
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    target = resources / 'content' / 'sounds' / 'oof.ogg'
    target.parent.mkdir(parents=True)
    target.write_text('content', encoding='utf-8')
    outside = tmp_path / 'outside.ogg'
    outside.write_text('content', encoding='utf-8')

    assert _relative_target_path_for_resource_file(target, [resources]) == 'content/sounds/oof.ogg'
    assert _relative_target_path_for_resource_file(outside, [resources]) is None
