import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Fleasion.cache import obj_viewer


def test_discard_mesh_display_list_clears_cached_id_and_deletes_in_context(monkeypatch):
    calls = []

    class Viewer:
        mesh_display_list = 42

        def context(self):
            return object()

        def makeCurrent(self):  # noqa: N802
            calls.append(('makeCurrent',))

        def doneCurrent(self):  # noqa: N802
            calls.append(('doneCurrent',))

    monkeypatch.setattr(
        obj_viewer,
        'glDeleteLists',
        lambda display_list, count: calls.append(('glDeleteLists', display_list, count)),
    )

    viewer = Viewer()
    obj_viewer.ObjViewerWidget._discard_mesh_display_list(viewer)

    assert viewer.mesh_display_list == 0
    assert calls == [
        ('makeCurrent',),
        ('glDeleteLists', 42, 1),
        ('doneCurrent',),
    ]


def test_discard_mesh_display_list_clears_cached_id_even_if_delete_fails(monkeypatch):
    class Viewer:
        mesh_display_list = 42

        def context(self):
            return None

    def fail_delete(display_list, count):
        raise RuntimeError('no current OpenGL context')

    monkeypatch.setattr(obj_viewer, 'glDeleteLists', fail_delete)

    viewer = Viewer()
    obj_viewer.ObjViewerWidget._discard_mesh_display_list(viewer)

    assert viewer.mesh_display_list == 0
