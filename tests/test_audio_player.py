import os
import threading
from collections.abc import Callable
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from numpy.typing import NDArray
from PySide6.QtWidgets import QApplication

from fleasion.cache.audio_player import AudioPlayerWidget


_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    app = QApplication.instance()
    _app = cast(QApplication, app) if app is not None else QApplication([])
    return _app


def _play(player: AudioPlayerWidget) -> None:
    callback = cast('Callable[[], None]', getattr(player, '_play'))
    callback()


def _thread(player: AudioPlayerWidget) -> threading.Thread:
    thread = player.playback_thread
    assert thread is not None
    return thread


def _stub_loaded_audio(player: AudioPlayerWidget) -> None:
    player.audio_data = np.zeros((4096, 2), dtype=np.float32)
    player.sample_rate = 44100
    player.duration = len(player.audio_data) / player.sample_rate


def _stub_nonzero_audio(player: AudioPlayerWidget) -> None:
    player.audio_data = np.full((4096, 2), 0.5, dtype=np.float32)
    player.sample_rate = 44100
    player.duration = len(player.audio_data) / player.sample_rate


class RecordingStream:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.close_calls = 0
        self.stop_thread: threading.Thread | None = None
        self.close_thread: threading.Thread | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_calls += 1
        self.stop_thread = threading.current_thread()

    def close(self) -> None:
        self.close_calls += 1
        self.close_thread = threading.current_thread()


def _recording_stream_factory(stream: RecordingStream) -> Callable[..., RecordingStream]:
    def factory(**_kwargs: object) -> RecordingStream:
        return stream

    return factory


def test_stop_does_not_close_stream_on_ui_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, '_load_audio', _stub_loaded_audio)
    player = AudioPlayerWidget('unused')
    stream = RecordingStream()
    stop_event = threading.Event()
    player.stream = stream
    player.stop_event = stop_event

    player.stop()

    assert stop_event.is_set()
    assert stream.stop_calls == 0
    assert stream.close_calls == 0
    player.deleteLater()


def test_playback_worker_closes_stream_after_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, '_load_audio', _stub_loaded_audio)
    stream = RecordingStream()
    monkeypatch.setattr(
        'fleasion.cache.audio_player.sd.OutputStream',
        _recording_stream_factory(stream),
    )
    player = AudioPlayerWidget('unused')

    _play(player)
    player.stop()
    thread = _thread(player)
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert stream.close_thread is not threading.current_thread()
    player.deleteLater()


def test_playback_callback_outputs_nonzero_float32_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, '_load_audio', _stub_nonzero_audio)
    captured: dict[str, object] = {}

    class CallbackStream(RecordingStream):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            self.kwargs: dict[str, object] = kwargs
            captured['kwargs'] = kwargs

        def start(self) -> None:
            callback = cast(
                'Callable[[NDArray[np.float32], int, object, object], None]',
                self.kwargs['callback'],
            )
            channels = cast(int, self.kwargs['channels'])
            outdata: NDArray[np.float32] = np.zeros((128, channels), dtype=np.float32)
            callback(outdata, len(outdata), None, None)
            captured['outdata'] = outdata

    def callback_stream_factory(**kwargs: object) -> CallbackStream:
        return CallbackStream(**kwargs)

    monkeypatch.setattr(
        'fleasion.cache.audio_player.sd.OutputStream',
        callback_stream_factory,
    )
    player = AudioPlayerWidget('unused')

    _play(player)
    player.stop()
    thread = _thread(player)
    thread.join(timeout=1)

    kwargs = cast('dict[str, object]', captured['kwargs'])
    outdata = cast('NDArray[np.float32]', captured['outdata'])
    assert kwargs['dtype'] == 'float32'
    assert outdata.dtype == np.float32
    assert np.any(outdata != 0)
    player.deleteLater()
