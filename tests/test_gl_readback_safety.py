"""OpenGL diagnostic sampling must not touch unresolved multisampled buffers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from OpenGL import GL

from fleasion.cache.gl_format import multisampled_readback_skip_reason


@pytest.mark.parametrize('samples', [2, 4, 8])
def test_skip_multisample_pixel_readback(
    monkeypatch: pytest.MonkeyPatch, samples: int
) -> None:
    diagnostic_readback = MagicMock(side_effect=AssertionError('glReadPixels called'))
    def query_samples(_pname: int) -> int:
        return samples

    monkeypatch.setattr(GL, 'glGetIntegerv', query_samples)
    monkeypatch.setattr(GL, 'glReadPixels', diagnostic_readback)
    assert multisampled_readback_skip_reason() == (
        f'readback_skipped=multisampled_fbo(samples={samples})'
    )
    diagnostic_readback.assert_not_called()


def test_single_sample_readback_remains_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def query_samples(_pname: int) -> int:
        return 0

    monkeypatch.setattr(GL, 'glGetIntegerv', query_samples)
    assert multisampled_readback_skip_reason() is None



def test_both_viewers_skip_invalid_multisample_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import cast

    from fleasion.cache import animation_viewer, obj_viewer
    from fleasion.cache.animation_viewer import AnimationGLWidget
    from fleasion.cache.obj_viewer import ObjViewerWidget

    reason = 'readback_skipped=multisampled_fbo(samples=4)'

    class ObjStub:
        def _center_pixel_description(self) -> str:
            raise AssertionError('OBJ readback attempted on multisampled FBO')

    class AnimationStub:
        def _read_center_pixel_values(self) -> object:
            raise AssertionError('Animation readback attempted on multisampled FBO')

    monkeypatch.setattr(obj_viewer, 'multisampled_readback_skip_reason', lambda: reason)
    monkeypatch.setattr(animation_viewer, 'multisampled_readback_skip_reason', lambda: reason)
    assert getattr(ObjViewerWidget, '_sample_center_pixel')(cast('ObjViewerWidget', ObjStub())) == reason
    assert getattr(AnimationGLWidget, '_sample_center_pixel')(cast('AnimationGLWidget', AnimationStub())) == reason
