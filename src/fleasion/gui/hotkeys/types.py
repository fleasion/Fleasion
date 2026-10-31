"""Types shared by hotkey widgets and platform controllers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleasion.gui.linux_hotkeys import LinuxCustomFFlagHotkeyController, LinuxHotkeyService
    from fleasion.gui.windows_hotkeys import (
        WindowsCustomFFlagHotkeyController,
        WindowsHotkeyService,
    )


type HotkeyService = WindowsHotkeyService | LinuxHotkeyService
type CustomFFlagHotkeyController = (
    WindowsCustomFFlagHotkeyController | LinuxCustomFFlagHotkeyController
)
