"""Windows scan-code hotkeys for custom FastFlag toggles.

Bindings are captured as physical scan codes and converted to virtual keys for
``GetAsyncKeyState`` polling.  Polling is deliberately used instead of a
keyboard hook: it observes input but cannot consume or block the user's keys.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Mapping
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal

from ..utils import log_buffer
from .hotkey_names import format_smu_virtual_key


MOD_SHIFT = 0x01
MOD_CTRL = 0x02
MOD_ALT = 0x04
MOD_WIN = 0x08
MODIFIER_MASK = MOD_SHIFT | MOD_CTRL | MOD_ALT | MOD_WIN

_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C
_VK_LSHIFT = 0xA0
_VK_RSHIFT = 0xA1
_VK_LCONTROL = 0xA2
_VK_RCONTROL = 0xA3
_VK_LMENU = 0xA4
_VK_RMENU = 0xA5


def modifier_mask_for_virtual_key(virtual_key: int) -> int:
    if virtual_key in (_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT):
        return MOD_SHIFT
    if virtual_key in (_VK_CONTROL, _VK_LCONTROL, _VK_RCONTROL):
        return MOD_CTRL
    if virtual_key in (_VK_MENU, _VK_LMENU, _VK_RMENU):
        return MOD_ALT
    if virtual_key in (_VK_LWIN, _VK_RWIN):
        return MOD_WIN
    return 0


def normalize_binding(binding) -> dict[str, int | bool] | None:
    """Validate a persisted physical-key binding."""
    if not isinstance(binding, Mapping) or binding.get('platform') not in (None, 'windows'):
        return None
    scan_code = binding.get('scan_code')
    modifiers = binding.get('modifiers', 0)
    extended = binding.get('extended', False)
    if (
        not isinstance(scan_code, int)
        or isinstance(scan_code, bool)
        or not 0 < scan_code <= 0xFF
        or not isinstance(modifiers, int)
        or isinstance(modifiers, bool)
        or modifiers & ~MODIFIER_MASK
        or not isinstance(extended, bool)
    ):
        return None
    return {'scan_code': scan_code, 'extended': extended, 'modifiers': modifiers}


# SMU's WinScanToVk table from platform/linux/input_evdev_uinput.cpp.  Windows
# itself performs this conversion at runtime; the table keeps stored bindings
# readable in tests and on non-Windows systems with identical SMU names.
_SMU_WINDOWS_SCAN_TO_VK = {
    0x01: 0x1B, 0x02: 0x31, 0x03: 0x32, 0x04: 0x33, 0x05: 0x34, 0x06: 0x35,
    0x07: 0x36, 0x08: 0x37, 0x09: 0x38, 0x0A: 0x39, 0x0B: 0x30, 0x0C: 0xBD,
    0x0D: 0xBB, 0x0E: 0x08, 0x0F: 0x09, 0x10: 0x51, 0x11: 0x57, 0x12: 0x45,
    0x13: 0x52, 0x14: 0x54, 0x15: 0x59, 0x16: 0x55, 0x17: 0x49, 0x18: 0x4F,
    0x19: 0x50, 0x1A: 0xDB, 0x1B: 0xDD, 0x1C: 0x0D, 0x1D: 0x11, 0x1E: 0x41,
    0x1F: 0x53, 0x20: 0x44, 0x21: 0x46, 0x22: 0x47, 0x23: 0x48, 0x24: 0x4A,
    0x25: 0x4B, 0x26: 0x4C, 0x27: 0xBA, 0x28: 0xDE, 0x29: 0xC0, 0x2A: 0x10,
    0x2B: 0xDC, 0x2C: 0x5A, 0x2D: 0x58, 0x2E: 0x43, 0x2F: 0x56, 0x30: 0x42,
    0x31: 0x4E, 0x32: 0x4D, 0x33: 0xBC, 0x34: 0xBE, 0x35: 0xBF, 0x36: 0x10,
    0x37: 0x6A, 0x38: 0x12, 0x39: 0x20, 0x3A: 0x14, 0x3B: 0x70, 0x3C: 0x71,
    0x3D: 0x72, 0x3E: 0x73, 0x3F: 0x74, 0x40: 0x75, 0x41: 0x76, 0x42: 0x77,
    0x43: 0x78, 0x44: 0x79, 0x45: 0x90, 0x46: 0x91, 0x47: 0x67, 0x48: 0x68,
    0x49: 0x69, 0x4A: 0x6D, 0x4B: 0x64, 0x4C: 0x65, 0x4D: 0x66, 0x4E: 0x6B,
    0x4F: 0x61, 0x50: 0x62, 0x51: 0x63, 0x52: 0x60, 0x53: 0x6E, 0x57: 0x7A,
    0x58: 0x7B,
}
_SMU_WINDOWS_EXTENDED_SCAN_TO_VK = {
    0x1D: 0x11, 0x35: 0x6F, 0x38: 0x12, 0x47: 0x24, 0x48: 0x26, 0x49: 0x21,
    0x4B: 0x25, 0x4D: 0x27, 0x4F: 0x23, 0x50: 0x28, 0x51: 0x22, 0x52: 0x2D,
    0x53: 0x2E, 0x5B: 0x5B, 0x5C: 0x5C,
}


def _virtual_key_for_binding(scan_code: int, extended: bool) -> int:
    if sys.platform == 'win32':
        try:
            mapped_scan_code = scan_code | (0xE000 if extended else 0)
            virtual_key = int(ctypes.windll.user32.MapVirtualKeyW(mapped_scan_code, 3))
            if virtual_key:
                return virtual_key
        except (AttributeError, OSError):
            pass
    if extended and scan_code in _SMU_WINDOWS_EXTENDED_SCAN_TO_VK:
        return _SMU_WINDOWS_EXTENDED_SCAN_TO_VK[scan_code]
    return _SMU_WINDOWS_SCAN_TO_VK.get(scan_code, 0x0F)


def binding_text(binding) -> str:
    """Return the user-facing label for a persisted binding."""
    normalized = normalize_binding(binding)
    if normalized is None:
        return 'Not assigned'
    modifiers = int(normalized['modifiers'])
    labels = [
        label for flag, label in ((MOD_WIN, 'Win'), (MOD_CTRL, 'Ctrl'), (MOD_ALT, 'Alt'), (MOD_SHIFT, 'Shift'))
        if modifiers & flag
    ]
    key_text = format_smu_virtual_key(
        _virtual_key_for_binding(int(normalized['scan_code']), bool(normalized['extended']))
    )
    return '+'.join([*labels, key_text])


class WindowsHotkeyService(QObject):
    """Poll global key state and dispatch a binding exactly once per key press."""

    activated = pyqtSignal(str)

    _MAPVK_VSC_TO_VK_EX = 3
    _POLL_SECONDS = 0.01

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def set_bindings(self, bindings: Mapping[str, Mapping[str, int]]) -> None:
        """Replace active bindings. Bare and modifier-only keys are supported."""
        self.stop()
        if sys.platform != 'win32':
            return
        clean = {
            str(name): normalized
            for name, spec in bindings.items()
            if (normalized := normalize_binding(spec)) is not None
        }
        if not clean:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(clean,), daemon=True, name='fleasion-fflag-hotkeys'
        )
        self._thread.start()

    def _run(self, bindings: Mapping[str, Mapping[str, int | bool]]) -> None:
        user32 = ctypes.windll.user32
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        user32.MapVirtualKeyW.restype = wintypes.UINT
        translated: dict[str, tuple[int, int]] = {}
        try:
            for name, binding in bindings.items():
                scan_code = int(binding['scan_code'])
                if binding['extended']:
                    scan_code |= 0xE000
                virtual_key = int(user32.MapVirtualKeyW(scan_code, self._MAPVK_VSC_TO_VK_EX))
                if virtual_key:
                    translated[name] = (virtual_key, int(binding['modifiers']))
                else:
                    log_buffer.log('CustomFFlags', f'Could not map the keybind for {name}.')
        except Exception as exc:
            log_buffer.log('CustomFFlags', f'Could not start Windows FastFlag key polling: {exc}')
            return

        def is_pressed(virtual_key: int) -> bool:
            return bool(user32.GetAsyncKeyState(virtual_key) & 0x8000)

        def active_modifiers() -> int:
            result = 0
            if is_pressed(_VK_LSHIFT) or is_pressed(_VK_RSHIFT):
                result |= MOD_SHIFT
            if is_pressed(_VK_LCONTROL) or is_pressed(_VK_RCONTROL):
                result |= MOD_CTRL
            if is_pressed(_VK_LMENU) or is_pressed(_VK_RMENU):
                result |= MOD_ALT
            if is_pressed(_VK_LWIN) or is_pressed(_VK_RWIN):
                result |= MOD_WIN
            return result

        def binding_is_active(virtual_key: int, required_modifiers: int) -> bool:
            modifiers = active_modifiers()
            main_modifier = modifier_mask_for_virtual_key(virtual_key)
            return is_pressed(virtual_key) and (
                modifiers & ~main_modifier
            ) == required_modifiers

        # A newly started poller must treat keys that are already held as its
        # baseline, not as a new press.  This prevents a settings refresh from
        # retriggering the same hotkey until the user releases it first.
        was_active = {
            name: binding_is_active(virtual_key, required_modifiers)
            for name, (virtual_key, required_modifiers) in translated.items()
        }
        while not self._stop.wait(self._POLL_SECONDS):
            for name, (virtual_key, required_modifiers) in translated.items():
                active = binding_is_active(virtual_key, required_modifiers)
                if active and not was_active[name]:
                    self.activated.emit(name)
                was_active[name] = active

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
