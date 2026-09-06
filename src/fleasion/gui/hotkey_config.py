"""Shared configuration access for custom FastFlag hotkeys."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast


class HotkeyConfig(Protocol):
    @property
    def custom_fflags_enabled(self) -> bool: ...

    @property
    def custom_fflags(self) -> dict[str, str]: ...

    @custom_fflags.setter
    def custom_fflags(self, value: dict[str, str]) -> None: ...

    @property
    def custom_fflag_disabled(self) -> list[str]: ...

    @custom_fflag_disabled.setter
    def custom_fflag_disabled(self, value: list[str]) -> None: ...

    @property
    def custom_fflag_disabled_folders(self) -> list[str]: ...

    @custom_fflag_disabled_folders.setter
    def custom_fflag_disabled_folders(self, value: list[str]) -> None: ...

    @property
    def custom_fflag_keybinds(self) -> Mapping[str, Mapping[str, object]]: ...

    @property
    def custom_fflag_folders(self) -> Mapping[str, list[str]]: ...

    @property
    def custom_fflag_folder_keybinds(self) -> Mapping[str, Mapping[str, object]]: ...

    @property
    def custom_fflag_actions(self) -> Mapping[str, object]: ...


class HotkeyProxy(Protocol):
    def refresh_custom_fflag_interception(self) -> None: ...


def binding_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast('Mapping[object, object]', value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast('Mapping[str, object]', mapping)
