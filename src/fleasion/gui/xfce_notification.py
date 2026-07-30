"""XFCE-specific tray notification widget."""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class XfceTrayNotification(QWidget):
    """A readable tray notification for XFCE's inconsistent native palette."""

    closed = pyqtSignal(object)

    def __init__(self, title: str, message: str, icon: QIcon, dark: bool, timeout: int):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # A translucent top-level surface can lose its stylesheet background under XFCE/X11.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName('FleasionXfceTrayNotification')
        self.setWindowTitle(title)
        self.setWindowIcon(icon)

        if dark:
            background = '#2b2b2b'
            foreground = '#f4f4f4'
            secondary = '#d8d8d8'
            border = '#626262'
        else:
            background = '#fffdf2'
            foreground = '#202020'
            secondary = '#353535'
            border = '#b7b7b7'

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(foreground))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.setStyleSheet(
            f'''
            QWidget#FleasionXfceTrayNotification {{
                background-color: {background};
                color: {foreground};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLabel#FleasionXfceTrayNotificationTitle {{
                color: {foreground};
                background: transparent;
                border: none;
                font-weight: 700;
            }}
            QLabel#FleasionXfceTrayNotificationMessage {{
                color: {secondary};
                background: transparent;
                border: none;
            }}
            QPushButton#FleasionXfceTrayNotificationClose {{
                color: {secondary};
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 0;
            }}
            QPushButton#FleasionXfceTrayNotificationClose:hover {{
                color: {foreground};
                background: {border};
                border-radius: 4px;
            }}
            '''
        )

        icon_label = QLabel()
        icon_label.setFixedSize(32, 32)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(32, 32))

        title_label = QLabel(title)
        title_label.setObjectName('FleasionXfceTrayNotificationTitle')

        message_label = QLabel(message)
        message_label.setObjectName('FleasionXfceTrayNotificationMessage')
        message_label.setWordWrap(True)
        message_label.setMinimumWidth(320)
        message_label.setMaximumWidth(420)

        close_button = QPushButton('×')
        close_button.setObjectName('FleasionXfceTrayNotificationClose')
        close_button.setFixedSize(24, 24)
        close_button.setToolTip('Close notification')
        close_button.clicked.connect(self.close)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title_label)
        text_layout.addWidget(message_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 10)
        layout.setSpacing(8)
        layout.addWidget(icon_label)
        layout.addLayout(text_layout, 1)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(timeout)

    def show_near_tray(self, tray_geometry) -> None:
        """Show the notification beside the tray icon without taking focus."""
        self.adjustSize()
        screen = None
        if not tray_geometry.isNull():
            screen = QApplication.screenAt(tray_geometry.center())
        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is not None:
            available = screen.availableGeometry()
            width = self.width()
            height = self.height()
            if tray_geometry.isNull():
                x = available.right() - width - 12
                y = available.bottom() - height - 12
            else:
                x = tray_geometry.center().x() - width // 2
                x = max(available.left() + 8, min(x, available.right() - width - 8))
                if tray_geometry.center().y() >= available.center().y():
                    y = max(available.top() + 8, tray_geometry.top() - height - 8)
                else:
                    y = min(available.bottom() - height - 8, tray_geometry.bottom() + 8)
            self.move(x, y)

        self.show()

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)
