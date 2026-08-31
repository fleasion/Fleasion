"""Audio player widget using sounddevice for Python 3.14 compatibility."""

from __future__ import annotations

import ctypes.util
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, override

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent
from fleasion.utils import log_buffer

type AudioArray = NDArray[np.float32]
type AudioCallback = Callable[[AudioArray, int, object, object], None]


class _OutputStreamLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class _OutputStreamFactory(Protocol):
    def __call__(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: AudioCallback,
        blocksize: int,
    ) -> _OutputStreamLike: ...


class _SoundDeviceDefault(Protocol):
    device: object


class _SoundDeviceModule(Protocol):
    OutputStream: _OutputStreamFactory
    PortAudioError: type[Exception]
    default: _SoundDeviceDefault

    def query_devices(self) -> Sequence[object]: ...
    def get_portaudio_version(self) -> object: ...


class _SoundFileModule(Protocol):
    LibsndfileError: type[Exception]

    def read(self, path: str, *, dtype: str) -> tuple[AudioArray, int]: ...


if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent

    sf: _SoundFileModule

    def _import_sounddevice_runtime() -> _SoundDeviceModule: ...

    def _audio_data(value: AudioArray | None) -> AudioArray: ...

    def _sample_rate(value: int | None) -> int: ...

    def _config_audio_volume(config: object) -> int: ...

    def _set_config_audio_volume(config: object, value: int) -> None: ...
else:
    import importlib

    sf = importlib.import_module('soundfile')

    def _import_sounddevice_runtime() -> _SoundDeviceModule:
        return importlib.import_module('sounddevice')

    def _audio_data(value: AudioArray | None) -> AudioArray:
        return value

    def _sample_rate(value: int | None) -> int:
        return value

    def _config_audio_volume(config: object) -> int:
        return config.audio_volume

    def _set_config_audio_volume(config: object, value: int) -> None:
        config.audio_volume = value


_LINUX_LIBRARY_SEARCH_DIRS = (
    '/lib64',
    '/usr/lib64',
    '/lib/x86_64-linux-gnu',
    '/usr/lib/x86_64-linux-gnu',
    '/lib',
    '/usr/lib',
    '/usr/local/lib',
)


def _resolve_library_path(
    library_name: str, search_dirs: Iterable[str] | None = None
) -> Path | None:
    """Resolve a system shared library to an absolute path when possible."""
    resolved = ctypes.util.find_library(library_name)
    if not resolved:
        return None

    candidate = Path(resolved)
    if candidate.is_file():
        return candidate
    if Path(resolved).parent != Path():
        return None

    for search_dir in search_dirs or _LINUX_LIBRARY_SEARCH_DIRS:
        candidate = Path(search_dir) / resolved
        if candidate.is_file():
            return candidate
    return None


def _bundled_portaudio_path() -> Path | None:
    """Return PyInstaller's bundled PortAudio library, when available."""
    meipass = getattr(sys, '_MEIPASS', None)
    if not meipass:
        return None

    root = Path(meipass)
    for relative_path in (
        'libportaudio.so.2',
        'libportaudio.so',
        '_internal/libportaudio.so.2',
        '_internal/libportaudio.so',
    ):
        candidate = root / relative_path
        if candidate.is_file():
            return candidate
    return None


def _preferred_portaudio_path() -> Path | None:
    """Prefer the host audio stack; use bundled PortAudio only as fallback."""
    if not sys.platform.startswith('linux'):
        return None
    return _resolve_library_path('portaudio') or _bundled_portaudio_path()


def _import_sounddevice_with_preferred_portaudio() -> _SoundDeviceModule:
    """Import sounddevice with a deterministic PortAudio path on Linux."""
    original_find_library = ctypes.util.find_library
    preferred_portaudio = _preferred_portaudio_path()

    def find_library(name: str) -> str | None:
        if name == 'portaudio' and preferred_portaudio is not None:
            return str(preferred_portaudio)
        return original_find_library(name)

    if preferred_portaudio is not None:
        ctypes.util.find_library = find_library
    try:
        sounddevice = _import_sounddevice_runtime()
    finally:
        ctypes.util.find_library = original_find_library

    return sounddevice


sd = _import_sounddevice_with_preferred_portaudio()
_audio_backend_logged = threading.Event()


def _audio_diagnostic_value(callback: Callable[[], object]) -> object:
    try:
        return callback()
    except (OSError, RuntimeError, sd.PortAudioError) as exc:
        return f'unavailable ({type(exc).__name__}: {exc})'


def _log_audio_backend_once() -> None:
    """Log the PortAudio backend selected by the GUI player once per process."""
    if _audio_backend_logged.is_set():
        return
    _audio_backend_logged.set()

    preferred = _audio_diagnostic_value(_preferred_portaudio_path)
    loaded = getattr(sd, '_libname', None)
    version = getattr(sd, '__version__', 'unknown')
    portaudio_version = _audio_diagnostic_value(sd.get_portaudio_version)
    default_device = _audio_diagnostic_value(lambda: sd.default.device)
    device_count = _audio_diagnostic_value(lambda: len(sd.query_devices()))
    log_buffer.log(
        'Audio',
        'Backend '
        f'sounddevice={version} '
        f'preferred_portaudio={preferred or "default"} '
        f'loaded_portaudio={loaded or "unknown"} '
        f'portaudio_version={portaudio_version} '
        f'default_device={default_device} '
        f'device_count={device_count}',
    )


class AudioPlayerWidget(QWidget):
    """Audio player widget with play/pause, volume, and seek controls."""

    stopped = Signal()

    def __init__(
        self,
        audio_file_path: str,
        parent: QWidget | None = None,
        config_manager: object | None = None,
    ) -> None:
        """
        Initialize audio player.

        Args:
            audio_file_path: Path to audio file (mp3, ogg, wav, etc.)
            parent: Parent widget
            config_manager: ConfigManager for persisting volume
        """
        super().__init__(parent)
        self.audio_file_path = audio_file_path
        self.config_manager = config_manager

        # Playback state
        self.is_playing = False
        self.is_scrubbing = False
        self.should_stop = False

        # Position in samples (single source of truth)
        self.playback_position = 0

        # Audio data
        self.audio_data: AudioArray | None = None
        self.sample_rate: int | None = None
        self.duration = 0.0

        # Volume
        initial_slider = _config_audio_volume(config_manager) if config_manager else 70

        if initial_slider <= 0:
            self.volume = 0.0
        else:
            # Logarithmic mapping: volume = (10^(value/100) - 1) / 9
            self.volume = (pow(10, initial_slider / 100.0) - 1.0) / 9.0

        # Playback thread and stream
        self.stream: _OutputStreamLike | None = None
        self.playback_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.position_lock = threading.Lock()
        self.stream_lock = threading.Lock()

        _log_audio_backend_once()
        self._load_audio()
        self._setup_ui()

        # Update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_ui)
        self.timer.start(50)  # 20 FPS

    def _read_audio_data(self) -> None:
        self.audio_data, self.sample_rate = sf.read(self.audio_file_path, dtype='float32')
        if len(self.audio_data.shape) == 1:
            self.audio_data = np.column_stack((self.audio_data, self.audio_data))
        elif self.audio_data.shape[1] == 1:
            self.audio_data = np.repeat(self.audio_data, 2, axis=1)
        elif self.audio_data.shape[1] > 2:
            mono = self.audio_data.mean(axis=1)
            self.audio_data = np.column_stack((mono, mono))

        self.audio_data = np.ascontiguousarray(
            np.clip(self.audio_data, -1.0, 1.0), dtype=np.float32
        )
        self.duration = len(_audio_data(self.audio_data)) / _sample_rate(self.sample_rate)

    def _load_audio(self) -> None:
        """Load audio file and get metadata."""
        try:
            self._read_audio_data()
        except (OSError, RuntimeError, TypeError, ValueError, sf.LibsndfileError) as exc:
            log_buffer.log('Audio', f'Error loading audio: {exc}')
            self.duration = 0

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addStretch()

        # Central container for all controls (centered)
        controls_container = QVBoxLayout()
        controls_container.setSpacing(6)
        controls_container.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Volume slider row
        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(8)
        volume_layout.addStretch()
        volume_layout.addWidget(QLabel(tr('ui.cache.audio_player.volume')))

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        # Set initial slider value from config if available, otherwise default to 70
        initial_val = _config_audio_volume(self.config_manager) if self.config_manager else 70
        self.volume_slider.setValue(initial_val)
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.volume_slider.setFixedWidth(175)
        volume_layout.addWidget(self.volume_slider)

        volume_layout.addStretch()
        controls_container.addLayout(volume_layout)

        # Progress slider row
        progress_layout = QHBoxLayout()
        progress_layout.addStretch()
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, int(self.duration * 1000))
        self.progress_slider.sliderPressed.connect(self._start_scrub)
        self.progress_slider.sliderReleased.connect(self._end_scrub)
        self.progress_slider.setFixedWidth(226)
        progress_layout.addWidget(self.progress_slider)
        progress_layout.addStretch()
        controls_container.addLayout(progress_layout)

        # Play/Replay buttons and time label row
        button_time_layout = QHBoxLayout()
        button_time_layout.setSpacing(8)
        button_time_layout.addStretch()

        self.play_pause_btn = QPushButton(tr('ui.cache.audio_player.text'))
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.play_pause_btn.setFixedSize(32, 32)
        self.play_pause_btn.setToolTip(tr('ui.cache.audio_player.play_pause'))
        button_time_layout.addWidget(self.play_pause_btn)

        self.replay_btn = QPushButton(tr('ui.cache.audio_player.text_2'))
        self.replay_btn.clicked.connect(self._replay)
        self.replay_btn.setFixedSize(32, 32)
        self.replay_btn.setToolTip(tr('ui.cache.audio_player.replay'))
        button_time_layout.addWidget(self.replay_btn)

        self.time_label = QLabel(
            tr('ui.cache.audio_player.00_00_000_value', value0=self._format_time(self.duration))
        )
        self.time_label.setStyleSheet('color: #888; font-size: 11px;')
        button_time_layout.addWidget(self.time_label)

        button_time_layout.addStretch()
        controls_container.addLayout(button_time_layout)

        layout.addLayout(controls_container)
        layout.addStretch()

        self.setLayout(layout)

    def _toggle_play_pause(self) -> None:
        """Toggle play/pause state."""
        if not self.is_playing:
            self._play()
        else:
            self._pause()

    def _play(self) -> None:
        """Start playback."""
        if self.audio_data is None:
            return

        # Reset if at end
        with self.position_lock:
            if self.playback_position >= len(self.audio_data):
                self.playback_position = 0

        self.is_playing = True
        self.should_stop = False
        self.play_pause_btn.setText(tr('ui.cache.audio_player.text_3'))

        # Start playback thread
        stop_event = threading.Event()
        self.stop_event = stop_event
        self.playback_thread = threading.Thread(
            target=self._playback_worker,
            args=(stop_event,),
            daemon=True,
        )
        self.playback_thread.start()

    def _pause(self) -> None:
        """Pause playback."""
        self.is_playing = False
        self.should_stop = True
        self.play_pause_btn.setText(tr('ui.cache.audio_player.text'))
        if self.stop_event:
            self.stop_event.set()

        # Let the playback worker close the PortAudio stream. Closing can block
        # inside Pa_CloseStream on some device/backend transitions, and this
        # method runs on the Qt UI thread.

    def _replay(self) -> None:
        """Replay from beginning."""
        # Stop current playback
        if self.is_playing:
            self._pause()

        # Reset position
        with self.position_lock:
            self.playback_position = 0

        # Start playing
        self._play()

    def _write_audio_callback(
        self,
        stop_event: threading.Event,
        outdata: AudioArray,
        frames: int,
        status: object,
    ) -> None:
        if status:
            log_buffer.log('Audio', f'Audio callback status: {status}')
        with self.position_lock:
            start_pos = self.playback_position
            audio_data = _audio_data(self.audio_data)
            end_pos = min(start_pos + frames, len(audio_data))
            chunk_size = end_pos - start_pos
            if chunk_size <= 0 or stop_event.is_set():
                outdata[:] = 0
                stop_event.set()
                return
            outdata[:chunk_size] = audio_data[start_pos:end_pos] * self.volume
            if chunk_size < frames:
                outdata[chunk_size:] = 0
            self.playback_position = end_pos

    def _create_output_stream(self, stop_event: threading.Event) -> _OutputStreamLike:
        def callback(
            outdata: AudioArray,
            frames: int,
            _time_info: object,
            status: object,
        ) -> None:
            self._write_audio_callback(stop_event, outdata, frames, status)

        return sd.OutputStream(
            samplerate=_sample_rate(self.sample_rate),
            channels=2,
            dtype='float32',
            callback=callback,
            blocksize=2048,
        )

    def _wait_for_playback_stop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            time.sleep(0.01)
            with self.position_lock:
                if self.playback_position >= len(_audio_data(self.audio_data)):
                    self.should_stop = True
                    stop_event.set()

    @staticmethod
    def _close_output_stream(stream: _OutputStreamLike) -> None:
        try:
            stream.stop()
        except (RuntimeError, sd.PortAudioError) as exc:
            log_buffer.log('Audio', f'Error stopping audio stream: {exc}')
        try:
            stream.close()
        except (RuntimeError, sd.PortAudioError) as exc:
            log_buffer.log('Audio', f'Error closing audio stream: {exc}')

    def _finish_playback(
        self,
        stop_event: threading.Event,
        stream: _OutputStreamLike | None,
    ) -> None:
        is_current_playback = False
        with self.stream_lock:
            if self.stream is stream:
                self.stream = None
            if self.stop_event is stop_event:
                self.stop_event = None
                is_current_playback = True
        if not is_current_playback:
            return
        self.is_playing = False
        try:
            QTimer.singleShot(0, lambda: self._safe_set_play_pause_text('▶'))
        except RuntimeError:
            pass

    def _playback_worker(self, stop_event: threading.Event) -> None:
        """Worker thread for audio playback."""
        stream: _OutputStreamLike | None = None
        try:
            stream = self._create_output_stream(stop_event)
            with self.stream_lock:
                self.stream = stream
            stream.start()
            self._wait_for_playback_stop(stop_event)
        except (RuntimeError, TypeError, ValueError, sd.PortAudioError) as exc:
            log_buffer.log('Audio', f'Playback error: {exc}')
        finally:
            if stream is not None:
                self._close_output_stream(stream)
            self._finish_playback(stop_event, stream)

    def _safe_set_play_pause_text(self, text: str) -> None:
        """Set play/pause button text from the main thread, safely."""
        try:
            self.play_pause_btn.setText(text)
        except RuntimeError:
            pass

    def _start_scrub(self) -> None:
        """Called when user starts dragging progress slider."""
        self.is_scrubbing = True
        if self.is_playing:
            self._pause()

    def _end_scrub(self) -> None:
        """Called when user releases progress slider."""
        # Seek to new position
        new_time = self.progress_slider.value() / 1000.0
        new_time = max(0, min(new_time, self.duration))

        with self.position_lock:
            self.playback_position = int(new_time * _sample_rate(self.sample_rate))

        self.is_scrubbing = False

    def _set_volume(self, value: int) -> None:
        """Set volume level."""
        # Logarithmic mapping: volume = (10^(value/100) - 1) / 9
        if value <= 0:
            self.volume = 0.0
        else:
            self.volume = (pow(10, value / 100.0) - 1.0) / 9.0

        if self.config_manager:
            _set_config_audio_volume(self.config_manager, value)

    def _update_ui(self) -> None:
        """Update progress slider and time label."""
        if not self.is_scrubbing and self.sample_rate:
            with self.position_lock:
                current_time = self.playback_position / self.sample_rate

            self.progress_slider.setValue(int(current_time * 1000))
            self.time_label.setText(
                tr(
                    'ui.cache.audio_player.value_value',
                    value0=self._format_time(current_time),
                    value1=self._format_time(self.duration),
                )
            )

        # Keep button in sync with playback state (handles thread-safe UI updates)
        expected_text = '⏸' if self.is_playing else '▶'
        if self.play_pause_btn.text() != expected_text:
            self.play_pause_btn.setText(expected_text)

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS.mmm."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f'{minutes:02d}:{secs:02d}.{millis:03d}'

    def stop(self) -> None:
        """Stop playback and cleanup."""
        self.should_stop = True
        self.is_playing = False
        if self.stop_event:
            self.stop_event.set()

        if self.timer:
            self.timer.stop()

        self.stopped.emit()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle widget close."""
        self.stop()
        super().closeEvent(event)
