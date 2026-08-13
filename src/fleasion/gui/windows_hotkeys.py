"""Compatibility imports for the nonvisual Windows hotkey service."""

from __future__ import annotations

from ..modifications.hotkeys.windows import (
    MOD_ALT,
    MOD_CTRL,
    MOD_SHIFT,
    MOD_WIN,
    MODIFIER_MASK,
    WindowsCustomFFlagHotkeyController,
    WindowsHotkeyService,
    binding_text,
    modifier_mask_for_virtual_key,
    normalize_binding,
)

__all__ = [
    'MOD_ALT',
    'MOD_CTRL',
    'MOD_SHIFT',
    'MOD_WIN',
    'MODIFIER_MASK',
    'WindowsCustomFFlagHotkeyController',
    'WindowsHotkeyService',
    'binding_text',
    'modifier_mask_for_virtual_key',
    'normalize_binding',
]
