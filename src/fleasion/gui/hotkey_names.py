"""SMU virtual-key display names shared by Fleasion hotkey backends."""

from __future__ import annotations


# Ported from SMU's core/key_codes.h (KeyCodeName and FormatKeyCodeFallback).
SMU_VK_NAMES = {
    0x01: 'Mouse Left', 0x02: 'Mouse Right', 0x04: 'Mouse Middle',
    0x05: 'Mouse X1', 0x06: 'Mouse X2', 0x08: 'Backspace', 0x09: 'Tab',
    0x0D: 'Enter', 0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt', 0x13: 'Pause',
    0x14: 'Caps Lock', 0x1B: 'Escape', 0x20: 'Space', 0x21: 'Page Up',
    0x22: 'Page Down', 0x23: 'End', 0x24: 'Home', 0x25: 'Left', 0x26: 'Up',
    0x27: 'Right', 0x28: 'Down', 0x2C: 'Print Screen', 0x2D: 'Insert',
    0x2E: 'Delete', 0x5B: 'Left Super', 0x5C: 'Right Super', 0x5D: 'Menu',
    0x90: 'Num Lock', 0x91: 'Scroll Lock', 0xA0: 'Left Shift', 0xA1: 'Right Shift',
    0xA2: 'Left Ctrl', 0xA3: 'Right Ctrl', 0xA4: 'Left Alt', 0xA5: 'Right Alt',
    0xBA: ';', 0xBB: '=', 0xBC: ',', 0xBD: '-', 0xBE: '.', 0xBF: '/', 0xC0: '`',
    0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'",
}


def format_smu_virtual_key(virtual_key: int) -> str:
    """Return the same virtual-key label SMU presents in its hotkey UI."""
    if 0x41 <= virtual_key <= 0x5A or 0x30 <= virtual_key <= 0x39:
        return chr(virtual_key)
    if 0x70 <= virtual_key <= 0x87:
        return f'F{virtual_key - 0x70 + 1}'
    if 0x60 <= virtual_key <= 0x69:
        return f'Numpad {virtual_key - 0x60}'
    return SMU_VK_NAMES.get(virtual_key, f'0x{virtual_key:X}')
