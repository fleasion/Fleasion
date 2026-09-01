import errno
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast

import pytest

from fleasion.gui.linux_hotkeys import (
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    LinuxCustomFFlagHotkeyController,
    LinuxHotkeyService,
    binding_text,
    modifier_mask_for_evdev_code,
    normalize_binding,
)
from fleasion.gui.windows_hotkeys import binding_text as windows_binding_text

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Linux-only hotkey translation tests'
)


def _fds(service: LinuxHotkeyService) -> dict[int, set[int]]:
    return cast('dict[int, set[int]]', service.__dict__['_fds'])


def _fd_paths(service: LinuxHotkeyService) -> dict[int, Path]:
    return cast('dict[int, Path]', service.__dict__['_fd_paths'])


def _drain(service: LinuxHotkeyService, fd: int) -> None:
    callback = cast('Callable[[int], None]', getattr(service, '_drain_device'))
    callback(fd)


def _run(service: LinuxHotkeyService) -> None:
    callback = cast('Callable[[], None]', getattr(service, '_run'))
    callback()


def _raise_enodev(*_args: object) -> Never:
    raise OSError(errno.ENODEV, 'No such device')


def _record_close(values: list[int]) -> Callable[[int], None]:
    def close(fd: int) -> None:
        values.append(fd)

    return close


def _select_once(
    calls: list[tuple[Sequence[int], Sequence[int], Sequence[int], float]],
) -> Callable[
    [Sequence[int], Sequence[int], Sequence[int], float],
    tuple[list[int], list[int], list[int]],
]:
    def select_call(
        read: Sequence[int],
        write: Sequence[int],
        error: Sequence[int],
        timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        calls.append((read, write, error, timeout))
        return [43], [], []

    return select_call


def _noop_close(_fd: int) -> None:
    return None


def _record_toggled(values: list[str]) -> Callable[[str], None]:
    def record(name: str) -> None:
        values.append(name)

    return record


def _refresh_proxy(proxy: SimpleNamespace) -> None:
    proxy.refresh_calls += 1


def test_linux_keybinding_uses_tagged_evdev_codes() -> None:
    assert normalize_binding(
        {'platform': 'linux_evdev', 'scan_code': 30, 'modifiers': MOD_CTRL | MOD_SHIFT}
    ) == {'platform': 'linux_evdev', 'scan_code': 30, 'modifiers': MOD_CTRL | MOD_SHIFT}
    assert (
        binding_text(
            {'platform': 'linux_evdev', 'scan_code': 30, 'modifiers': MOD_CTRL | MOD_SHIFT}
        )
        == 'Ctrl+Shift+A'
    )
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 76, 'modifiers': 0}) == 'Numpad 5'


def test_linux_keybinding_uses_the_complete_smu_evdev_translation_table() -> None:
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 1, 'modifiers': 0}) == 'Escape'
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 183, 'modifiers': 0}) == 'F13'
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 194, 'modifiers': 0}) == 'F24'
    assert (
        binding_text({'platform': 'linux_evdev', 'scan_code': 0x110, 'modifiers': 0})
        == 'Mouse Left'
    )
    # SMU's KeyNameFallback also deliberately retains its hexadecimal label
    # for keypad operators which do not have a named virtual key entry.
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 98, 'modifiers': 0}) == '0x6F'


def test_windows_and_linux_bindings_share_smu_key_names() -> None:
    assert windows_binding_text({'scan_code': 0x1E, 'extended': False, 'modifiers': 0}) == 'A'
    assert (
        windows_binding_text({'scan_code': 0x4C, 'extended': False, 'modifiers': 0}) == 'Numpad 5'
    )
    assert windows_binding_text({'scan_code': 0x4B, 'extended': True, 'modifiers': 0}) == 'Left'
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 30, 'modifiers': 0}) == 'A'
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 76, 'modifiers': 0}) == 'Numpad 5'
    assert binding_text({'platform': 'linux_evdev', 'scan_code': 105, 'modifiers': 0}) == 'Left'
    assert (
        windows_binding_text(
            {
                'platform': 'windows',
                'kind': 'mouse_button',
                'scan_code': 5,
                'extended': False,
                'modifiers': 0,
            }
        )
        == 'Mouse X1'
    )
    assert (
        windows_binding_text(
            {'platform': 'windows', 'kind': 'mouse_wheel', 'direction': 'down', 'modifiers': 0}
        )
        == 'Mouse Wheel Down'
    )


def test_linux_keybinding_rejects_windows_or_malformed_settings() -> None:
    assert normalize_binding({'scan_code': 30, 'extended': False, 'modifiers': 0}) is None
    assert normalize_binding({'platform': 'windows', 'scan_code': 30, 'modifiers': 0}) is None
    assert normalize_binding({'platform': 'linux_evdev', 'scan_code': 0, 'modifiers': 0}) is None


def test_linux_mouse_buttons_and_wheel_use_smu_codes() -> None:
    mouse_four = {
        'platform': 'linux_evdev',
        'kind': 'mouse_button',
        'scan_code': 0x113,
        'modifiers': 0,
    }
    wheel_up = {'platform': 'linux_evdev', 'kind': 'mouse_wheel', 'direction': 'up', 'modifiers': 0}
    assert normalize_binding(mouse_four) == mouse_four
    assert binding_text(mouse_four) == 'Mouse X1'
    assert normalize_binding(wheel_up) == wheel_up
    assert binding_text(wheel_up) == 'Mouse Wheel Up'


def test_linux_modifier_codes_are_combined_generically() -> None:
    assert modifier_mask_for_evdev_code(29) == MOD_CTRL
    assert modifier_mask_for_evdev_code(100) == MOD_ALT
    assert modifier_mask_for_evdev_code(42) == MOD_SHIFT
    assert modifier_mask_for_evdev_code(30) == 0


def test_linux_hotkey_controller_toggles_without_the_dashboard() -> None:
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagExample': 'True'},
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
    )
    proxy = SimpleNamespace(refresh_calls=0)

    def refresh() -> None:
        _refresh_proxy(proxy)

    proxy.refresh_custom_fflag_interception = refresh
    controller = LinuxCustomFFlagHotkeyController(config, proxy)
    toggled: list[str] = []
    controller.toggled.connect(_record_toggled(toggled))

    controller.service.activated.emit('FFlagExample')

    assert config.custom_fflag_disabled == ['FFlagExample']
    assert proxy.refresh_calls == 1
    assert toggled == ['FFlagExample']
    controller.stop()


def test_linux_hotkey_reader_drops_disconnected_evdev_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LinuxHotkeyService()
    service.__dict__['_fds'] = {43: set[int]()}
    service.__dict__['_fd_paths'] = {43: Path('/dev/input/event27')}
    closed: list[int] = []
    monkeypatch.setattr(
        os,
        'read',
        _raise_enodev,
    )
    monkeypatch.setattr(os, 'close', _record_close(closed))

    _drain(service, 43)

    assert _fds(service) == {}
    assert _fd_paths(service) == {}
    assert closed == [43]


def test_linux_hotkey_reader_does_not_spin_after_all_devices_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LinuxHotkeyService()
    service.__dict__['_fds'] = {43: set[int]()}
    service.__dict__['_fd_paths'] = {43: Path('/dev/input/event27')}
    select_calls: list[tuple[Sequence[int], Sequence[int], Sequence[int], float]] = []
    monkeypatch.setattr(
        'fleasion.gui.linux_hotkeys.select.select',
        _select_once(select_calls),
    )
    monkeypatch.setattr(
        os,
        'read',
        _raise_enodev,
    )
    monkeypatch.setattr(os, 'close', _noop_close)

    _run(service)

    assert len(select_calls) == 1


def test_linux_hotkey_controller_toggles_fastflag_folder() -> None:
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagOne': 'True', 'FFlagTwo': 'False'},
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={'Visual': ['FFlagOne', 'FFlagTwo']},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
    )
    proxy = SimpleNamespace(refresh_calls=0)

    def refresh() -> None:
        _refresh_proxy(proxy)

    proxy.refresh_custom_fflag_interception = refresh
    controller = LinuxCustomFFlagHotkeyController(config, proxy)
    toggled: list[str] = []
    controller.toggled.connect(_record_toggled(toggled))

    controller.service.activated.emit('folder:Visual')

    assert config.custom_fflag_disabled_folders == ['Visual']
    assert config.custom_fflag_disabled == []
    assert proxy.refresh_calls == 1
    assert toggled == ['folder:Visual']
    controller.stop()
