import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from fleasion.gui.windows_hotkeys import (
    MOD_CTRL,
    MOD_SHIFT,
    WindowsCustomFFlagHotkeyController,
    binding_text,
    modifier_mask_for_virtual_key,
    normalize_binding,
)


def test_scan_code_bindings_allow_bare_keys_modifier_keys_and_combinations():
    assert normalize_binding({'scan_code': 0x1E, 'extended': False, 'modifiers': 0}) == {
        'scan_code': 0x1E,
        'extended': False,
        'modifiers': 0,
    }
    assert normalize_binding({'scan_code': 0x1D, 'extended': False, 'modifiers': 0}) is not None
    assert normalize_binding({'scan_code': 0x3B, 'extended': False, 'modifiers': MOD_CTRL | MOD_SHIFT}) is not None
    assert normalize_binding({'scan_code': 0, 'extended': False, 'modifiers': 0}) is None
    assert normalize_binding({'scan_code': 0x1E, 'extended': False, 'modifiers': 0x10}) is None


def test_scan_code_binding_labels_and_modifier_categories_are_human_readable():
    assert binding_text({'scan_code': 0x1E, 'extended': False, 'modifiers': 0}) == 'A'
    assert binding_text({'scan_code': 0x1D, 'extended': False, 'modifiers': 0}) == 'Left Ctrl'
    assert binding_text({'scan_code': 0x3B, 'extended': False, 'modifiers': MOD_CTRL}) == 'Ctrl+F1'
    assert modifier_mask_for_virtual_key(0xA2) == MOD_CTRL


def test_custom_fflag_hotkey_controller_toggles_without_dashboard():
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
    controller = WindowsCustomFFlagHotkeyController(config, proxy)
    toggled = []
    controller.toggled.connect(toggled.append)

    controller.toggle_flag('FFlagExample')

    assert config.custom_fflag_disabled == ['FFlagExample']
    assert proxy.refresh_calls == 1
    assert toggled == ['FFlagExample']
    controller.stop()


def test_custom_fflag_hotkey_controller_toggles_folder_without_dashboard():
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
    controller = WindowsCustomFFlagHotkeyController(config, proxy)
    toggled = []
    controller.toggled.connect(toggled.append)

    controller.toggle_target('folder:Visual')

    assert config.custom_fflag_disabled_folders == ['Visual']
    assert config.custom_fflag_disabled == []
    assert proxy.refresh_calls == 1
    assert toggled == ['folder:Visual']
    controller.stop()
