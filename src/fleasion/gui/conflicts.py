"""Mod-conflict checker: asset IDs claimed by more than one enabled config.

Mirrors the proxy's real precedence so the reported winner is what players
actually get: any replacement beats a removal, local files beat CDN URLs, CDN
URLs beat ID swaps, and within a mode the config enabled later overwrites the
earlier one (``ConfigManager.get_all_replacements`` uses dict updates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
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

from ..localization import tr

if TYPE_CHECKING:
    from collections.abc import Iterator

type Mode = Literal['remove', 'id', 'cdn', 'local']
type Rule = dict[str, Any]
type Signature = tuple[tuple[str, tuple[int, int] | None], ...]

_RANK: Final[dict[Mode, int]] = {'remove': 0, 'id': 1, 'cdn': 2, 'local': 3}
_REFRESH_MS: Final = 1500
_WINNER_COLOR: Final = QColor('#7ef0bf')
_LOSER_COLOR: Final = QColor(245, 247, 251, 120)


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


def find_conflicts(source: RuleSource) -> list[Conflict]:
    claims: dict[str, list[Claim]] = {}
    for config, order, rule in source.iter_enabled_rules():
        mode = rule_mode(rule)
        raw_ids: list[object] = list(rule.get('replace_ids') or [])
        for raw in raw_ids:
            asset = str(raw).strip()
            if not asset:
                continue
            # Within one config only its last rule for an ID matters
            bucket = [claim for claim in claims.get(asset, []) if claim.config != config]
            bucket.append(Claim(config, str(rule.get('name', '')), mode, order))
            claims[asset] = bucket
    conflicts = [
        Conflict(asset, found)
        for asset, found in claims.items()
        if len({claim.config for claim in found}) > 1
    ]
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
                    conflict.winner.label(),
                    '\n'.join(claim.label() for claim in conflict.losers),
                ]
            )
            item.setForeground(1, QBrush(_WINNER_COLOR))
            item.setForeground(2, QBrush(_LOSER_COLOR))
            font = item.font(1)
            font.setBold(True)
            item.setFont(1, font)
            tree.addTopLevelItem(item)
        header = tree.header()
        if header is not None:
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
        count = len(self.conflicts)
        self.setText(
            tr('ui.gui.conflicts.badge_count', value0=count)
            if count
            else tr('ui.gui.conflicts.badge_none')
        )
        self.setProperty('clean', count == 0)
        if (style := self.style()) is not None:
            style.unpolish(self)
            style.polish(self)

    def show_dialog(self) -> None:
        self.refresh(force=True)
        ConflictsDialog(self.window(), self.conflicts).exec()
