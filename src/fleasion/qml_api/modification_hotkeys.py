"""Platform hotkey lifecycle for QML custom FastFlag controls."""

from __future__ import annotations

import sys
from typing import Any, Final

from PySide6.QtCore import QObject, QTimer, Qt, Signal


def _qt_key_value(key: Qt.Key) -> int:
    return int(key.value)


_QT_MODIFIER_MASKS: Final = {
    _qt_key_value(Qt.Key.Key_Shift): 0x01,
    _qt_key_value(Qt.Key.Key_Control): 0x02,
    _qt_key_value(Qt.Key.Key_Alt): 0x04,
    _qt_key_value(Qt.Key.Key_AltGr): 0x04,
    _qt_key_value(Qt.Key.Key_Meta): 0x08,
}
_QT_EXTENDED_KEYS: Final = frozenset(
    _qt_key_value(key)
    for key in (
        Qt.Key.Key_Insert,
        Qt.Key.Key_Delete,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Left,
        Qt.Key.Key_Up,
        Qt.Key.Key_Right,
        Qt.Key.Key_Down,
        Qt.Key.Key_Meta,
    )
)
_QT_KEYPAD_MODIFIER: Final = int(Qt.KeyboardModifier.KeypadModifier.value)


class CustomFastFlagHotkeys(QObject):
    """Own platform hotkey services without exposing input details to QML."""

    toggled = Signal(str)
    captureChanged = Signal()
    captureCompleted = Signal(str, str)
    errorOccurred = Signal(str)

    _LINUX_MOUSE_CODES = frozenset({0x110, 0x111, 0x112, 0x113, 0x114})

    def __init__(
        self,
        config_manager: Any | None,
        proxy_master: Any | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._controller: Any | None = None
        self._capture_name = ''
        self._capture_message = ''
        self._pending_linux_modifier: int | None = None
        self._pending_windows_key: tuple[int, int] | None = None
        if config_manager is None:
            return
        if sys.platform == 'win32':
            from ..modifications.hotkeys.windows import (
                WindowsCustomFFlagHotkeyController,
            )

            self._controller = WindowsCustomFFlagHotkeyController(
                config_manager, proxy_master, self
            )
        elif sys.platform.startswith('linux'):
            from ..modifications.hotkeys.linux import LinuxCustomFFlagHotkeyController

            self._controller = LinuxCustomFFlagHotkeyController(config_manager, proxy_master, self)
            service = self._controller.service
            service.key_pressed.connect(self._on_linux_key_pressed)
            service.key_released.connect(self._on_linux_key_released)
            service.wheel_scrolled.connect(self._on_linux_wheel)
        if self._controller is not None:
            self._controller.toggled.connect(self.toggled)
            self.sync()

    @property
    def supported(self) -> bool:
        return self._controller is not None

    @property
    def capture_busy(self) -> bool:
        return bool(self._capture_name)

    @property
    def capture_message(self) -> str:
        return self._capture_message

    @property
    def permission_setup_available(self) -> bool:
        return sys.platform.startswith('linux')

    def begin_capture(self, name: str) -> bool:
        if self._controller is None or self._config is None:
            self.errorOccurred.emit('Global FastFlag hotkeys are unavailable on this platform.')
            return False
        if name not in self._config.custom_fflags:
            self.errorOccurred.emit('The selected FastFlag no longer exists.')
            return False
        self._capture_name = name
        self._pending_linux_modifier = None
        self._pending_windows_key = None
        if sys.platform.startswith('linux'):
            service = self._controller.service
            if not service.begin_capture():
                self._capture_name = ''
                detail = service.last_error or 'Fleasion could not read Linux input devices.'
                self._capture_message = detail
                self.captureChanged.emit()
                self.errorOccurred.emit(detail)
                return False
            self._capture_message = 'Press a key, mouse button, or scroll the wheel.'
        else:
            self._capture_message = 'Press a key, click the capture area, or scroll.'
        self.captureChanged.emit()
        return True

    def cancel_capture(self) -> None:
        if not self._capture_name and not self._capture_message:
            return
        self._capture_name = ''
        self._capture_message = ''
        self._pending_linux_modifier = None
        self._pending_windows_key = None
        self.captureChanged.emit()
        self.sync()

    def capture_native_key(
        self,
        native_scan_code: int,
        qt_key: int,
        qt_modifiers: int,
    ) -> bool:
        if sys.platform != 'win32' or not self._capture_name:
            return False
        scan_code = native_scan_code & 0xFF
        if not scan_code:
            self._capture_message = 'That key did not provide a usable physical scan code.'
            self.captureChanged.emit()
            return False
        modifiers = self._modifier_mask(qt_modifiers)
        own_modifier = _QT_MODIFIER_MASKS.get(qt_key, 0)
        binding: dict[str, int | bool | str] = {
            'scan_code': scan_code,
            'extended': bool(
                native_scan_code & ~0xFF
                or qt_key in _QT_EXTENDED_KEYS
                or qt_modifiers & _QT_KEYPAD_MODIFIER
                and scan_code in {0x1C, 0x35}
            ),
            'modifiers': modifiers & ~own_modifier,
        }
        if own_modifier:
            self._pending_windows_key = (scan_code, qt_key)
            self._capture_message = (
                'Release the modifier to bind it alone, or press another key for a combination.'
            )
            self.captureChanged.emit()
            return True
        self._pending_windows_key = None
        return self._finish_binding(binding)

    def release_native_key(self, native_scan_code: int, qt_key: int) -> bool:
        if sys.platform != 'win32' or not self._capture_name:
            return False
        scan_code = native_scan_code & 0xFF
        if self._pending_windows_key != (scan_code, qt_key):
            return False
        self._pending_windows_key = None
        return self._finish_binding(
            {
                'scan_code': scan_code,
                'extended': bool(native_scan_code & ~0xFF or qt_key in _QT_EXTENDED_KEYS),
                'modifiers': 0,
            }
        )

    def capture_pointer(self, kind: str, code: int, qt_modifiers: int) -> bool:
        if sys.platform != 'win32' or not self._capture_name:
            return False
        modifiers = self._modifier_mask(qt_modifiers)
        if kind == 'wheel' and code in {-1, 1}:
            return self._finish_binding(
                {
                    'platform': 'windows',
                    'kind': 'mouse_wheel',
                    'direction': 'up' if code > 0 else 'down',
                    'modifiers': modifiers,
                }
            )
        if kind == 'mouse' and code in {1, 2, 4, 5, 6}:
            return self._finish_binding(
                {
                    'platform': 'windows',
                    'kind': 'mouse_button',
                    'scan_code': code,
                    'extended': False,
                    'modifiers': modifiers,
                }
            )
        return False

    def clear_binding(self, name: str) -> bool:
        if self._config is None:
            return False
        bindings = dict(self._config.custom_fflag_keybinds)
        if name not in bindings:
            return True
        bindings.pop(name, None)
        self._config.custom_fflag_keybinds = bindings
        self.sync()
        return True

    def setup_linux_permissions(self) -> bool:
        if not self.permission_setup_available:
            return False
        from ..modifications.hotkeys.linux import launch_permission_setup

        try:
            launch_permission_setup()
        except OSError as exc:
            self.errorOccurred.emit(f'Could not start the permission setup: {exc}')
            return False
        self._capture_message = (
            'Complete the permission prompt, then retry. A sign-out may be required '
            'before the new group membership applies.'
        )
        self.captureChanged.emit()
        return True

    def binding_text(self, name: str) -> str:
        if self._config is None:
            return 'Not assigned'
        binding = self._config.custom_fflag_keybinds.get(name)
        if sys.platform.startswith('linux'):
            from ..modifications.hotkeys.linux import binding_text
        else:
            from ..modifications.hotkeys.windows import binding_text
        return binding_text(binding)

    def sync(self) -> None:
        if self._controller is not None:
            self._controller.sync()

    def shutdown(self) -> None:
        self._capture_name = ''
        self._pending_linux_modifier = None
        self._pending_windows_key = None
        if self._controller is not None:
            self._controller.stop()

    def _on_linux_key_pressed(self, code: int, modifiers: int) -> None:
        if not self._capture_name:
            return
        from ..modifications.hotkeys.linux import modifier_mask_for_evdev_code

        own_modifier = modifier_mask_for_evdev_code(code)
        if own_modifier:
            self._pending_linux_modifier = code
            self._capture_message = (
                'Release the modifier to bind it alone, or press another key for a combination.'
            )
            self.captureChanged.emit()
            return
        self._pending_linux_modifier = None
        kind = 'mouse_button' if code in self._LINUX_MOUSE_CODES else 'key'
        binding: dict[str, int | str] = {
            'platform': 'linux_evdev',
            'scan_code': code,
            'modifiers': modifiers & ~own_modifier,
        }
        if kind == 'mouse_button':
            binding['kind'] = kind
            capture_name = self._capture_name
            QTimer.singleShot(
                40,
                lambda: self._finish_linux_pointer(capture_name, binding),
            )
            return
        self._finish_binding(binding)

    def _on_linux_key_released(self, code: int) -> None:
        if not self._capture_name or code != self._pending_linux_modifier:
            return
        self._pending_linux_modifier = None
        self._finish_binding(
            {
                'platform': 'linux_evdev',
                'scan_code': code,
                'modifiers': 0,
            }
        )

    def _finish_linux_pointer(
        self,
        capture_name: str,
        binding: dict[str, int | str],
    ) -> None:
        if capture_name and self._capture_name == capture_name:
            self._finish_binding(binding)

    def _on_linux_wheel(self, code: int, modifiers: int) -> None:
        if not self._capture_name:
            return
        from ..modifications.hotkeys.names import SMU_MOUSE_WHEEL_UP

        self._finish_binding(
            {
                'platform': 'linux_evdev',
                'kind': 'mouse_wheel',
                'direction': 'up' if code == SMU_MOUSE_WHEEL_UP else 'down',
                'modifiers': modifiers,
            }
        )

    def _finish_binding(self, binding: dict[str, int | bool | str]) -> bool:
        if self._config is None or not self._capture_name:
            return False
        name = self._capture_name
        bindings = dict(self._config.custom_fflag_keybinds)
        bindings[name] = binding
        self._config.custom_fflag_keybinds = bindings
        label = self.binding_text(name)
        self._capture_name = ''
        self._capture_message = ''
        self._pending_linux_modifier = None
        self._pending_windows_key = None
        self.sync()
        self.captureChanged.emit()
        self.captureCompleted.emit(name, label)
        return True

    @staticmethod
    def _modifier_mask(qt_modifiers: int) -> int:
        from ..modifications.hotkeys.windows import MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN

        result = 0
        if qt_modifiers & 0x02000000:
            result |= MOD_SHIFT
        if qt_modifiers & 0x04000000:
            result |= MOD_CTRL
        if qt_modifiers & 0x08000000:
            result |= MOD_ALT
        if qt_modifiers & 0x10000000:
            result |= MOD_WIN
        return result
