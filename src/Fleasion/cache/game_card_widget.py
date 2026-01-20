from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt
from PIL import Image, ImageDraw

from .gameCard import Ui_GameCard  # uses the UI class from gameCard.py


class GameCardWidget(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_GameCard()
        self.ui.setupUi(self)

        # Make thumbnail behave nicely with layouts
        vertical_policy = self.ui.thumbLabel.sizePolicy().verticalPolicy()
        self.ui.thumbLabel.setSizePolicy(
            QSizePolicy.Policy.Expanding, vertical_policy)
        self.ui.thumbLabel.setScaledContents(False)
        self.ui.thumbLabel.setMinimumWidth(1)

        self._current_pixmap = None

    def set_data(self, name: str, created: str, updated: str):
        self.ui.nameLabel.setText(name)
        self.ui.createdLabel.setText(f"Created: {created}")
        self.ui.updatedLabel.setText(f"Updated: {updated}")

    def refresh_thumbnail(self):
        if self._current_pixmap:
            self.set_thumbnail(self._current_pixmap)

    def set_thumbnail(self, pixmap: QPixmap | None, radius: int = 4, scale_factor: int = 2):
        self._current_pixmap = pixmap
        label = self.ui.thumbLabel

        if not pixmap or pixmap.isNull():
            label.setText("No thumbnail")
            return

        label_w = label.width()
        label_h = label.height()
        if label_w == 0 or label_h == 0:
            return

        qimg = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        img = Image.frombytes(
            "RGBA", (qimg.width(), qimg.height()), qimg.bits().tobytes())

        src_w, src_h = img.size
        label_ratio = label_w / label_h
        src_ratio = src_w / src_h

        # Center crop
        if src_ratio > label_ratio:
            new_w = int(src_h * label_ratio)
            left = (src_w - new_w) // 2
            box = (left, 0, left + new_w, src_h)
        else:
            new_h = int(src_w / label_ratio)
            top = (src_h - new_h) // 2
            box = (0, top, src_w, top + new_h)

        img = img.crop(box)

        # Overscale
        big_w = label_w * scale_factor
        big_h = label_h * scale_factor
        img = img.resize((big_w, big_h), Image.LANCZOS)

        # Rounded corners
        mask = Image.new("L", (big_w, big_h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, big_w, big_h),
                               radius=radius * scale_factor, fill=255)
        img.putalpha(mask)

        # Downscale
        img = img.resize((label_w, label_h), Image.LANCZOS)

        out_qimg = QImage(img.tobytes("raw", "RGBA"), img.width,
                          img.height, QImage.Format_RGBA8888)
        label.setPixmap(QPixmap.fromImage(out_qimg))

    def on_join(self, fn):
        self.ui.joinButton.clicked.connect(fn)

    def on_open(self, fn):
        self.ui.openButton.clicked.connect(fn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Don’t fire if clicking a button
            child = self.childAt(event.pos())
            if child and child.inherits("QPushButton"):
                return super().mousePressEvent(event)
            self.clicked.emit()
        return super().mousePressEvent(event)
