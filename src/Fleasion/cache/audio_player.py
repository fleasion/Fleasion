"""Audio player widget using sounddevice for Python 3.14 compatibility."""

import ctypes.util
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from ..utils import log_buffer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


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


def _import_sounddevice_with_bundled_portaudio():
    """Import sounddevice with PyInstaller's bundled PortAudio on Linux."""
    original_find_library = ctypes.util.find_library
    bundled_portaudio = _bundled_portaudio_path()

    def find_library(name):
        if name == 'portaudio' and bundled_portaudio is not None:
            return str(bundled_portaudio)
        return original_find_library(name)

    if bundled_portaudio is not None and sys.platform.startswith('linux'):
        ctypes.util.find_library = find_library
    try:
        import sounddevice as sounddevice
    finally:
        ctypes.util.find_library = original_find_library

    return sounddevice


sd = _import_sounddevice_with_bundled_portaudio()


class AudioPlayerWidget(QWidget):
    """Audio player widget with play/pause, volume, and seek controls."""

    stopped = pyqtSignal()

    def __init__(self, audio_file_path: str, parent=None, config_manager=None):
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
        self.audio_data = None
        self.sample_rate = None
        self.duration = 0.0

        # Volume
        if config_manager:
            initial_slider = config_manager.audio_volume
        else:
            initial_slider = 70
        
        if initial_slider <= 0:
            self.volume = 0.0
        else:
            # Logarithmic mapping: volume = (10^(value/100) - 1) / 9
            self.volume = (pow(10, initial_slider / 100.0) - 1.0) / 9.0

        # Playback thread and stream
        self.stream = None
        self.playback_thread = None
        self.stop_event = None
        self.position_lock = threading.Lock()
        self.stream_lock = threading.Lock()

        self._load_audio()
        self._setup_ui()

        # Update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_ui)
        self.timer.start(50)  # 20 FPS

    def _load_audio(self):
        """Load audio file and get metadata."""
        try:
            # Load audio as float32 so the callback writes the same dtype that
            # PortAudio receives from sounddevice.
            self.audio_data, self.sample_rate = sf.read(self.audio_file_path, dtype='float32')

            # Convert to exactly stereo for the fixed two-channel output stream.
            if len(self.audio_data.shape) == 1:
                self.audio_data = np.column_stack((self.audio_data, self.audio_data))
            elif self.audio_data.shape[1] == 1:
                self.audio_data = np.repeat(self.audio_data, 2, axis=1)
            elif self.audio_data.shape[1] > 2:
                mono = self.audio_data.mean(axis=1)
                self.audio_data = np.column_stack((mono, mono))

            self.audio_data = np.ascontiguousarray(np.clip(self.audio_data, -1.0, 1.0), dtype=np.float32)

            # Calculate duration
            self.duration = len(self.audio_data) / self.sample_rate

        except Exception as e:
            log_buffer.log('Audio', f'Error loading audio: {e}')
            self.duration = 0

    def _setup_ui(self):
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
        volume_layout.addWidget(QLabel('Volume:'))

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        # Set initial slider value from config if available, otherwise default to 70
        initial_val = self.config_manager.audio_volume if self.config_manager else 70
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

        self.play_pause_btn = QPushButton('▶')
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.play_pause_btn.setFixedSize(32, 32)
        self.play_pause_btn.setToolTip('Play/Pause')
        button_time_layout.addWidget(self.play_pause_btn)

        self.replay_btn = QPushButton('↻')
        self.replay_btn.clicked.connect(self._replay)
        self.replay_btn.setFixedSize(32, 32)
        self.replay_btn.setToolTip('Replay')
        button_time_layout.addWidget(self.replay_btn)

        self.time_label = QLabel(f'00:00.000 / {self._format_time(self.duration)}')
        self.time_label.setStyleSheet('color: #888; font-size: 11px;')
        button_time_layout.addWidget(self.time_label)

        button_time_layout.addStretch()
        controls_container.addLayout(button_time_layout)

        layout.addLayout(controls_container)
        layout.addStretch()

        self.setLayout(layout)

    def _toggle_play_pause(self):
        """Toggle play/pause state."""
        if not self.is_playing:
            self._play()
        else:
            self._pause()

    def _play(self):
        """Start playback."""
        if self.audio_data is None:
            return

        # Reset if at end
        with self.position_lock:
            if self.playback_position >= len(self.audio_data):
                self.playback_position = 0

        self.is_playing = True
        self.should_stop = False
        self.play_pause_btn.setText('⏸')

        # Start playback thread
        stop_event = threading.Event()
        self.stop_event = stop_event
        self.playback_thread = threading.Thread(
            target=self._playback_worker,
            args=(stop_event,),
            daemon=True,
        )
        self.playback_thread.start()

    def _pause(self):
        """Pause playback."""
        self.is_playing = False
        self.should_stop = True
        self.play_pause_btn.setText('▶')
        if self.stop_event:
            self.stop_event.set()

        # Let the playback worker close the PortAudio stream. Closing can block
        # inside Pa_CloseStream on some device/backend transitions, and this
        # method runs on the Qt UI thread.

    def _replay(self):
        """Replay from beginning."""
        # Stop current playback
        if self.is_playing:
            self._pause()

        # Reset position
        with self.position_lock:
            self.playback_position = 0

        # Start playing
        self._play()

    def _playback_worker(self, stop_event):
        """Worker thread for audio playback."""
        try:
            def callback(outdata, frames, time_info, status):
                if status:
                    log_buffer.log('Audio', f'Audio callback status: {status}')

                with self.position_lock:
                    start_pos = self.playback_position
                    end_pos = min(start_pos + frames, len(self.audio_data))
                    chunk_size = end_pos - start_pos

                    if chunk_size <= 0 or stop_event.is_set():
                        outdata[:] = 0
                        stop_event.set()
                        return

                    # Get audio data and apply volume
                    chunk = self.audio_data[start_pos:end_pos] * self.volume
                    outdata[:chunk_size] = chunk

                    # Fill remaining with silence
                    if chunk_size < frames:
                        outdata[chunk_size:] = 0

                    self.playback_position = end_pos

            # Create and start stream
            stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=2,
                dtype='float32',
                callback=callback,
                blocksize=2048
            )
            with self.stream_lock:
                self.stream = stream

            stream.start()
            try:
                while not stop_event.is_set():
                    time.sleep(0.01)

                    # Check if reached end
                    with self.position_lock:
                        if self.playback_position >= len(self.audio_data):
                            self.should_stop = True
                            stop_event.set()
            finally:
                try:
                    stream.stop()
                except Exception as e:
                    log_buffer.log('Audio', f'Error stopping audio stream: {e}')
                try:
                    stream.close()
                except Exception as e:
                    log_buffer.log('Audio', f'Error closing audio stream: {e}')

        except Exception as e:
            log_buffer.log('Audio', f'Playback error: {e}')
        finally:
            is_current_playback = False
            try:
                with self.stream_lock:
                    if self.stream is locals().get('stream'):
                        self.stream = None
                    if self.stop_event is stop_event:
                        self.stop_event = None
                        is_current_playback = True
            except Exception:
                pass
            if is_current_playback:
                self.is_playing = False
                # Schedule UI update on the main thread to avoid manipulating
                # Qt widgets from this worker thread (which can cause
                # "wrapped C/C++ object ... has been deleted" errors).
                try:
                    QTimer.singleShot(0, lambda: self._safe_set_play_pause_text('▶'))
                except Exception:
                    # If scheduling fails for any reason, ignore silently.
                    pass

    def _safe_set_play_pause_text(self, text: str):
        """Set play/pause button text from the main thread, safely.

        This method swallows exceptions that occur if the underlying
        C++ widget has been deleted.
        """
        try:
            self.play_pause_btn.setText(text)
        except Exception:
            # Widget may have been deleted; ignore.
            pass

    def _start_scrub(self):
        """Called when user starts dragging progress slider."""
        self.is_scrubbing = True
        if self.is_playing:
            self._pause()

    def _end_scrub(self):
        """Called when user releases progress slider."""
        # Seek to new position
        new_time = self.progress_slider.value() / 1000.0
        new_time = max(0, min(new_time, self.duration))

        with self.position_lock:
            self.playback_position = int(new_time * self.sample_rate)

        self.is_scrubbing = False

    def _set_volume(self, value):
        """Set volume level."""
        # Logarithmic mapping: volume = (10^(value/100) - 1) / 9
        if value <= 0:
            self.volume = 0.0
        else:
            self.volume = (pow(10, value / 100.0) - 1.0) / 9.0
            
        if self.config_manager:
            self.config_manager.audio_volume = value

    def _update_ui(self):
        """Update progress slider and time label."""
        if not self.is_scrubbing and self.sample_rate:
            with self.position_lock:
                current_time = self.playback_position / self.sample_rate

            self.progress_slider.setValue(int(current_time * 1000))
            self.time_label.setText(f'{self._format_time(current_time)} / {self._format_time(self.duration)}')

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

    def stop(self):
        """Stop playback and cleanup."""
        self.should_stop = True
        self.is_playing = False
        if self.stop_event:
            self.stop_event.set()

        if self.timer:
            self.timer.stop()

        self.stopped.emit()

    def closeEvent(self, event):
        """Handle widget close."""
        self.stop()
        super().closeEvent(event)
