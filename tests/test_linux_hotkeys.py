import errno
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.gui.linux_hotkeys import (
    LinuxCustomFFlagHotkeyController,
    LinuxHotkeyService,
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    binding_text,
    modifier_mask_for_evdev_code,
    normalize_binding,
)
from fleasion.gui.windows_hotkeys import binding_text as windows_binding_text


pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Linux-only hotkey translation tests'
)


def test_linux_keybinding_uses_tagged_evdev_codes():
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


def test_linux_keybinding_uses_the_complete_smu_evdev_translation_table():
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


def test_windows_and_linux_bindings_share_smu_key_names():
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


def test_linux_keybinding_rejects_windows_or_malformed_settings():
    assert normalize_binding({'scan_code': 30, 'extended': False, 'modifiers': 0}) is None
    assert normalize_binding({'platform': 'windows', 'scan_code': 30, 'modifiers': 0}) is None
    assert normalize_binding({'platform': 'linux_evdev', 'scan_code': 0, 'modifiers': 0}) is None


def test_linux_mouse_buttons_and_wheel_use_smu_codes():
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


def test_linux_modifier_codes_are_combined_generically():
    assert modifier_mask_for_evdev_code(29) == MOD_CTRL
    assert modifier_mask_for_evdev_code(100) == MOD_ALT
    assert modifier_mask_for_evdev_code(42) == MOD_SHIFT
    assert modifier_mask_for_evdev_code(30) == 0


def test_linux_hotkey_controller_toggles_without_the_dashboard():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagExample': 'True'},
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
    )
    proxy = SimpleNamespace(refresh_calls=0)
    proxy.refresh_custom_fflag_interception = lambda: setattr(
        proxy, 'refresh_calls', proxy.refresh_calls + 1
    )
    controller = LinuxCustomFFlagHotkeyController(config, proxy)
    toggled = []
    controller.toggled.connect(toggled.append)

    controller.service.activated.emit('FFlagExample')

    assert config.custom_fflag_disabled == ['FFlagExample']
    assert proxy.refresh_calls == 1
    assert toggled == ['FFlagExample']
    controller.stop()


def test_linux_hotkey_reader_drops_disconnected_evdev_device(monkeypatch):
    service = LinuxHotkeyService()
    service._fds = {43: set()}
    service._fd_paths = {43: Path('/dev/input/event27')}
    closed = []
    monkeypatch.setattr(
        os,
        'read',
        lambda *_args: (_ for _ in ()).throw(OSError(errno.ENODEV, 'No such device')),
    )
    monkeypatch.setattr(os, 'close', closed.append)

    service._drain_device(43)

    assert service._fds == {}
    assert service._fd_paths == {}
    assert closed == [43]


def test_linux_hotkey_reader_does_not_spin_after_all_devices_disconnect(monkeypatch):
    service = LinuxHotkeyService()
    service._fds = {43: set()}
    service._fd_paths = {43: Path('/dev/input/event27')}
    select_calls = []
    monkeypatch.setattr(
        'fleasion.gui.linux_hotkeys.select.select',
        lambda *args: select_calls.append(args) or ([43], [], []),
    )
    monkeypatch.setattr(
        os,
        'read',
        lambda *_args: (_ for _ in ()).throw(OSError(errno.ENODEV, 'No such device')),
    )
    monkeypatch.setattr(os, 'close', lambda _fd: None)

    service._run()

    assert len(select_calls) == 1


def test_linux_hotkey_controller_toggles_fastflag_folder():
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
    proxy.refresh_custom_fflag_interception = lambda: setattr(
        proxy, 'refresh_calls', proxy.refresh_calls + 1
    )
    controller = LinuxCustomFFlagHotkeyController(config, proxy)
    toggled = []
    controller.toggled.connect(toggled.append)

    controller.service.activated.emit('folder:Visual')

    assert config.custom_fflag_disabled_folders == ['Visual']
    assert config.custom_fflag_disabled == []
    assert proxy.refresh_calls == 1
    assert toggled == ['folder:Visual']
    controller.stop()


def test_linux_custom_fflag_actions_can_switch_same_flag_between_values():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'DFIntTaskSchedulerTargetFps': '60'},
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
        custom_fflag_folders={},
        custom_fflag_disabled_folders=[],
        custom_fflag_folder_keybinds={},
        custom_fflag_actions={
            '90 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '90'}},
            '144 FPS': {'flags': {'DFIntTaskSchedulerTargetFps': '144'}},
        },
    )
    proxy = SimpleNamespace(refresh_calls=0)
    proxy.refresh_custom_fflag_interception = lambda: setattr(
        proxy, 'refresh_calls', proxy.refresh_calls + 1
    )
    controller = LinuxCustomFFlagHotkeyController(config, proxy)
    toggled = []
    controller.toggled.connect(toggled.append)

    controller.toggle_target('action:90 FPS')
    assert config.custom_fflags['DFIntTaskSchedulerTargetFps'] == '90'
    controller.toggle_target('action:144 FPS')

    assert config.custom_fflags['DFIntTaskSchedulerTargetFps'] == '144'
    assert proxy.refresh_calls == 0
    assert toggled == ['action:90 FPS', 'action:144 FPS']
    controller.stop()
