"""Reusable UI gate for sections that require Fleasion's proxy."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


PROXY_DISABLED_MESSAGE = 'This section is closed because you turned off the proxy in the Settings.'


class ProxyGate(QWidget):
    """Wrap a widget with a disabled overlay controlled by the proxy toggle."""

    def __init__(self, content: QWidget, message: str = PROXY_DISABLED_MESSAGE,
                 compact: bool = False, parent=None):
        super().__init__(parent)
        self._content = content
        self._compact = compact

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)

        self._overlay = QFrame(self)
        self._overlay.setObjectName('_FleasionProxyDisabledOverlayCompact' if compact else '_FleasionProxyDisabledOverlay')
        self._overlay.setVisible(False)
        self._overlay.raise_()

        overlay_layout = QVBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(16, 12, 16, 12)

        label = QLabel(message)
        label.setObjectName('_FleasionProxyDisabledOverlayLabel')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setMinimumHeight(40 if compact else 80)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(label)
        row.addStretch()

        overlay_layout.addStretch()
        overlay_layout.addLayout(row)
        overlay_layout.addStretch()
        self._apply_style()

    def event(self, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()
        return super().event(event)

    def set_proxy_enabled(self, enabled: bool):
        self._content.setEnabled(enabled)
        self._overlay.setVisible(not enabled)
        if not enabled:
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()

    def _apply_style(self):
        if self._compact:
            radius = 6
            label_padding = '10px 14px'
            label_width = 'min-width: 220px; max-width: 420px;'
        else:
            radius = 8
            label_padding = '18px 24px'
            label_width = 'min-width: 320px; max-width: 560px;'

        self._overlay.setStyleSheet(f"""
            QFrame#{self._overlay.objectName()} {{
                background-color: rgba(24, 24, 24, 150);
                border-radius: {radius}px;
            }}
            QLabel#_FleasionProxyDisabledOverlayLabel {{
                background-color: rgba(20, 20, 20, 210);
                color: white;
                border: 1px solid rgba(255, 255, 255, 80);
                border-radius: {radius}px;
                padding: {label_padding};
                font-weight: 600;
                {label_width}
            }}
        """)
