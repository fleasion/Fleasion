"""Shared configuration and replacement rule contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, Protocol, TypedDict

if TYPE_CHECKING:
    from fleasion.utils.json_types import JsonValue


type ReplacementRules = list[JsonValue]

type ReplacementKey = int | str

type ReplacementMode = Literal['remove', 'id', 'cdn', 'local']

type ReplacementMaps = tuple[
    dict[ReplacementKey, int],
    set[ReplacementKey],
    dict[ReplacementKey, str],
    dict[ReplacementKey, str],
]

type FileSignature = tuple[int, int] | None

type ReplacementsSignature = tuple[tuple[str, FileSignature], ...]


class ProfileRule(TypedDict, total=False):
    type: Literal['profile']
    name: str
    enabled: bool
    replace_ids: list[int | str]
    mode: str
    remove: bool
    with_id: int
    cdn_url: str
    local_path: str


class GroupRule(TypedDict):
    type: Literal['group']
    name: str
    children: NotRequired[Any]
    expanded: NotRequired[bool]


type RuleEntry = ProfileRule | GroupRule

# Imported entries remain permissive so editing preserves unknown data
type RuleList = list[Any]


type HotkeyBinding = dict[str, int | bool | str]
type HotkeyBindings = dict[str, HotkeyBinding]


class FastFlagAction(TypedDict, total=False):
    flags: dict[str, str]
    keybind: HotkeyBinding


type FastFlagActions = dict[str, FastFlagAction]


class SettingsSource(Protocol):
    @property
    def settings(self) -> dict[str, JsonValue]: ...

    def save(self) -> None: ...
