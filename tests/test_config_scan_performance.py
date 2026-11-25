from __future__ import annotations

from operator import itemgetter
from typing import TYPE_CHECKING

from fleasion.config import manager

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_config_scan_matches_pathlib_glob_and_observes_external_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path)
    for name in (
        'normal.json',
        '.json',
        '..json',
        '...json',
        '.hidden.json',
        'mixed.JSON',
        'unicode.jſon',
        'ignored.txt',
    ):
        (tmp_path / name).write_text(name, encoding='utf-8')
    (tmp_path / 'directory.json').mkdir()
    try:
        (tmp_path / 'broken.json').symlink_to(tmp_path / 'missing')
    except OSError:
        pass

    def expected() -> tuple[list[str], tuple[tuple[str, int, int], ...]]:
        entries: list[tuple[str, str, int, int]] = []
        for path in tmp_path.glob('*.json'):
            try:
                info = path.stat()
            except OSError:
                continue
            entries.append((path.stem, path.name, info.st_mtime_ns, info.st_size))
        entries.sort(key=itemgetter(0))
        return [name for name, *_ in entries], tuple(
            (filename, mtime, size) for _, filename, mtime, size in entries
        )

    assert manager.ConfigManager._scan_config_files() == expected()  # pyright: ignore[reportPrivateUsage]
    (tmp_path / 'normal.json').unlink()
    (tmp_path / 'new.json').write_text('new', encoding='utf-8')
    assert manager.ConfigManager._scan_config_files() == expected()  # pyright: ignore[reportPrivateUsage]
