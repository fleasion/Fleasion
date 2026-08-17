"""Compatibility imports for the nonvisual Linux hotkey service."""

from __future__ import annotations

from ..modifications.hotkeys.linux import (
    LinuxCustomFFlagHotkeyController,
    LinuxHotkeyService,
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    MOD_WIN,
    binding_text,
    launch_permission_setup,
    modifier_mask_for_evdev_code,
    normalize_binding,
)

__all__ = [
    'LinuxCustomFFlagHotkeyController',
    'LinuxHotkeyService',
    'MOD_ALT',
    'MOD_CTRL',
    'MOD_SHIFT',
    'MOD_WIN',
    'binding_text',
    'launch_permission_setup',
    'modifier_mask_for_evdev_code',
    'normalize_binding',
]
