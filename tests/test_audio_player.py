import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication

from Fleasion.cache.audio_player import AudioPlayerWidget


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _stub_loaded_audio(player):
    player.audio_data = np.zeros((4096, 2), dtype=np.float32)
    player.sample_rate = 44100
    player.duration = len(player.audio_data) / player.sample_rate


def _stub_nonzero_audio(player):
    player.audio_data = np.full((4096, 2), 0.5, dtype=np.float32)
    player.sample_rate = 44100
    player.duration = len(player.audio_data) / player.sample_rate


class RecordingStream:
    def __init__(self):
        self.stop_calls = 0
        self.close_calls = 0
        self.stop_thread = None
        self.close_thread = None

    def start(self):
        pass

    def stop(self):
        self.stop_calls += 1
        self.stop_thread = threading.current_thread()

    def close(self):
        self.close_calls += 1
        self.close_thread = threading.current_thread()


def test_stop_does_not_close_stream_on_ui_thread(monkeypatch):
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, "_load_audio", _stub_loaded_audio)
    player = AudioPlayerWidget("unused")
    stream = RecordingStream()
    stop_event = threading.Event()
    player.stream = stream
    player.stop_event = stop_event

    player.stop()

    assert stop_event.is_set()
    assert stream.stop_calls == 0
    assert stream.close_calls == 0
    player.deleteLater()


def test_playback_worker_closes_stream_after_stop(monkeypatch):
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, "_load_audio", _stub_loaded_audio)
    stream = RecordingStream()
    monkeypatch.setattr(
        "Fleasion.cache.audio_player.sd.OutputStream",
        lambda **kwargs: stream,
    )
    player = AudioPlayerWidget("unused")

    player._play()
    player.stop()
    player.playback_thread.join(timeout=1)

    assert not player.playback_thread.is_alive()
    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert stream.close_thread is not threading.current_thread()
    player.deleteLater()


def test_playback_callback_outputs_nonzero_float32_audio(monkeypatch):
    _qapp()
    monkeypatch.setattr(AudioPlayerWidget, "_load_audio", _stub_nonzero_audio)
    captured = {}

    class CallbackStream(RecordingStream):
        def __init__(self, **kwargs):
            super().__init__()
            self.kwargs = kwargs
            captured["kwargs"] = kwargs

        def start(self):
            callback = self.kwargs["callback"]
            outdata = np.zeros((128, self.kwargs["channels"]), dtype=np.float32)
            callback(outdata, len(outdata), None, None)
            captured["outdata"] = outdata

    monkeypatch.setattr(
        "Fleasion.cache.audio_player.sd.OutputStream",
        lambda **kwargs: CallbackStream(**kwargs),
    )
    player = AudioPlayerWidget("unused")

    player._play()
    player.stop()
    player.playback_thread.join(timeout=1)

    assert captured["kwargs"]["dtype"] == "float32"
    assert captured["outdata"].dtype == np.float32
    assert np.any(captured["outdata"] != 0)
    player.deleteLater()
