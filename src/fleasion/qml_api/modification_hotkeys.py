"""Platform hotkey lifecycle for QML custom FastFlag controls."""

from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QObject, Signal


class CustomFastFlagHotkeys(QObject):
    """Own the legacy platform services without exposing widget UI to QML."""

    toggled = Signal(str)
    captureChanged = Signal()
    captureCompleted = Signal(str, str)
    errorOccurred = Signal(str)

    _LINUX_MODIFIER_CODES = frozenset({29, 42, 54, 56, 97, 100, 125, 126})
    _LINUX_MOUSE_CODES = frozenset({0x110, 0x111, 0x112, 0x113, 0x114})
    _WINDOWS_EXTENDED_VIRTUAL_KEYS = frozenset(
        {
            0xA3,
            0xA5,
            0x2D,
            0x2E,
            0x24,
            0x23,
            0x21,
            0x22,
            0x25,
            0x26,
            0x27,
            0x28,
            0x5B,
            0x5C,
        }
    )

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

            self._controller = LinuxCustomFFlagHotkeyController(
                config_manager, proxy_master, self
            )
            service = self._controller.service
            service.key_pressed.connect(self._on_linux_key_pressed)
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

    def begin_capture(self, name: str) -> bool:
        if self._controller is None or self._config is None:
            self.errorOccurred.emit('Global FastFlag hotkeys are unavailable on this platform.')
            return False
        if name not in self._config.custom_fflags:
            self.errorOccurred.emit('The selected FastFlag no longer exists.')
            return False
        self._capture_name = name
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
        if not self._capture_name:
            return
        self._capture_name = ''
        self._capture_message = ''
        self.captureChanged.emit()
        self.sync()

    def capture_native_key(
        self,
        native_scan_code: int,
        native_virtual_key: int,
        qt_modifiers: int,
    ) -> bool:
        if sys.platform != 'win32' or not self._capture_name:
            return False
        if not 0 < native_scan_code <= 0xFF:
            self._capture_message = 'That key did not provide a usable physical scan code.'
            self.captureChanged.emit()
            return False
        from ..modifications.hotkeys.windows import modifier_mask_for_virtual_key

        modifiers = self._modifier_mask(qt_modifiers)
        return self._finish_binding(
            {
                'scan_code': native_scan_code,
                'extended': native_virtual_key in self._WINDOWS_EXTENDED_VIRTUAL_KEYS,
                'modifiers': modifiers
                & ~modifier_mask_for_virtual_key(native_virtual_key),
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
        if self._controller is not None:
            self._controller.stop()

    def _on_linux_key_pressed(self, code: int, modifiers: int) -> None:
        if not self._capture_name or code in self._LINUX_MODIFIER_CODES:
            return
        kind = 'mouse_button' if code in self._LINUX_MOUSE_CODES else 'key'
        binding: dict[str, int | str] = {
            'platform': 'linux_evdev',
            'scan_code': code,
            'modifiers': modifiers,
        }
        if kind == 'mouse_button':
            binding['kind'] = kind
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
