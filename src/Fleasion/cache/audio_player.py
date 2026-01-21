"""Audio player widget using sounddevice for Python 3.14 compatibility."""

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
    """Audio player widget with play/pause, volume, and seek controls."""

    stopped = pyqtSignal()

    def __init__(self, audio_file_path: str, parent=None):
        """
        Initialize audio player.

        Args:
            audio_file_path: Path to audio file (mp3, ogg, wav, etc.)
            parent: Parent widget
        """
        super().__init__(parent)
        self.audio_file_path = audio_file_path
        self.is_playing = False
        self.position = 0.0  # Current position in seconds
        self.duration = 0.0
        self.volume = 0.7

        # Audio data
        self.audio_data = None
        self.sample_rate = None
        self.current_frame = 0

        # Playback thread and stream
        self.stream = None
        self.playback_thread = None
        self.stop_playback = False

        self._load_audio()
        self._setup_ui()

        # Update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_progress)
        self.timer.start(100)

    def _load_audio(self):
        """Load audio file and get metadata."""
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
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # File info
        file_name = os.path.basename(self.audio_file_path)
        file_size = os.path.getsize(self.audio_file_path)
        info_text = f'File: {file_name} | Size: {self._format_size(file_size)} | Duration: {self._format_time(self.duration)}'
        layout.addWidget(QLabel(info_text))

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
        self.progress_slider.sliderReleased.connect(self._seek_audio)
        layout.addWidget(self.progress_slider)

        # Time label
        self.time_label = QLabel(f'00:00 / {self._format_time(self.duration)}')
        layout.addWidget(self.time_label)

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
        if self.position >= self.duration:
            self.position = 0
            self.current_frame = 0

        self.is_playing = True
        self.play_pause_btn.setText('Pause')
        self.stop_playback = False

        # Start playback in separate thread
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.playback_thread.start()

    def _pause(self):
        """Pause playback."""
        self.is_playing = False
        self.play_pause_btn.setText('Play')
        self.stop_playback = True

        if self.stream:
            self.stream.stop()

    def _playback_worker(self):
        """Worker thread for audio playback."""
        try:
            # Calculate start frame
            start_frame = int(self.position * self.sample_rate)

            # Create audio stream
            def callback(outdata, frames, time_info, status):
                if status:
                    print(f'Audio callback status: {status}')

                # Get audio chunk
                end_frame = min(start_frame + self.current_frame + frames, len(self.audio_data))
                chunk_size = end_frame - (start_frame + self.current_frame)

                if chunk_size <= 0:
                    # End of audio
                    self.stop_playback = True
                    outdata[:] = 0
                    return

                # Get audio data and apply volume
                chunk = self.audio_data[start_frame + self.current_frame:end_frame] * self.volume
                outdata[:chunk_size] = chunk

                # Fill remaining with silence
                if chunk_size < frames:
                    outdata[chunk_size:] = 0

                self.current_frame += chunk_size

            # Create and start stream
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=2,
                callback=callback,
                finished_callback=self._on_playback_finished
            )

            with self.stream:
                while not self.stop_playback:
                    time.sleep(0.01)

        except Exception as e:
            print(f'Playback error: {e}')
            self.is_playing = False

    def _on_playback_finished(self):
        """Called when playback finishes."""
        self.is_playing = False
        self.position = self.duration
        self.current_frame = len(self.audio_data)

    def _start_scrub(self):
        """Called when user starts dragging progress slider."""
        if self.is_playing:
            self._pause()

    def _seek_audio(self):
        """Seek to new position."""
        self.position = self.progress_slider.value() / 1000.0
        self.position = max(0, min(self.position, self.duration))
        self.current_frame = int(self.position * self.sample_rate)

        self.time_label.setText(f'{self._format_time(self.position)} / {self._format_time(self.duration)}')

    def _set_volume(self, value):
        """Set volume level."""
        self.volume = value / 100.0

    def _update_progress(self):
        """Update progress slider and time label."""
        if self.is_playing:
            # Update position based on current frame
            self.position = self.current_frame / self.sample_rate

            # Update UI
            self.progress_slider.setValue(int(self.position * 1000))
            self.time_label.setText(f'{self._format_time(self.position)} / {self._format_time(self.duration)}')

            # Check if finished
            if self.position >= self.duration:
                self.is_playing = False
                self.play_pause_btn.setText('Play')
                self.position = self.duration
                self.progress_slider.setValue(int(self.duration * 1000))

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS.mmm."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f'{minutes:02d}:{secs:02d}.{millis:03d}'

    def _format_size(self, size_bytes: int) -> str:
        """Format file size."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.1f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.1f} TB'

    def stop(self):
        """Stop playback and cleanup."""
        self.stop_playback = True
        self.is_playing = False

        if self.stream:
            self.stream.stop()
            self.stream.close()

        if self.timer:
            self.timer.stop()

        self.stopped.emit()

    def closeEvent(self, event):
        """Handle widget close."""
        self.stop()
        super().closeEvent(event)
