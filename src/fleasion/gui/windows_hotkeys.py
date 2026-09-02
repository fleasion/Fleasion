"""Windows scan-code hotkeys for custom FastFlag toggles.

Bindings are captured as physical scan codes and converted to virtual keys for
``GetAsyncKeyState`` polling.  Polling is deliberately used instead of a
keyboard hook: it observes input but cannot consume or block the user's keys.
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from collections.abc import Mapping
from ctypes import wintypes
from typing import TYPE_CHECKING, Protocol, cast

from PySide6.QtCore import QObject, Signal

from fleasion.utils import log_buffer

from .hotkey_names import SMU_MOUSE_WHEEL_DOWN, SMU_MOUSE_WHEEL_UP, format_smu_virtual_key

type HotkeyBinding = dict[str, int | bool | str]


class _DisabledConfigLike(Protocol):
    custom_fflag_disabled: list[str]
    custom_fflag_disabled_folders: list[str]


class _FlagConfigLike(Protocol):
    custom_fflags: dict[str, str]


class _RefreshProxyLike(Protocol):
    def refresh_custom_fflag_interception(self) -> None: ...


class _WinFunction(Protocol):
    argtypes: object
    restype: object

    def __call__(self, *args: object) -> int: ...


class _User32(Protocol):
    GetAsyncKeyState: _WinFunction
    MapVirtualKeyW: _WinFunction
    PeekMessageW: _WinFunction
    TranslateMessage: _WinFunction
    DispatchMessageW: _WinFunction
    UnhookWindowsHookEx: _WinFunction


class _Kernel32(Protocol):
    GetModuleHandleW: _WinFunction


class _Windll(Protocol):
    user32: _User32
    kernel32: _Kernel32


if TYPE_CHECKING:

    def _binding_mapping(value: object) -> Mapping[str, object] | None: ...

    def _windll() -> _Windll: ...

    def _install_mouse_wheel_hook_runtime(
        wheel_events: queue.SimpleQueue[str],
    ) -> tuple[object, object] | None: ...

    def _config_enabled(config: object) -> bool: ...

    def _config_bindings(config: object) -> Mapping[str, Mapping[str, object]]: ...

    def _config_flags(config: object) -> Mapping[str, object]: ...

    def _config_folders(config: object) -> Mapping[str, object]: ...

    def _config_folder_bindings(config: object) -> Mapping[str, Mapping[str, object]]: ...

    def _config_actions(config: object) -> Mapping[str, object]: ...

    def _set_config_flags(config: object, values: dict[str, str]) -> None: ...

    def _config_disabled(config: object) -> list[str]: ...

    def _set_config_disabled(config: object, values: list[str]) -> None: ...

    def _config_disabled_folders(config: object) -> list[str]: ...

    def _set_config_disabled_folders(config: object, values: list[str]) -> None: ...

    def _refresh_proxy(proxy: object) -> None: ...
else:

    def _binding_mapping(value: object) -> Mapping[str, object] | None:
        return value if isinstance(value, Mapping) else None

    def _windll() -> _Windll:
        return ctypes.windll

    def _install_mouse_wheel_hook_runtime(
        wheel_events: queue.SimpleQueue[str],
    ) -> tuple[object, object] | None:
        if sys.platform != 'win32':
            return None

        class POINT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('pt', POINT),
                ('mouseData', wintypes.DWORD),
                ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32 = _windll().user32

        @callback_type
        def callback(code: int, message: int, lparam: int) -> int:
            if code >= 0 and message == 0x020A:  # WM_MOUSEWHEEL
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                if delta:
                    wheel_events.put('up' if delta > 0 else 'down')
            return user32.CallNextHookEx(None, code, message, lparam)

        hook = user32.SetWindowsHookExW(
            14, callback, ctypes.windll.kernel32.GetModuleHandleW(None), 0
        )
        return (hook, callback) if hook else None

    def _config_enabled(config: object) -> bool:
        return bool(getattr(config, 'custom_fflags_enabled', False))

    def _config_bindings(config: object) -> Mapping[str, Mapping[str, object]]:
        bindings = getattr(config, 'custom_fflag_keybinds', {}) or {}
        return bindings if isinstance(bindings, Mapping) else {}

    def _config_flags(config: object) -> Mapping[str, object]:
        flags = getattr(config, 'custom_fflags', {}) or {}
        return flags if isinstance(flags, Mapping) else {}

    def _config_folders(config: object) -> Mapping[str, object]:
        folders = getattr(config, 'custom_fflag_folders', {}) or {}
        return folders if isinstance(folders, Mapping) else {}

    def _config_folder_bindings(config: object) -> Mapping[str, Mapping[str, object]]:
        bindings = getattr(config, 'custom_fflag_folder_keybinds', {}) or {}
        return bindings if isinstance(bindings, Mapping) else {}

    def _config_actions(config: object) -> Mapping[str, object]:
        actions = getattr(config, 'custom_fflag_actions', {}) or {}
        return actions if isinstance(actions, Mapping) else {}

    def _set_config_flags(config: _FlagConfigLike, values: dict[str, str]) -> None:
        config.custom_fflags = values

    def _config_disabled(config: object) -> list[str]:
        disabled = getattr(config, 'custom_fflag_disabled', []) or []
        return [str(value) for value in disabled]

    def _set_config_disabled(config: _DisabledConfigLike, values: list[str]) -> None:
        config.custom_fflag_disabled = values

    def _config_disabled_folders(config: object) -> list[str]:
        disabled = getattr(config, 'custom_fflag_disabled_folders', []) or []
        return [str(value) for value in disabled]

    def _set_config_disabled_folders(config: _DisabledConfigLike, values: list[str]) -> None:
        config.custom_fflag_disabled_folders = values

    def _refresh_proxy(proxy: _RefreshProxyLike) -> None:
        proxy.refresh_custom_fflag_interception()


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
    if virtual_key in {_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT}:
        return MOD_SHIFT
    if virtual_key in {_VK_CONTROL, _VK_LCONTROL, _VK_RCONTROL}:
        return MOD_CTRL
    if virtual_key in {_VK_MENU, _VK_LMENU, _VK_RMENU}:
        return MOD_ALT
    if virtual_key in {_VK_LWIN, _VK_RWIN}:
        return MOD_WIN
    return 0


def normalize_binding(binding: object) -> HotkeyBinding | None:
    """Validate a persisted physical-key binding."""
    binding_map = _binding_mapping(binding)
    if binding_map is None or binding_map.get('platform') not in {None, 'windows'}:
        return None
    kind = binding_map.get('kind', 'key')
    modifiers = binding_map.get('modifiers', 0)
    extended = binding_map.get('extended', False)
    if (
        not isinstance(kind, str)
        or not isinstance(modifiers, int)
        or isinstance(modifiers, bool)
        or modifiers & ~MODIFIER_MASK
    ):
        return None
    if kind == 'mouse_wheel':
        direction = binding_map.get('direction')
        if (
            binding_map.get('platform') != 'windows'
            or not isinstance(direction, str)
            or direction not in {'up', 'down'}
        ):
            return None
        return {
            'platform': 'windows',
            'kind': 'mouse_wheel',
            'direction': direction,
            'modifiers': modifiers,
        }
    scan_code = binding_map.get('scan_code')
    invalid_kind = kind not in {'key', 'mouse_button'}
    invalid_scan_code = (
        not isinstance(scan_code, int) or isinstance(scan_code, bool) or not 0 < scan_code <= 0xFF
    )
    invalid_extended = not isinstance(extended, bool)
    invalid_mouse_button = kind == 'mouse_button' and scan_code not in {1, 2, 4, 5, 6}
    if invalid_kind or invalid_scan_code or invalid_extended or invalid_mouse_button:
        return None
    scan_code = cast('int', scan_code)
    result: HotkeyBinding = {
        'scan_code': scan_code,
        'extended': extended,
        'modifiers': modifiers,
    }
    if kind == 'mouse_button':
        result['platform'] = 'windows'
        result['kind'] = kind
    return result


# SMU's WinScanToVk table from platform/linux/input_evdev_uinput.cpp.  Windows
# itself performs this conversion at runtime; the table keeps stored bindings
# readable in tests and on non-Windows systems with identical SMU names.
_SMU_WINDOWS_SCAN_TO_VK = {
    0x01: 0x1B,
    0x02: 0x31,
    0x03: 0x32,
    0x04: 0x33,
    0x05: 0x34,
    0x06: 0x35,
    0x07: 0x36,
    0x08: 0x37,
    0x09: 0x38,
    0x0A: 0x39,
    0x0B: 0x30,
    0x0C: 0xBD,
    0x0D: 0xBB,
    0x0E: 0x08,
    0x0F: 0x09,
    0x10: 0x51,
    0x11: 0x57,
    0x12: 0x45,
    0x13: 0x52,
    0x14: 0x54,
    0x15: 0x59,
    0x16: 0x55,
    0x17: 0x49,
    0x18: 0x4F,
    0x19: 0x50,
    0x1A: 0xDB,
    0x1B: 0xDD,
    0x1C: 0x0D,
    0x1D: 0xA2,
    0x1E: 0x41,
    0x1F: 0x53,
    0x20: 0x44,
    0x21: 0x46,
    0x22: 0x47,
    0x23: 0x48,
    0x24: 0x4A,
    0x25: 0x4B,
    0x26: 0x4C,
    0x27: 0xBA,
    0x28: 0xDE,
    0x29: 0xC0,
    0x2A: 0xA0,
    0x2B: 0xDC,
    0x2C: 0x5A,
    0x2D: 0x58,
    0x2E: 0x43,
    0x2F: 0x56,
    0x30: 0x42,
    0x31: 0x4E,
    0x32: 0x4D,
    0x33: 0xBC,
    0x34: 0xBE,
    0x35: 0xBF,
    0x36: 0xA1,
    0x37: 0x6A,
    0x38: 0xA4,
    0x39: 0x20,
    0x3A: 0x14,
    0x3B: 0x70,
    0x3C: 0x71,
    0x3D: 0x72,
    0x3E: 0x73,
    0x3F: 0x74,
    0x40: 0x75,
    0x41: 0x76,
    0x42: 0x77,
    0x43: 0x78,
    0x44: 0x79,
    0x45: 0x90,
    0x46: 0x91,
    0x47: 0x67,
    0x48: 0x68,
    0x49: 0x69,
    0x4A: 0x6D,
    0x4B: 0x64,
    0x4C: 0x65,
    0x4D: 0x66,
    0x4E: 0x6B,
    0x4F: 0x61,
    0x50: 0x62,
    0x51: 0x63,
    0x52: 0x60,
    0x53: 0x6E,
    0x57: 0x7A,
    0x58: 0x7B,
}
_SMU_WINDOWS_EXTENDED_SCAN_TO_VK = {
    0x1D: 0xA3,
    0x35: 0x6F,
    0x38: 0xA5,
    0x47: 0x24,
    0x48: 0x26,
    0x49: 0x21,
    0x4B: 0x25,
    0x4D: 0x27,
    0x4F: 0x23,
    0x50: 0x28,
    0x51: 0x22,
    0x52: 0x2D,
    0x53: 0x2E,
    0x5B: 0x5B,
    0x5C: 0x5C,
}


def _virtual_key_for_binding(scan_code: int, extended: bool) -> int:
    if sys.platform == 'win32':
        try:
            mapped_scan_code = scan_code | (0xE000 if extended else 0)
            virtual_key = int(_windll().user32.MapVirtualKeyW(mapped_scan_code, 3))
            if virtual_key:
                return virtual_key
        except AttributeError, OSError:
            pass
    if extended and scan_code in _SMU_WINDOWS_EXTENDED_SCAN_TO_VK:
        return _SMU_WINDOWS_EXTENDED_SCAN_TO_VK[scan_code]
    return _SMU_WINDOWS_SCAN_TO_VK.get(scan_code, 0x0F)


def binding_text(binding: object) -> str:
    """Return the user-facing label for a persisted binding."""
    normalized = normalize_binding(binding)
    if normalized is None:
        return 'Not assigned'
    modifiers = int(normalized['modifiers'])
    labels = [
        label
        for flag, label in (
            (MOD_WIN, 'Win'),
            (MOD_CTRL, 'Ctrl'),
            (MOD_ALT, 'Alt'),
            (MOD_SHIFT, 'Shift'),
        )
        if modifiers & flag
    ]
    if normalized.get('kind') == 'mouse_wheel':
        key_text = format_smu_virtual_key(
            SMU_MOUSE_WHEEL_UP if normalized['direction'] == 'up' else SMU_MOUSE_WHEEL_DOWN
        )
    elif normalized.get('kind') == 'mouse_button':
        key_text = format_smu_virtual_key(int(normalized['scan_code']))
    else:
        key_text = format_smu_virtual_key(
            _virtual_key_for_binding(int(normalized['scan_code']), bool(normalized['extended']))
        )
    return '+'.join([*labels, key_text])


class WindowsHotkeyService(QObject):
    """Poll global key state and dispatch a binding exactly once per key press."""

    activated = Signal(str)

    _MAPVK_VSC_TO_VK_EX = 3
    _POLL_SECONDS = 0.01

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._qt_deleted = threading.Event()
        self._bindings: dict[str, HotkeyBinding] = {}
        self.destroyed.connect(self._on_qt_destroyed)

    def _on_qt_destroyed(self, *_args: object) -> None:
        self._qt_deleted.set()
        self._stop.set()

    def set_bindings(self, bindings: Mapping[str, Mapping[str, object]]) -> None:
        """Replace active bindings without restarting an unchanged poller."""
        clean = {
            str(name): normalized
            for name, spec in bindings.items()
            if (normalized := normalize_binding(spec)) is not None
        }
        if clean == self._bindings and (
            not clean or (self._thread is not None and self._thread.is_alive())
        ):
            return
        self.stop()
        self._bindings = clean
        if sys.platform != 'win32' or not clean:
            return
        if self._thread is not None and self._thread.is_alive():
            log_buffer.log(
                'CustomFFlags',
                'Previous Windows FastFlag poller did not stop in time; skipping restart.',
            )
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(clean,), daemon=True, name='fleasion-fflag-hotkeys'
        )
        self._thread.start()

    def _translate_bindings(
        self, user32: _User32, bindings: Mapping[str, HotkeyBinding]
    ) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[str, int]]]:
        translated: dict[str, tuple[int, int]] = {}
        wheel_bindings: dict[str, tuple[str, int]] = {}
        for name, binding in bindings.items():
            if binding.get('kind') == 'mouse_wheel':
                wheel_bindings[name] = (str(binding['direction']), int(binding['modifiers']))
                continue
            if binding.get('kind') == 'mouse_button':
                translated[name] = (int(binding['scan_code']), int(binding['modifiers']))
                continue
            scan_code = int(binding['scan_code'])
            if binding['extended']:
                scan_code |= 0xE000
            virtual_key = int(user32.MapVirtualKeyW(scan_code, self._MAPVK_VSC_TO_VK_EX))
            if virtual_key:
                translated[name] = (virtual_key, int(binding['modifiers']))
            else:
                log_buffer.log('CustomFFlags', f'Could not map the keybind for {name}.')
        return translated, wheel_bindings

    def _run(self, bindings: Mapping[str, HotkeyBinding]) -> None:
        user32 = _windll().user32
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        user32.MapVirtualKeyW.restype = wintypes.UINT
        try:
            translated, wheel_bindings = self._translate_bindings(user32, bindings)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            ctypes.ArgumentError,
        ) as exc:
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

        def binding_is_active(virtual_key: int, required_modifiers: int, modifiers: int) -> bool:
            main_modifier = modifier_mask_for_virtual_key(virtual_key)
            return is_pressed(virtual_key) and (modifiers & ~main_modifier) == required_modifiers

        wheel_events: queue.SimpleQueue[str] = queue.SimpleQueue()
        mouse_hook = self._install_mouse_wheel_hook(wheel_events) if wheel_bindings else None

        # A newly started poller must treat keys that are already held as its
        # baseline, not as a new press.  This prevents a settings refresh from
        # retriggering the same hotkey until the user releases it first.
        initial_modifiers = active_modifiers()
        was_active = {
            name: binding_is_active(virtual_key, required_modifiers, initial_modifiers)
            for name, (virtual_key, required_modifiers) in translated.items()
        }
        try:
            while not self._stop.wait(self._POLL_SECONDS):
                self._pump_windows_messages()
                while not wheel_events.empty():
                    direction = wheel_events.get_nowait()
                    modifiers = active_modifiers()
                    for name, (required_direction, required_modifiers) in wheel_bindings.items():
                        if direction == required_direction and modifiers == required_modifiers:
                            self._emit_activation(name)
                modifiers = active_modifiers()
                for name, (virtual_key, required_modifiers) in translated.items():
                    active = binding_is_active(virtual_key, required_modifiers, modifiers)
                    if active and not was_active[name]:
                        self._emit_activation(name)
                    was_active[name] = active
        finally:
            if mouse_hook is not None:
                _windll().user32.UnhookWindowsHookEx(mouse_hook[0])

    def _emit_activation(self, name: str) -> None:
        """Emit only while the Qt signal owner is still alive."""
        if self._qt_deleted.is_set():
            return
        try:
            self.activated.emit(name)
        except RuntimeError:
            self._qt_deleted.set()
            self._stop.set()

    @staticmethod
    def _pump_windows_messages() -> None:
        if sys.platform != 'win32':
            return
        message = wintypes.MSG()
        user32 = ctypes.windll.user32
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    @staticmethod
    def _install_mouse_wheel_hook(
        wheel_events: queue.SimpleQueue[str],
    ) -> tuple[object, object] | None:
        """Port SMU's global wheel pseudo-keys through a passive LL mouse hook."""
        return _install_mouse_wheel_hook_runtime(wheel_events)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if thread is None or not thread.is_alive():
            self._thread = None


class WindowsCustomFFlagHotkeyController(QObject):
    """Keep custom FastFlag hotkeys alive independently of the dashboard."""

    toggled = Signal(str)

    def __init__(
        self,
        config_manager: object | None = None,
        proxy_master: object | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._service = WindowsHotkeyService(self)
        self._service.activated.connect(self.toggle_target)

    @property
    def service(self) -> WindowsHotkeyService:
        return self._service

    def sync(self) -> None:
        if self._config is None or not _config_enabled(self._config):
            self._service.set_bindings({})
            return
        bindings = dict(_config_bindings(self._config))
        bindings.update(
            {
                f'folder:{name}': binding
                for name, binding in _config_folder_bindings(self._config).items()
            }
        )
        for name, action_value in _config_actions(self._config).items():
            action = _binding_mapping(action_value)
            if action is None:
                continue
            keybind = _binding_mapping(action.get('keybind'))
            if keybind is not None:
                bindings[f'action:{name}'] = keybind
        self._service.set_bindings(bindings)

    def toggle_target(self, target: str) -> None:
        if target.startswith('folder:'):
            self.toggle_folder(target.removeprefix('folder:'))
            return
        if target.startswith('action:'):
            self.apply_action(target.removeprefix('action:'))
            return
        self.toggle_flag(target)

    def apply_action(self, name: str) -> None:
        if self._config is None or not _config_enabled(self._config):
            return
        action = _binding_mapping(_config_actions(self._config).get(name))
        if action is None:
            return
        action_flags = _binding_mapping(action.get('flags'))
        if not action_flags:
            return
        updated = {str(flag): str(value) for flag, value in _config_flags(self._config).items()}
        updated.update({str(flag): str(value) for flag, value in action_flags.items()})
        _set_config_flags(self._config, updated)
        log_buffer.log('CustomFFlags', f'Windows keybind applied action {name}')
        self.toggled.emit(f'action:{name}')

    def toggle_flag(self, name: str) -> None:
        if (
            self._config is None
            or not _config_enabled(self._config)
            or name not in _config_flags(self._config)
        ):
            return
        disabled = set(_config_disabled(self._config))
        is_enabled = name in disabled
        if is_enabled:
            disabled.remove(name)
        else:
            disabled.add(name)
        _set_config_disabled(self._config, sorted(disabled))
        if self._proxy_master is not None:
            try:
                _refresh_proxy(self._proxy_master)
            except Exception as exc:  # ruff: ignore[blind-except]
                log_buffer.log('CustomFFlags', f'Could not refresh proxy interception: {exc}')
        log_buffer.log(
            'CustomFFlags',
            f'Windows keybind turned {name} {"on" if is_enabled else "off"}',
        )
        self.toggled.emit(name)

    def toggle_folder(self, name: str) -> None:
        if (
            self._config is None
            or not _config_enabled(self._config)
            or name not in _config_folders(self._config)
        ):
            return
        disabled = set(_config_disabled_folders(self._config))
        is_enabled = name in disabled
        if is_enabled:
            disabled.remove(name)
        else:
            disabled.add(name)
        _set_config_disabled_folders(self._config, sorted(disabled))
        if self._proxy_master is not None:
            try:
                _refresh_proxy(self._proxy_master)
            except Exception as exc:  # ruff: ignore[blind-except]
                log_buffer.log('CustomFFlags', f'Could not refresh proxy interception: {exc}')
        log_buffer.log(
            'CustomFFlags',
            f'Windows keybind turned folder {name} {"on" if is_enabled else "off"}',
        )
        self.toggled.emit(f'folder:{name}')

    def stop(self) -> None:
        self._service.stop()
