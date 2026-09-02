"""Linux evdev hotkeys for custom FastFlag toggles.

This is the passive ``/dev/input/event*`` reader used by Spencer Macro
Utilities, adapted for Fleasion.  It deliberately observes input rather than
grabbing devices, so configured keys still reach Roblox and the desktop.  Raw
evdev codes also make global FastFlag keybinds work in both X11 and Wayland.
"""

from __future__ import annotations

import contextlib
import errno
import os
import select
import struct
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from PySide6.QtCore import QObject, Signal

from fleasion.utils import log_buffer

from .hotkey_names import SMU_MOUSE_WHEEL_DOWN, SMU_MOUSE_WHEEL_UP, format_smu_virtual_key
from .windows_hotkeys import MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN, MODIFIER_MASK

type HotkeyBinding = dict[str, int | str]


class _DisabledConfigLike(Protocol):
    custom_fflag_disabled: list[str]
    custom_fflag_disabled_folders: list[str]


class _FlagConfigLike(Protocol):
    custom_fflags: dict[str, str]


class _RefreshProxyLike(Protocol):
    def refresh_custom_fflag_interception(self) -> None: ...


class _SignalLike(Protocol):
    def emit(self, *args: object) -> None: ...


if TYPE_CHECKING:

    def _binding_mapping(value: object) -> Mapping[str, object] | None: ...

    def _qt_signal(obj: object, name: str) -> _SignalLike: ...

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

    def _qt_signal(obj: object, name: str) -> _SignalLike:
        return getattr(obj, name)

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


# ``struct input_event`` on Linux.  Native alignment keeps this correct for
# both 32-bit and 64-bit Python builds.
_INPUT_EVENT = struct.Struct('@llHHi')
_EV_KEY = 0x01
_EV_REL = 0x02
_REL_WHEEL = 0x08
_KEY_LEFTCTRL = 29
_KEY_LEFTSHIFT = 42
_KEY_LEFTALT = 56
_KEY_RIGHTCTRL = 97
_KEY_RIGHTALT = 100
_KEY_LEFTMETA = 125
_KEY_RIGHTMETA = 126
_KEY_RIGHTSHIFT = 54

# Direct port of SMU's ``evdev_to_win_vkey`` table from core/keymapping.h.
# Retaining this single source of truth means the Linux UI names every key the
# same way as SMU instead of maintaining an incomplete, separate label list.
_SMU_EVDEV_TO_VK = {
    0x110: 0x01,
    0x111: 0x02,
    0x112: 0x04,
    0x113: 0x05,
    0x114: 0x06,
    1: 0x1B,
    2: 0x31,
    3: 0x32,
    4: 0x33,
    5: 0x34,
    6: 0x35,
    7: 0x36,
    8: 0x37,
    9: 0x38,
    10: 0x39,
    11: 0x30,
    12: 0xBD,
    13: 0xBB,
    14: 0x08,
    15: 0x09,
    16: 0x51,
    17: 0x57,
    18: 0x45,
    19: 0x52,
    20: 0x54,
    21: 0x59,
    22: 0x55,
    23: 0x49,
    24: 0x4F,
    25: 0x50,
    26: 0xDB,
    27: 0xDD,
    28: 0x0D,
    29: 0x11,
    30: 0x41,
    31: 0x53,
    32: 0x44,
    33: 0x46,
    34: 0x47,
    35: 0x48,
    36: 0x4A,
    37: 0x4B,
    38: 0x4C,
    39: 0xBA,
    40: 0xDE,
    41: 0xC0,
    42: 0x10,
    43: 0xDC,
    44: 0x5A,
    45: 0x58,
    46: 0x43,
    47: 0x56,
    48: 0x42,
    49: 0x4E,
    50: 0x4D,
    51: 0xBC,
    52: 0xBE,
    53: 0xBF,
    54: 0x10,
    55: 0x6A,
    56: 0x12,
    57: 0x20,
    58: 0x14,
    59: 0x70,
    60: 0x71,
    61: 0x72,
    62: 0x73,
    63: 0x74,
    64: 0x75,
    65: 0x76,
    66: 0x77,
    67: 0x78,
    68: 0x79,
    69: 0x90,
    70: 0x91,
    71: 0x67,
    72: 0x68,
    73: 0x69,
    74: 0x6D,
    75: 0x64,
    76: 0x65,
    77: 0x66,
    78: 0x6B,
    79: 0x61,
    80: 0x62,
    81: 0x63,
    82: 0x60,
    83: 0x6E,
    87: 0x7A,
    88: 0x7B,
    96: 0x0D,
    97: 0x11,
    98: 0x6F,
    99: 0x2C,
    100: 0x12,
    102: 0x24,
    103: 0x26,
    104: 0x21,
    105: 0x25,
    106: 0x27,
    107: 0x23,
    108: 0x28,
    109: 0x22,
    110: 0x2D,
    111: 0x2E,
    119: 0x13,
    125: 0x5B,
    126: 0x5C,
    127: 0x5D,
    183: 0x7C,
    184: 0x7D,
    185: 0x7E,
    186: 0x7F,
    187: 0x80,
    188: 0x81,
    189: 0x82,
    190: 0x83,
    191: 0x84,
    192: 0x85,
    193: 0x86,
    194: 0x87,
}
_PERMISSION_INSTALLER = """set -eu
target_user="${SMU_TARGET_USER:-}"
if [ -z "$target_user" ] && [ -n "${PKEXEC_UID:-}" ]; then
    target_user="$(getent passwd "$PKEXEC_UID" | cut -d: -f1 || true)"
fi
if [ -z "$target_user" ] || [ "$target_user" = root ]; then
    echo "Could not determine the desktop user." >&2
    exit 1
fi
getent group fleasion-input >/dev/null 2>&1 || groupadd --system fleasion-input
usermod -aG fleasion-input "$target_user"
install -d -m 0755 /etc/udev/rules.d
cat > /etc/udev/rules.d/70-fleasion-hotkeys.rules <<'RULES'
SUBSYSTEM=="input", KERNEL=="event*", MODE="0660", GROUP="fleasion-input", TAG+="uaccess"
RULES
udevadm control --reload-rules
udevadm trigger
if command -v setfacl >/dev/null 2>&1; then
    for node in /dev/input/event*; do
        [ -e "$node" ] && setfacl -m "u:$target_user:rw" "$node" || true
    done
fi
echo "Installed Fleasion Linux keybind permissions for $target_user."
"""


def modifier_mask_for_evdev_code(code: int) -> int:
    """Return the generic modifier represented by an evdev key code."""
    if code in {_KEY_LEFTSHIFT, _KEY_RIGHTSHIFT}:
        return MOD_SHIFT
    if code in {_KEY_LEFTCTRL, _KEY_RIGHTCTRL}:
        return MOD_CTRL
    if code in {_KEY_LEFTALT, _KEY_RIGHTALT}:
        return MOD_ALT
    if code in {_KEY_LEFTMETA, _KEY_RIGHTMETA}:
        return MOD_WIN
    return 0


def normalize_binding(binding: object) -> HotkeyBinding | None:
    """Validate a persisted Linux physical-key binding."""
    binding_map = _binding_mapping(binding)
    if binding_map is None or binding_map.get('platform') != 'linux_evdev':
        return None
    kind = binding_map.get('kind', 'key')
    modifiers = binding_map.get('modifiers', 0)
    if (
        not isinstance(kind, str)
        or not isinstance(modifiers, int)
        or isinstance(modifiers, bool)
        or modifiers & ~MODIFIER_MASK
    ):
        return None
    if kind == 'mouse_wheel':
        direction = binding_map.get('direction')
        if not isinstance(direction, str) or direction not in {'up', 'down'}:
            return None
        return {
            'platform': 'linux_evdev',
            'kind': 'mouse_wheel',
            'direction': direction,
            'modifiers': modifiers,
        }
    scan_code = binding_map.get('scan_code')
    invalid_kind = kind not in {'key', 'mouse_button'}
    invalid_scan_code = (
        not isinstance(scan_code, int) or isinstance(scan_code, bool) or not 0 < scan_code <= 0x2FF
    )
    invalid_mouse_button = kind == 'mouse_button' and scan_code not in {
        0x110,
        0x111,
        0x112,
        0x113,
        0x114,
    }
    if invalid_kind or invalid_scan_code or invalid_mouse_button:
        return None
    scan_code = cast('int', scan_code)
    result: HotkeyBinding = {
        'platform': 'linux_evdev',
        'scan_code': scan_code,
        'modifiers': modifiers,
    }
    if kind == 'mouse_button':
        result['kind'] = kind
    return result


def binding_text(binding: object) -> str:
    """Return the SMU-formatted user-facing label for an evdev binding."""
    normalized = normalize_binding(binding)
    if normalized is None:
        return 'Not assigned'
    modifiers = int(normalized['modifiers'])
    labels = [
        label
        for flag, label in (
            (MOD_WIN, 'Super'),
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
    else:
        scan_code = int(normalized['scan_code'])
        key_text = format_smu_virtual_key(_SMU_EVDEV_TO_VK.get(scan_code, 0x0F))
    return '+'.join([*labels, key_text])


def _trusted_system_executable(name: str) -> str:
    for directory in ('/usr/bin', '/bin', '/usr/sbin', '/sbin'):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    msg = f'Required system executable not found in trusted paths: {name}'
    raise FileNotFoundError(msg)


def launch_permission_setup() -> None:
    """Start the one-time, Polkit-authorized input-read permission installer."""
    pkexec = _trusted_system_executable('pkexec')
    shell = _trusted_system_executable('sh')
    subprocess.Popen([pkexec, shell, '-c', _PERMISSION_INSTALLER], close_fds=True)


class LinuxHotkeyService(QObject):
    """Read global evdev state and emit an activation once per key-down edge."""

    activated = Signal(str)
    key_pressed = Signal(int, int)
    key_released = Signal(int)
    wheel_scrolled = Signal(int, int)

    def __init__(self, parent: QObject | None = None, input_dir: Path = Path('/dev/input')) -> None:
        super().__init__(parent)
        self._input_dir = input_dir
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._qt_deleted = threading.Event()
        self._lock = threading.Lock()
        self._fds: dict[int, set[int]] = {}
        self._fd_paths: dict[int, Path] = {}
        self._pressed: set[int] = set()
        self._bindings: dict[str, HotkeyBinding] = {}
        self._was_active: dict[str, bool] = {}
        self.last_error = ''
        # A custom FastFlag editor is a child widget and can be destroyed
        # without receiving closeEvent. Stop the reader before Qt tears down
        # this signal owner, rather than letting an in-flight event crash its
        # daemon thread.
        self.destroyed.connect(self._on_qt_destroyed)

    def _on_qt_destroyed(self, *_args: object) -> None:
        self._qt_deleted.set()
        self._stop.set()

    def set_bindings(self, bindings: Mapping[str, Mapping[str, object]]) -> bool:
        """Replace active bindings without reopening unchanged evdev readers."""
        clean = {
            str(name): normalized
            for name, spec in bindings.items()
            if (normalized := normalize_binding(spec)) is not None
        }
        if clean == self._bindings and (
            not clean or (self._thread is not None and self._thread.is_alive())
        ):
            return True
        self.stop()
        self._bindings = clean
        if not clean or not sys.platform.startswith('linux'):
            return True
        return self._start()

    def begin_capture(self) -> bool:
        """Ensure global input is available while a keybind dialog is open."""
        if not sys.platform.startswith('linux'):
            return False
        if self._thread is not None:
            return True
        return self._start()

    def _start(self) -> bool:
        self.last_error = ''
        try:
            paths = sorted(self._input_dir.glob('event*'))
        except OSError as exc:
            self.last_error = f'Cannot access {self._input_dir}: {exc.strerror or exc}'
            return False
        if not paths:
            self.last_error = f'No {self._input_dir}/event* input devices were found.'
            return False

        opened: dict[int, set[int]] = {}
        opened_paths: dict[int, Path] = {}
        errors: list[str] = []
        for path in paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError as exc:
                errors.append(f'{path.name}: {exc.strerror or exc}')
                continue
            opened[fd] = set()
            opened_paths[fd] = path
        if not opened:
            self.last_error = 'Cannot read /dev/input/event*' + (
                f' ({"; ".join(errors)})' if errors else '.'
            )
            return False

        with self._lock:
            self._fds = opened
            self._fd_paths = opened_paths
            self._pressed.clear()
            self._was_active = {
                name: self._binding_is_active(binding) for name, binding in self._bindings.items()
            }
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='fleasion-linux-fflag-hotkeys'
        )
        self._thread.start()
        log_buffer.log(
            'CustomFFlags', f'Linux keybind reader opened {len(opened)} input device(s).'
        )
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                fds = tuple(self._fds)
            if not fds:
                break
            try:
                readable, _, _ = select.select(fds, (), (), 0.05)
            except OSError, ValueError:
                break
            for fd in readable:
                self._drain_device(fd)

    def _drain_device(self, fd: int) -> None:
        while not self._stop.is_set():
            try:
                raw = os.read(fd, _INPUT_EVENT.size * 16)
            except OSError as exc:
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return
                self._drop_device(fd, exc)
                return
            if not raw:
                self._drop_device(fd, 'end-of-device')
                return
            complete_size = len(raw) - len(raw) % _INPUT_EVENT.size
            for offset in range(0, complete_size, _INPUT_EVENT.size):
                _, _, event_type, code, value = _INPUT_EVENT.unpack_from(raw, offset)
                if event_type == _EV_KEY and value in {0, 1}:
                    self._set_key_state(fd, code, value == 1)
                elif event_type == _EV_REL and code == _REL_WHEEL and value:
                    self._handle_wheel(value)

    def _drop_device(self, fd: int, reason: OSError | str) -> None:
        """Remove an input device that was disconnected or became unusable.

        Linux evdev descriptors remain readable after a device node is removed,
        but every read then returns ``ENODEV``.  Leaving that descriptor in the
        select set turns the reader into a tight loop that consumes a CPU core.
        """
        with self._lock:
            if fd not in self._fds:
                return
            path = self._fd_paths.pop(fd, Path(f'fd:{fd}'))
            self._fds.pop(fd, None)
            self._pressed = set().union(*self._fds.values()) if self._fds else set()
            self._was_active = {
                name: self._binding_is_active(binding) for name, binding in self._bindings.items()
            }
        with contextlib.suppress(OSError):
            os.close(fd)
        detail = reason.strerror or reason if isinstance(reason, OSError) else reason
        log_buffer.log(
            'CustomFFlags',
            f'Linux keybind reader dropped {path.name} after input-device error: {detail}',
        )

    def _set_key_state(self, fd: int, code: int, pressed: bool) -> None:
        with self._lock:
            device_keys = self._fds.get(fd)
            if device_keys is None:
                return
            was_pressed = code in self._pressed
            if pressed:
                device_keys.add(code)
            else:
                device_keys.discard(code)
            self._pressed = set().union(*self._fds.values()) if self._fds else set()
            is_pressed = code in self._pressed
            if was_pressed == is_pressed:
                return
            active_modifiers = self._active_modifiers()
            activations: list[str] = []
            for name, binding in self._bindings.items():
                active = self._binding_is_active(binding, active_modifiers)
                if active and not self._was_active.get(name, False):
                    activations.append(name)
                self._was_active[name] = active

        if is_pressed:
            self._emit_signal('key_pressed', code, active_modifiers)
        else:
            self._emit_signal('key_released', code)
        for name in activations:
            self._emit_signal('activated', name)

    def _emit_signal(self, signal_name: str, *args: object) -> None:
        """Deliver a queued Qt signal unless this QObject is being deleted."""
        if self._qt_deleted.is_set():
            return
        try:
            _qt_signal(self, signal_name).emit(*args)
        except RuntimeError:
            # Qt may delete a parent-owned QObject between the check above and
            # emit(). The event was already obsolete, so just terminate the
            # passive reader; never let a daemon thread report an exception.
            self._qt_deleted.set()
            self._stop.set()

    def _active_modifiers(self) -> int:
        result = 0
        for code in self._pressed:
            result |= modifier_mask_for_evdev_code(code)
        return result

    def _handle_wheel(self, delta: int) -> None:
        with self._lock:
            modifiers = self._active_modifiers()
            direction = 'up' if delta > 0 else 'down'
            activations = [
                name
                for name, binding in self._bindings.items()
                if binding.get('kind') == 'mouse_wheel'
                and binding.get('direction') == direction
                and int(binding['modifiers']) == modifiers
            ]
        wheel_code = SMU_MOUSE_WHEEL_UP if delta > 0 else SMU_MOUSE_WHEEL_DOWN
        self._emit_signal('wheel_scrolled', wheel_code, modifiers)
        for name in activations:
            self._emit_signal('activated', name)

    def _binding_is_active(self, binding: HotkeyBinding, modifiers: int | None = None) -> bool:
        if binding.get('kind') == 'mouse_wheel':
            return False
        code = int(binding['scan_code'])
        if modifiers is None:
            modifiers = self._active_modifiers()
        return code in self._pressed and (modifiers & ~modifier_mask_for_evdev_code(code)) == int(
            binding['modifiers']
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            fds = tuple(self._fds)
            self._fds.clear()
            self._fd_paths.clear()
            self._pressed.clear()
            self._was_active.clear()
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)


class LinuxCustomFFlagHotkeyController(QObject):
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
        self._service = LinuxHotkeyService(self)
        self._service.activated.connect(self.toggle_target)

    @property
    def service(self) -> LinuxHotkeyService:
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
        log_buffer.log('CustomFFlags', f'Linux keybind applied action {name}')
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
            f'Linux keybind turned {name} {"on" if is_enabled else "off"}',
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
            f'Linux keybind turned folder {name} {"on" if is_enabled else "off"}',
        )
        self.toggled.emit(f'folder:{name}')

    def stop(self) -> None:
        self._service.stop()
