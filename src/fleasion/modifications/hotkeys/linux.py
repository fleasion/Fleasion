"""Linux evdev hotkeys for custom FastFlag toggles.

This is the passive ``/dev/input/event*`` reader used by Spencer Macro
Utilities, adapted for Fleasion.  It deliberately observes input rather than
grabbing devices, so configured keys still reach Roblox and the desktop.  Raw
evdev codes also make global FastFlag keybinds work in both X11 and Wayland.
"""

from __future__ import annotations

import errno
import os
import select
import struct
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ...utils import log_buffer
from .names import SMU_MOUSE_WHEEL_DOWN, SMU_MOUSE_WHEEL_UP, format_smu_virtual_key
from .windows import MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN, MODIFIER_MASK


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
    0x110: 0x01, 0x111: 0x02, 0x112: 0x04, 0x113: 0x05, 0x114: 0x06,
    1: 0x1B, 2: 0x31, 3: 0x32, 4: 0x33, 5: 0x34, 6: 0x35, 7: 0x36,
    8: 0x37, 9: 0x38, 10: 0x39, 11: 0x30, 12: 0xBD, 13: 0xBB, 14: 0x08,
    15: 0x09, 16: 0x51, 17: 0x57, 18: 0x45, 19: 0x52, 20: 0x54, 21: 0x59,
    22: 0x55, 23: 0x49, 24: 0x4F, 25: 0x50, 26: 0xDB, 27: 0xDD, 28: 0x0D,
    29: 0x11, 30: 0x41, 31: 0x53, 32: 0x44, 33: 0x46, 34: 0x47, 35: 0x48,
    36: 0x4A, 37: 0x4B, 38: 0x4C, 39: 0xBA, 40: 0xDE, 41: 0xC0, 42: 0x10,
    43: 0xDC, 44: 0x5A, 45: 0x58, 46: 0x43, 47: 0x56, 48: 0x42, 49: 0x4E,
    50: 0x4D, 51: 0xBC, 52: 0xBE, 53: 0xBF, 54: 0x10, 55: 0x6A, 56: 0x12,
    57: 0x20, 58: 0x14, 59: 0x70, 60: 0x71, 61: 0x72, 62: 0x73, 63: 0x74,
    64: 0x75, 65: 0x76, 66: 0x77, 67: 0x78, 68: 0x79, 69: 0x90, 70: 0x91,
    71: 0x67, 72: 0x68, 73: 0x69, 74: 0x6D, 75: 0x64, 76: 0x65, 77: 0x66,
    78: 0x6B, 79: 0x61, 80: 0x62, 81: 0x63, 82: 0x60, 83: 0x6E, 87: 0x7A,
    88: 0x7B, 96: 0x0D, 97: 0x11, 98: 0x6F, 99: 0x2C, 100: 0x12,
    102: 0x24, 103: 0x26, 104: 0x21, 105: 0x25, 106: 0x27, 107: 0x23,
    108: 0x28, 109: 0x22, 110: 0x2D, 111: 0x2E, 119: 0x13, 125: 0x5B,
    126: 0x5C, 127: 0x5D, 183: 0x7C, 184: 0x7D, 185: 0x7E, 186: 0x7F,
    187: 0x80, 188: 0x81, 189: 0x82, 190: 0x83, 191: 0x84, 192: 0x85,
    193: 0x86, 194: 0x87,
}
_PERMISSION_INSTALLER = '''set -eu
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
'''


def modifier_mask_for_evdev_code(code: int) -> int:
    """Return the generic modifier represented by an evdev key code."""
    if code in (_KEY_LEFTSHIFT, _KEY_RIGHTSHIFT):
        return MOD_SHIFT
    if code in (_KEY_LEFTCTRL, _KEY_RIGHTCTRL):
        return MOD_CTRL
    if code in (_KEY_LEFTALT, _KEY_RIGHTALT):
        return MOD_ALT
    if code in (_KEY_LEFTMETA, _KEY_RIGHTMETA):
        return MOD_WIN
    return 0


def normalize_binding(binding) -> dict[str, int | str] | None:
    """Validate a persisted Linux physical-key binding."""
    if not isinstance(binding, Mapping) or binding.get('platform') != 'linux_evdev':
        return None
    kind = binding.get('kind', 'key')
    modifiers = binding.get('modifiers', 0)
    if (
        not isinstance(modifiers, int)
        or isinstance(modifiers, bool)
        or modifiers & ~MODIFIER_MASK
    ):
        return None
    if kind == 'mouse_wheel':
        direction = binding.get('direction')
        if direction not in ('up', 'down'):
            return None
        return {
            'platform': 'linux_evdev', 'kind': 'mouse_wheel',
            'direction': direction, 'modifiers': modifiers,
        }
    scan_code = binding.get('scan_code')
    if (
        kind not in ('key', 'mouse_button')
        or not isinstance(scan_code, int)
        or isinstance(scan_code, bool)
        or not 0 < scan_code <= 0x2FF
        or kind == 'mouse_button' and scan_code not in (0x110, 0x111, 0x112, 0x113, 0x114)
    ):
        return None
    result: dict[str, int | str] = {
        'platform': 'linux_evdev', 'scan_code': scan_code, 'modifiers': modifiers,
    }
    if kind == 'mouse_button':
        result['kind'] = kind
    return result


def binding_text(binding) -> str:
    """Return the SMU-formatted user-facing label for an evdev binding."""
    normalized = normalize_binding(binding)
    if normalized is None:
        return 'Not assigned'
    modifiers = int(normalized['modifiers'])
    labels = [
        label
        for flag, label in ((MOD_WIN, 'Super'), (MOD_CTRL, 'Ctrl'), (MOD_ALT, 'Alt'), (MOD_SHIFT, 'Shift'))
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


def launch_permission_setup() -> None:
    """Start the one-time, Polkit-authorized input-read permission installer."""
    subprocess.Popen(['pkexec', 'sh', '-c', _PERMISSION_INSTALLER], close_fds=True)


class LinuxHotkeyService(QObject):
    """Read global evdev state and emit an activation once per key-down edge."""

    activated = Signal(str)
    key_pressed = Signal(int, int)
    key_released = Signal(int)
    wheel_scrolled = Signal(int, int)

    def __init__(self, parent=None, input_dir: Path = Path('/dev/input')):
        super().__init__(parent)
        self._input_dir = input_dir
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._qt_deleted = threading.Event()
        self._lock = threading.Lock()
        self._fds: dict[int, set[int]] = {}
        self._pressed: set[int] = set()
        self._bindings: dict[str, dict[str, int | str]] = {}
        self._was_active: dict[str, bool] = {}
        self.last_error = ''
        # A custom FastFlag editor is a child widget and can be destroyed
        # without receiving closeEvent. Stop the reader before Qt tears down
        # this signal owner, rather than letting an in-flight event crash its
        # daemon thread.
        self.destroyed.connect(self._on_qt_destroyed)

    def _on_qt_destroyed(self, *_args) -> None:
        self._qt_deleted.set()
        self._stop.set()

    def set_bindings(self, bindings: Mapping[str, Mapping[str, object]]) -> bool:
        """Replace active bindings, returning whether event devices were opened."""
        clean = {
            str(name): normalized
            for name, spec in bindings.items()
            if (normalized := normalize_binding(spec)) is not None
        }
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
        errors: list[str] = []
        for path in paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError as exc:
                errors.append(f'{path.name}: {exc.strerror or exc}')
                continue
            opened[fd] = set()
        if not opened:
            self.last_error = 'Cannot read /dev/input/event*' + (
                f' ({"; ".join(errors)})' if errors else '.'
            )
            return False

        with self._lock:
            self._fds = opened
            self._pressed.clear()
            self._was_active = {
                name: self._binding_is_active(binding) for name, binding in self._bindings.items()
            }
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='fleasion-linux-fflag-hotkeys'
        )
        self._thread.start()
        log_buffer.log('CustomFFlags', f'Linux keybind reader opened {len(opened)} input device(s).')
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                fds = tuple(self._fds)
            if not fds:
                break
            try:
                readable, _, _ = select.select(fds, (), (), 0.05)
            except (OSError, ValueError):
                break
            for fd in readable:
                self._drain_device(fd)

    def _drain_device(self, fd: int) -> None:
        while not self._stop.is_set():
            try:
                raw = os.read(fd, _INPUT_EVENT.size * 16)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                return
            if not raw:
                return
            complete_size = len(raw) - len(raw) % _INPUT_EVENT.size
            for offset in range(0, complete_size, _INPUT_EVENT.size):
                _, _, event_type, code, value = _INPUT_EVENT.unpack_from(raw, offset)
                if event_type == _EV_KEY and value in (0, 1):
                    self._set_key_state(fd, code, value == 1)
                elif event_type == _EV_REL and code == _REL_WHEEL and value:
                    self._handle_wheel(value)

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

    def _emit_signal(self, signal_name: str, *args) -> None:
        """Deliver a queued Qt signal unless this QObject is being deleted."""
        if self._qt_deleted.is_set():
            return
        try:
            getattr(self, signal_name).emit(*args)
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

    def _binding_is_active(self, binding: Mapping[str, int | str], modifiers: int | None = None) -> bool:
        if binding.get('kind') == 'mouse_wheel':
            return False
        code = int(binding['scan_code'])
        if modifiers is None:
            modifiers = self._active_modifiers()
        return code in self._pressed and (
            modifiers & ~modifier_mask_for_evdev_code(code)
        ) == int(binding['modifiers'])

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            fds = tuple(self._fds)
            self._fds.clear()
            self._pressed.clear()
            self._was_active.clear()
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


class LinuxCustomFFlagHotkeyController(QObject):
    """Keep custom FastFlag hotkeys alive independently of the dashboard."""

    toggled = Signal(str)

    def __init__(self, config_manager=None, proxy_master=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._service = LinuxHotkeyService(self)
        self._service.activated.connect(self.toggle_flag)

    @property
    def service(self) -> LinuxHotkeyService:
        return self._service

    def sync(self) -> None:
        if self._config is None or not getattr(self._config, 'custom_fflags_enabled', False):
            self._service.set_bindings({})
            return
        bindings = getattr(self._config, 'custom_fflag_keybinds', {}) or {}
        self._service.set_bindings(bindings if isinstance(bindings, Mapping) else {})

    def toggle_flag(self, name: str) -> None:
        if (
            self._config is None
            or not getattr(self._config, 'custom_fflags_enabled', False)
            or name not in (getattr(self._config, 'custom_fflags', {}) or {})
        ):
            return
        disabled = set(getattr(self._config, 'custom_fflag_disabled', []) or [])
        is_enabled = name in disabled
        if is_enabled:
            disabled.remove(name)
        else:
            disabled.add(name)
        self._config.custom_fflag_disabled = sorted(disabled)
        if self._proxy_master is not None:
            try:
                self._proxy_master.refresh_custom_fflag_interception()
            except Exception as exc:
                log_buffer.log('CustomFFlags', f'Could not refresh proxy interception: {exc}')
        log_buffer.log(
            'CustomFFlags',
            f'Linux keybind turned {name} {"on" if is_enabled else "off"}',
        )
        self.toggled.emit(name)

    def stop(self) -> None:
        self._service.stop()
