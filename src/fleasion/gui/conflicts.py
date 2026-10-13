"""Mod-conflict checker: exact-key collisions and possible overlapping replacement scopes.

Mirrors the proxy's real precedence so the reported winner is what players
actually get: any replacement beats a removal, local files beat CDN URLs, CDN
URLs beat ID swaps, and within a mode the config enabled later overwrites the
earlier one (``ConfigManager.get_all_replacements`` uses dict updates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain, combinations
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fleasion.config.manager import (
    ANIMATION_TYPE_KEYS,
    ASSET_TYPE_KEYS,
    ASSET_TYPE_NAMES,
    TEXTUREPACK_TYPE_KEY,
    VIRTUAL_ANIMATION_KEYS,
    replacement_rule_claims,
)
from fleasion.localization import tr

if TYPE_CHECKING:
    from collections.abc import Iterator

type Mode = Literal['remove', 'id', 'cdn', 'local']
type Rule = dict[str, Any]
type Signature = tuple[tuple[str, tuple[int, int] | None], ...]

_RANK: Final[dict[Mode, int]] = {'remove': 0, 'id': 1, 'cdn': 2, 'local': 3}
_REFRESH_MS: Final = 1500


class RuleSource(Protocol):
    """The parts of ConfigManager the checker needs."""

    def iter_enabled_rules(self) -> Iterator[tuple[str, int, Rule]]: ...

    def enabled_configs_signature(self) -> Signature: ...


@dataclass(frozen=True)
class Claim:
    config: str
    rule: str
    mode: Mode
    order: int

    @property
    def precedence(self) -> tuple[int, int]:
        return _RANK[self.mode], self.order

    def label(self) -> str:
        return f'{self.config} \u203a {self.rule or "-"} ({self.mode})'


@dataclass
class Conflict:
    asset: str
    claims: list[Claim] = field(default_factory=list[Claim])
    potential: bool = False

    @property
    def winner(self) -> Claim:
        return max(self.claims, key=lambda claim: claim.precedence)

    @property
    def losers(self) -> list[Claim]:
        winner = self.winner
        return [claim for claim in self.claims if claim is not winner]


def rule_mode(rule: Rule) -> Mode:
    """Effective mode, treating an empty target as a removal like the proxy does."""
    mode = str(rule.get('mode', 'id'))
    if 'remove' in rule and 'mode' not in rule:
        mode = 'remove' if rule.get('remove') else 'id'
    empty_target = (
        (mode == 'cdn' and not rule.get('cdn_url'))
        or (mode == 'local' and not rule.get('local_path'))
        or (mode == 'id' and rule.get('with_id') is None)
    )
    if empty_target:
        return 'remove'
    known: dict[str, Mode] = {'remove': 'remove', 'id': 'id', 'cdn': 'cdn', 'local': 'local'}
    return known.get(mode, 'id')


@dataclass(frozen=True)
class _RequestScope:
    """Constraints on a possible proxy request, independent of replacement mode."""

    asset_id: int | None = None
    asset_types: frozenset[int] | None = None
    texpack_slot: int | None = None

    def overlaps(self, other: _RequestScope) -> bool:
        """Whether one request can satisfy both matching-key constraints."""
        return (
            (self.asset_id is None or other.asset_id is None or self.asset_id == other.asset_id)
            and (
                self.asset_types is None
                or other.asset_types is None
                or not self.asset_types.isdisjoint(other.asset_types)
            )
            and (
                self.texpack_slot is None
                or other.texpack_slot is None
                or self.texpack_slot == other.texpack_slot
            )
        )


def _request_scope(key: str) -> _RequestScope | None:
    """Project the proxy's request matching keys to their shared request constraints.

    Mirrors ``TextureStripper``'s slot/asset/wildcard/type key groups and
    animation-only virtual keys. A missing constraint means any value is possible,
    not that the request has no type or TexturePack slot.
    """
    if key in VIRTUAL_ANIMATION_KEYS:
        return _RequestScope(asset_types=ANIMATION_TYPE_KEYS)
    if key.isdigit():
        value = int(key)
        if value in ASSET_TYPE_KEYS:
            return _RequestScope(asset_types=frozenset({value}))
        return _RequestScope(asset_id=value)
    prefix, sep, suffix = key.partition(':')
    if sep and suffix.isdigit():
        texturepack_types = frozenset({TEXTUREPACK_TYPE_KEY})
        if prefix == 'TexturePack':
            return _RequestScope(asset_types=texturepack_types, texpack_slot=int(suffix))
        if prefix.isdigit():
            return _RequestScope(
                asset_id=int(prefix), asset_types=texturepack_types, texpack_slot=int(suffix)
            )
    return None


def find_conflicts(source: RuleSource) -> list[Conflict]:
    claims: dict[str, list[Claim]] = {}
    for config, order, rule in source.iter_enabled_rules():
        for key, mode in replacement_rule_claims(rule):
            asset = str(key)
            # The proxy overwrites within each mode, retaining other mode maps
            bucket = [
                claim
                for claim in claims.get(asset, [])
                if claim.config != config or claim.mode != mode
            ]
            bucket.append(Claim(config, str(rule.get('name', '')), mode, order))
            claims[asset] = bucket
    conflicts = [
        Conflict(asset, found)
        for asset, found in claims.items()
        if len({claim.config for claim in found}) > 1
    ]
    # Unlike exact-key collisions, different matching groups can only be
    # identified as potential overlaps without the asset's request metadata.
    # Compare their shared request constraints instead of enumerating special
    # cases (ID/type, slot/wildcard, animation fallback, etc.).
    scopes = {key: scope for key in claims if (scope := _request_scope(key)) is not None}
    broad_keys: list[str] = []
    specific_by_id: dict[int, list[str]] = {}
    for key, scope in scopes.items():
        if scope.asset_id is None:
            broad_keys.append(key)
        else:
            specific_by_id.setdefault(scope.asset_id, []).append(key)

    # Unrelated concrete asset IDs can never overlap. Only compare keys with
    # unconstrained IDs, or keys that target the same concrete asset ID.
    concrete_keys = [key for group in specific_by_id.values() for key in group]
    candidates = chain(
        combinations(broad_keys, 2),
        ((left, right) for left in broad_keys for right in concrete_keys),
        (pair for group in specific_by_id.values() for pair in combinations(group, 2)),
    )
    for left, right in candidates:
        if not scopes[left].overlaps(scopes[right]):
            continue
        found = [*claims[left], *claims[right]]
        if len({claim.config for claim in found}) <= 1:
            continue
        left_label = (
            ASSET_TYPE_NAMES.get(int(left), left).title()
            if left.isdigit() and int(left) in ASSET_TYPE_KEYS
            else left
        )
        right_label = (
            ASSET_TYPE_NAMES.get(int(right), right).title()
            if right.isdigit() and int(right) in ASSET_TYPE_KEYS
            else right
        )
        conflicts.append(Conflict(f'{left_label} / {right_label}', found, potential=True))

    conflicts.sort(key=lambda conflict: (conflict.winner.config, conflict.asset))
    return conflicts


class ConflictsDialog(QDialog):
    def __init__(self, parent: QWidget | None, conflicts: list[Conflict]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.conflicts.title'))
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        summary = QLabel(tr('ui.gui.conflicts.summary', value0=len(conflicts)))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels(
            [
                tr('ui.gui.conflicts.col_asset'),
                tr('ui.gui.conflicts.col_winner'),
                tr('ui.gui.conflicts.col_losers'),
            ]
        )
        for conflict in conflicts:
            item = QTreeWidgetItem(
                [
                    conflict.asset,
                    tr('ui.gui.conflicts.potential')
                    if conflict.potential
                    else conflict.winner.label(),
                    '\n'.join(
                        claim.label()
                        for claim in (conflict.claims if conflict.potential else conflict.losers)
                    ),
                ]
            )
            font = item.font(1)
            font.setBold(not conflict.potential)
            font.setItalic(conflict.potential)
            item.setFont(1, font)
            tree.addTopLevelItem(item)
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(tree, 1)

        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton(tr('ui.gui.conflicts.close'))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


class ConflictBadge(QPushButton):
    """Footer pill that live-updates as configs are toggled or edited."""

    def __init__(self, source: RuleSource, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('ConflictBadge')
        self._source = source
        self._signature: Signature | None = None
        self.conflicts: list[Conflict] = []
        self.clicked.connect(self.show_dialog)
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self, *, force: bool = False) -> None:
        try:
            signature = self._source.enabled_configs_signature()
        except OSError:
            return
        if signature == self._signature and not force:
            return
        self._signature = signature
        self.conflicts = find_conflicts(self._source)
        exact = sum(not conflict.potential for conflict in self.conflicts)
        potential = len(self.conflicts) - exact
        if exact and potential:
            label = tr('ui.gui.conflicts.badge_mixed', value0=exact, value1=potential)
        elif potential:
            label = tr('ui.gui.conflicts.badge_potential', value0=potential)
        elif exact:
            label = tr('ui.gui.conflicts.badge_count', value0=exact)
        else:
            label = tr('ui.gui.conflicts.badge_none')
        self.setText(label)
        self.setProperty('clean', not (exact or potential))
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def show_dialog(self) -> None:
        self.refresh(force=True)
        ConflictsDialog(self.window(), self.conflicts).exec()
