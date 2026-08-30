from collections.abc import Callable
from typing import cast

import pytest

from fleasion.cache.animation_viewer import AnimationGLWidget


def _release_display_lists(widget: object) -> None:
    callback = cast(
        'Callable[[object], None]',
        AnimationGLWidget.__dict__['release_display_lists'],
    )
    callback(widget)


class _DisplayListWidget:
    def __init__(self) -> None:
        self.display_lists = {'torso': 17, 'head': 23}
        self.grid_display_list = 0
        self.made_current = False
        self.done_current = False

    def context(self):
        return object()

    def makeCurrent(self) -> None:  # noqa: N802 - mirrors Qt's API
        self.made_current = True

    def doneCurrent(self) -> None:  # noqa: N802 - mirrors Qt's API
        self.done_current = True


def test_release_display_lists_deletes_every_cached_list(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[tuple[int, int]] = []

    def delete_lists(display_list: int, count: int) -> None:
        deleted.append((display_list, count))

    monkeypatch.setattr('fleasion.cache.animation_viewer.glDeleteLists', delete_lists)
    widget = _DisplayListWidget()

    _release_display_lists(widget)

    assert deleted == [(17, 1), (23, 1)]
    assert widget.display_lists == {}
    assert widget.grid_display_list == 0
    assert widget.made_current
    assert widget.done_current
