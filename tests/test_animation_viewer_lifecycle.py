from fleasion.cache.animation_viewer import AnimationGLWidget


class _DisplayListWidget:
    def __init__(self):
        self.display_lists = {'torso': 17, 'head': 23}
        self.grid_display_list = 0
        self.made_current = False
        self.done_current = False

    def context(self):
        return object()

    def makeCurrent(self):  # noqa: N802 - mirrors Qt's API
        self.made_current = True

    def doneCurrent(self):  # noqa: N802 - mirrors Qt's API
        self.done_current = True


def test_release_display_lists_deletes_every_cached_list(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        'fleasion.cache.animation_viewer.glDeleteLists',
        lambda display_list, count: deleted.append((display_list, count)),
    )
    widget = _DisplayListWidget()

    AnimationGLWidget.release_display_lists(widget)

    assert deleted == [(17, 1), (23, 1)]
    assert widget.display_lists == {}
    assert widget.grid_display_list == 0
    assert widget.made_current
    assert widget.done_current
