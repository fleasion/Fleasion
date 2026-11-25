from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HotkeyConfig:
    custom_fflags_enabled: bool = False
    custom_fflags: dict[str, str] = field(default_factory=dict[str, str])
    custom_fflag_disabled: list[str] = field(default_factory=list[str])
    custom_fflag_disabled_folders: list[str] = field(default_factory=list[str])
    custom_fflag_keybinds: dict[str, dict[str, object]] = field(default_factory=dict[str, dict[str, object]])
    custom_fflag_folder_keybinds: dict[str, dict[str, object]] = field(default_factory=dict[str, dict[str, object]])
    custom_fflag_folders: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    custom_fflag_actions: dict[str, object] = field(default_factory=dict[str, object])


@dataclass
class HotkeyProxy:
    refresh_calls: int = 0

    def refresh_custom_fflag_interception(self) -> None:
        self.refresh_calls += 1
