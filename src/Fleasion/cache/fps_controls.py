"""Keyboard helpers for OpenGL FPS camera controls."""

from typing import Literal

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent


MovementKey = Literal[
    'forward',
    'left',
    'backward',
    'right',
    'slow',
    'fast',
    'up',
    'down',
]

KEY_FORWARD: MovementKey = 'forward'
KEY_LEFT: MovementKey = 'left'
KEY_BACKWARD: MovementKey = 'backward'
KEY_RIGHT: MovementKey = 'right'
KEY_SLOW: MovementKey = 'slow'
KEY_FAST: MovementKey = 'fast'
KEY_UP: MovementKey = 'up'
KEY_DOWN: MovementKey = 'down'

WASD_MOVEMENT_KEYS = frozenset(
    (KEY_FORWARD, KEY_LEFT, KEY_BACKWARD, KEY_RIGHT),
)
VERTICAL_MOVEMENT_KEYS = frozenset((KEY_UP, KEY_DOWN))

# Qt reports platform-native scan codes. Windows and Linux/Wayland usually expose
# evdev/set-1 style values, while Linux/X11 commonly reports XKB keycodes (+8).
_SCAN_CODES_BY_MOVEMENT_KEY: dict[MovementKey, frozenset[int]] = {
    KEY_FORWARD: frozenset((0x11, 25)),
    KEY_LEFT: frozenset((0x1E, 38)),
    KEY_BACKWARD: frozenset((0x1F, 39)),
    KEY_RIGHT: frozenset((0x20, 40)),
    KEY_SLOW: frozenset((0x10, 24)),
    KEY_FAST: frozenset((0x12, 26)),
    KEY_UP: frozenset((0x39, 65)),
    KEY_DOWN: frozenset((0x2A, 50)),
}

_MOVEMENT_KEY_BY_QT_KEY: dict[int, MovementKey] = {
    int(Qt.Key.Key_W): KEY_FORWARD,
    int(Qt.Key.Key_A): KEY_LEFT,
    int(Qt.Key.Key_S): KEY_BACKWARD,
    int(Qt.Key.Key_D): KEY_RIGHT,
    int(Qt.Key.Key_Q): KEY_SLOW,
    int(Qt.Key.Key_E): KEY_FAST,
    int(Qt.Key.Key_Space): KEY_UP,
    int(Qt.Key.Key_Shift): KEY_DOWN,
}


def movement_key_from_scan_and_qt_key(
    scan_code: int,
    qt_key: int,
) -> MovementKey | None:
    """Return the normalized FPS movement key for a Qt key event."""
    for movement_key, scan_codes in _SCAN_CODES_BY_MOVEMENT_KEY.items():
        if scan_code in scan_codes:
            return movement_key

    return _MOVEMENT_KEY_BY_QT_KEY.get(qt_key)


def movement_key_from_event(event: QKeyEvent) -> MovementKey | None:
    """Return the normalized FPS movement key for a Qt key event."""
    return movement_key_from_scan_and_qt_key(
        event.nativeScanCode(),
        event.key(),
    )
