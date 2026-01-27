'''Audio player widget using sounddevice for Python 3.14 compatibility.'''

import os
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider
)


class AudioPlayerWidget(QWidget):
    '''Audio player widget with play/pause, volume, and seek controls.'''

    stopped = pyqtSignal()

    def __init__(self, audio_file_path: str, parent=None, config_manager=None):
        '''
        Initialize audio player.

        Args:
            audio_file_path: Path to audio file (mp3, ogg, wav, etc.)
            parent: Parent widget
            config_manager: ConfigManager for persisting volume
        '''
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
            self.volume = config_manager.audio_volume / 100.0
        else:
            self.volume = 0.7

        # Playback thread and stream
        self.stream = None
        self.playback_thread = None
        self.position_lock = threading.Lock()

        self._load_audio()
        self._setup_ui()

        # Update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_ui)
        self.timer.start(50)  # 20 FPS

    def _load_audio(self):
        '''Load audio file and get metadata.'''
        try:
            # Load audio file
            self.audio_data, self.sample_rate = sf.read(self.audio_file_path)

            # Convert to stereo if mono
            if len(self.audio_data.shape) == 1:
                self.audio_data = np.column_stack((self.audio_data, self.audio_data))

            # Calculate duration
            self.duration = len(self.audio_data) / self.sample_rate

        except Exception as e:
            print(f'Error loading audio: {e}')
            self.duration = 0

    def _setup_ui(self):
        '''Setup the UI.'''
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Controls
        controls_layout = QHBoxLayout()

        self.play_pause_btn = QPushButton('Play')
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.play_pause_btn.setFixedWidth(80)
        controls_layout.addWidget(self.play_pause_btn)

        controls_layout.addWidget(QLabel('Volume:'))

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.volume * 100))
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.volume_slider.setFixedWidth(150)
        controls_layout.addWidget(self.volume_slider)

        controls_layout.addStretch()

        layout.addLayout(controls_layout)

        # Progress slider
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, int(self.duration * 1000))
        self.progress_slider.sliderPressed.connect(self._start_scrub)
        self.progress_slider.sliderReleased.connect(self._end_scrub)
        layout.addWidget(self.progress_slider)

        # Time label
        self.time_label = QLabel(f'00:00.000 / {self._format_time(self.duration)}')
        layout.addWidget(self.time_label)

        self.setLayout(layout)

    def _toggle_play_pause(self):
        '''Toggle play/pause state.'''
        if not self.is_playing:
            self._play()
        else:
            self._pause()

    def _play(self):
        '''Start playback.'''
        if self.audio_data is None:
            return

        # Reset if at end
        with self.position_lock:
            if self.playback_position >= len(self.audio_data):
                self.playback_position = 0

        self.is_playing = True
        self.should_stop = False
        self.play_pause_btn.setText('Pause')

        # Start playback thread
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.playback_thread.start()

    def _pause(self):
        '''Pause playback.'''
        self.is_playing = False
        self.should_stop = True
        self.play_pause_btn.setText('Play')

        if self.stream:
            self.stream.stop()

    def _playback_worker(self):
        '''Worker thread for audio playback.'''
        try:
            def callback(outdata, frames, time_info, status):
                if status:
                    print(f'Audio callback status: {status}')

                with self.position_lock:
                    start_pos = self.playback_position
                    end_pos = min(start_pos + frames, len(self.audio_data))
                    chunk_size = end_pos - start_pos

                    if chunk_size <= 0 or self.should_stop:
                        outdata[:] = 0
                        self.should_stop = True
                        return

                    # Get audio data and apply volume
                    chunk = self.audio_data[start_pos:end_pos] * self.volume
                    outdata[:chunk_size] = chunk

                    # Fill remaining with silence
                    if chunk_size < frames:
                        outdata[chunk_size:] = 0

                    self.playback_position = end_pos

            # Create and start stream
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=2,
                callback=callback,
                blocksize=2048
            )

            with self.stream:
                while not self.should_stop:
                    time.sleep(0.01)

                    # Check if reached end
                    with self.position_lock:
                        if self.playback_position >= len(self.audio_data):
                            self.should_stop = True

        except Exception as e:
            print(f'Playback error: {e}')
        finally:
            self.is_playing = False
            self.play_pause_btn.setText('Play')

    def _start_scrub(self):
        '''Called when user starts dragging progress slider.'''
        self.is_scrubbing = True
        if self.is_playing:
            self._pause()

    def _end_scrub(self):
        '''Called when user releases progress slider.'''
        # Seek to new position
        new_time = self.progress_slider.value() / 1000.0
        new_time = max(0, min(new_time, self.duration))

        with self.position_lock:
            self.playback_position = int(new_time * self.sample_rate)

        self.is_scrubbing = False

    def _set_volume(self, value):
        '''Set volume level.'''
        self.volume = value / 100.0
        if self.config_manager:
            self.config_manager.audio_volume = value

    def _update_ui(self):
        '''Update progress slider and time label.'''
        if not self.is_scrubbing:
            with self.position_lock:
                current_time = self.playback_position / self.sample_rate

            self.progress_slider.setValue(int(current_time * 1000))
            self.time_label.setText(f'{self._format_time(current_time)} / {self._format_time(self.duration)}')

    def _format_time(self, seconds: float) -> str:
        '''Format seconds as MM:SS.mmm.'''
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f'{minutes:02d}:{secs:02d}.{millis:03d}'

    def stop(self):
        '''Stop playback and cleanup.'''
        self.should_stop = True
        self.is_playing = False

        if self.stream:
            self.stream.stop()
            self.stream.close()

        if self.timer:
            self.timer.stop()

        self.stopped.emit()

    def closeEvent(self, event):
        '''Handle widget close.'''
        self.stop()
        super().closeEvent(event)
