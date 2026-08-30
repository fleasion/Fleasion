"""PreJsons browser dialog - shows game configs as interactive cards with thumbnails."""

from __future__ import annotations

import contextlib
import io
import json
import threading
import uuid
from collections.abc import Callable  # ruff: ignore[typing-only-standard-library-import]
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QEnterEvent,
    QFont,
    QIcon,
    QImage,
    QMouseEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr
from fleasion.utils import CLOG_URL, ORIGINALS_DIR, PREJSONS_DIR, REPLACEMENTS_DIR, get_icon_path
from fleasion.utils.http import http_get

from .file_drop import FileDropLineEdit

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, JsonValue] | list[JsonValue]
type ImportValue = int | str


class GameEntry(TypedDict):
    name: str
    created: str
    updated: str
    credit: str
    placeId: int | None
    github: str
    replacement: str


if TYPE_CHECKING:

    def _is_dict(value: object) -> bool: ...

    def _is_list(value: object) -> bool: ...

    def _preserve_object_dict(value: object) -> dict[str, object]: ...

    def _preserve_object_list(value: object) -> list[object]: ...

    def _preserve_int_source(value: object) -> str | int | float: ...

    def _preserve_str(value: object) -> str: ...

    def _preserve_json(value: object) -> JsonValue: ...

    def _qimage_bits_setsize(value: object, size: int) -> None: ...

    def _scroll_area(dialog: PreJsonsDialog) -> QScrollArea: ...

    def _card_game_name(card: GameCard) -> str: ...

    def _card_dump_file(card: GameCard) -> Path | None: ...

    def _parent_config_manager(parent: object) -> ConfigManager | None: ...

    def _append_replace_ids(parent: object, ids: list[ImportValue]) -> None: ...

    def _set_replacement_value(parent: object, value: ImportValue) -> None: ...

    def _preserve_dialog(value: QObject | None) -> QDialog | None: ...

    def _entry_url(entry: GameEntry, key: Literal['github', 'replacement']) -> str: ...

    def _set_entry_url(
        entry: GameEntry, key: Literal['github', 'replacement'], value: str
    ) -> None: ...
else:

    def _is_dict(value: object) -> bool:
        return isinstance(value, dict)

    def _is_list(value: object) -> bool:
        return isinstance(value, list)

    def _preserve_object_dict(value: object) -> dict[str, object]:
        return value

    def _preserve_object_list(value: object) -> list[object]:
        return value

    def _preserve_int_source(value: object) -> str | int | float:
        return value

    def _preserve_str(value: object) -> str:
        return value

    def _preserve_json(value: object) -> JsonValue:
        return value

    def _qimage_bits_setsize(value: object, size: int) -> None:
        value.setsize(size)

    def _scroll_area(dialog: PreJsonsDialog) -> QScrollArea:
        return dialog.scroll

    def _card_game_name(card: GameCard) -> str:
        return card._game_name  # ruff: ignore[private-member-access]

    def _card_dump_file(card: GameCard) -> Path | None:
        return card._dump_file  # ruff: ignore[private-member-access]

    def _parent_config_manager(parent: object) -> ConfigManager | None:
        return getattr(parent, 'config_manager', None)

    def _append_replace_ids(parent: object, ids: list[ImportValue]) -> None:
        if hasattr(parent, 'replace_entry'):
            cur = parent.replace_entry.text()
            parent.replace_entry.setText(
                (cur + ', ' if cur.strip() else '') + ', '.join(str(x) for x in ids)
            )

    def _set_replacement_value(parent: object, value: ImportValue) -> None:
        if hasattr(parent, 'replacement_entry'):
            parent.replacement_entry.setText(str(value))

    def _preserve_dialog(value: QObject | None) -> QDialog | None:
        return value

    def _entry_url(entry: GameEntry, key: Literal['github', 'replacement']) -> str:
        return entry.get(key, '')

    def _set_entry_url(entry: GameEntry, key: Literal['github', 'replacement'], value: str) -> None:
        entry[key] = value


if TYPE_CHECKING:
    _LANCZOS = Image.Resampling.LANCZOS
else:
    _LANCZOS = Image.LANCZOS

CUSTOM_DUMPS_DIR = PREJSONS_DIR / 'custom_dumps'
CLOG_CACHE_FILE = PREJSONS_DIR / 'CLOG.json'

_DEFAULT_THUMB_URL = (
    'https://static.wikia.nocookie.net/roblox/images/5/54/Default_Thumbnail_1_updated.png'
    '/revision/latest/scale-to-width-down/1000?cb=20250523160858'
)
_default_thumb_bytes_cache: list[bytes] = []  # single-element list so it's mutable

# Module-level caches (persist across dialog instances)

# place_id -> (name, created, updated)
_meta_cache: dict[int, tuple[str, str, str]] = {}
# place_id -> raw PNG bytes (QPixmap reconstructed in main thread from these)
_thumb_bytes_cache: dict[int, bytes] = {}


# HTTP helper


def _http_get(url: str, timeout: int = 12) -> bytes:
    return http_get(url, timeout=timeout)


def _fetch_or_read(url_or_path: str, timeout: int = 15) -> bytes:
    """Fetch a URL or read a local file, returning raw bytes."""
    if url_or_path.startswith(('http://', 'https://')):
        return _http_get(url_or_path, timeout=timeout)
    return Path(url_or_path).read_bytes()


def _safe_filename(name: str) -> str:
    """Strip characters that are invalid in Windows filenames."""
    import re  # ruff: ignore[import-outside-top-level]

    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:128] or 'dump'


# PIL-based rounded thumbnail helper


def _make_rounded_pixmap(pix: QPixmap, w: int, h: int, radius: int = 6) -> QPixmap:
    """Scale-crop pixmap to (w × h) with rounded corners via PIL."""  # ruff: ignore[ambiguous-unicode-character-docstring]
    qimg = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = qimg.bits()
    _qimage_bits_setsize(ptr, qimg.width() * qimg.height() * 4)
    img = Image.frombytes('RGBA', (qimg.width(), qimg.height()), bytes(ptr))

    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        return pix

    ratio = w / h
    src_ratio = src_w / src_h
    if src_ratio > ratio:
        new_w = int(src_h * ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / ratio)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))

    scale = 2
    img = img.resize((w * scale, h * scale), _LANCZOS)
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.width, img.height), radius=radius * scale, fill=255)
    img.putalpha(mask)
    img = img.resize((w, h), _LANCZOS)

    out = QImage(img.tobytes('raw', 'RGBA'), img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(out)


def _preprocess_thumb_bytes(
    raw: bytes, w: int, h: int, radius: int = 6
) -> tuple[bytes, int, int] | None:
    """Crop, resize, and round-corner raw image bytes using PIL only.

    Safe to call from a background thread — no Qt objects involved.
    Returns (rgba_bytes, w, h) ready to hand to QImage on the main thread.
    """
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        img = Image.open(io.BytesIO(raw)).convert('RGBA')
        src_w, src_h = img.size
        if src_w == 0 or src_h == 0:
            return None
        ratio = w / h
        src_ratio = src_w / src_h
        if src_ratio > ratio:
            new_w = int(src_h * ratio)
            left = (src_w - new_w) // 2
            img = img.crop((left, 0, left + new_w, src_h))
        else:
            new_h = int(src_w / ratio)
            top = (src_h - new_h) // 2
            img = img.crop((0, top, src_w, top + new_h))
        scale = 2
        img = img.resize((w * scale, h * scale), _LANCZOS)
        mask = Image.new('L', img.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, img.width, img.height), radius=radius * scale, fill=255)
        img.putalpha(mask)
        img = img.resize((w, h), _LANCZOS)
        return img.tobytes('raw', 'RGBA'), w, h
    except Exception:  # ruff: ignore[blind-except]
        return None


if TYPE_CHECKING:
    _ = _preprocess_thumb_bytes


# Normalize game entry


def _normalize_entry(e: object) -> GameEntry | None:
    """Normalize a single game entry dict. Returns None if unusable."""
    entry_source: object = e
    if not _is_dict(entry_source):
        return None
    entry = _preserve_object_dict(entry_source)
    name = entry.get('name') or entry.get('game') or ''
    pid = entry.get('placeId') or entry.get('place_id') or entry.get('id')
    try:
        pid = int(_preserve_int_source(pid)) if pid is not None else None
    except Exception:  # ruff: ignore[blind-except]
        pid = None
    if not name and pid:
        name = f'Place {pid}'
    if not name:
        return None
    credit = (
        entry.get('credit')
        or entry.get('Credit')
        or entry.get('Owner')
        or entry.get('owner')
        or entry.get('author')
        or entry.get('Author')
        or ''
    )
    return {
        'name': str(name),
        'created': str(entry.get('created') or ''),
        'updated': str(entry.get('updated') or ''),
        'credit': str(credit),
        'placeId': pid,
        'github': _preserve_str(entry.get('github') or ''),
        'replacement': _preserve_str(entry.get('replacement') or entry.get('Replacement') or ''),
    }


def _normalize_games(data: object) -> list[GameEntry]:
    """Convert CLOG.json into a flat list of normalized game dicts."""
    data_source: object = data
    if not _is_dict(data_source):
        return []
    root = _preserve_object_dict(data_source)
    raw_source: object = root.get('games', {})
    entries: list[dict[str, object]] = []
    if _is_dict(raw_source):
        raw_dict = _preserve_object_dict(raw_source)
        for name, cfg in raw_dict.items():
            cfg_source: object = cfg
            if _is_dict(cfg_source):
                entry = _preserve_object_dict(cfg_source).copy()
                entry.setdefault('name', name)
                entries.append(entry)
            else:
                entries.append({'name': str(name)})
    elif _is_list(raw_source):
        raw_list = _preserve_object_list(raw_source)
        for raw_entry in raw_list:
            entry_source: object = raw_entry
            if _is_dict(entry_source):
                entries.append(_preserve_object_dict(entry_source))

    return [g for entry in entries if (g := _normalize_entry(entry)) is not None]


def _load_custom_dumps() -> list[tuple[GameEntry, Path]]:
    """Load all valid custom dump JSON files. Returns (game_dict, file_path) tuples."""
    results: list[tuple[GameEntry, Path]] = []
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        CUSTOM_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
        for fp in sorted(CUSTOM_DUMPS_DIR.glob('*.json')):
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                data: object = json.loads(fp.read_text(encoding='utf-8', errors='ignore'))
                # Support both single-entry {"name":...} and {"games":{...}} wrappers
                data_source: object = data
                if _is_dict(data_source):
                    data_dict = _preserve_object_dict(data_source)
                    if 'games' not in data_dict and (
                        isinstance(data_dict.get('name'), str)
                        or data_dict.get('placeId') is not None
                    ):
                        data = {'games': {'_': data_dict}}
                games = _normalize_games(data)
                for g in games:
                    results.append((g, fp))  # ruff: ignore[manual-list-comprehension]
            except Exception as e:  # ruff: ignore[blind-except]
                print(f'[CustomDump] Failed to load {fp.name}: {e}')
    except Exception as e:  # ruff: ignore[blind-except]
        print(f'[CustomDump] Failed to scan dir: {e}')
    return results


# Worker threads


class _ClogWorker(QThread):
    """Fetches CLOG.json and builds the normalised game list."""

    done = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            raw = _http_get(CLOG_URL, timeout=15)
            CLOG_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CLOG_CACHE_FILE.write_bytes(raw)
        except Exception as fetch_error:  # ruff: ignore[blind-except]
            try:
                raw = CLOG_CACHE_FILE.read_bytes()
            except Exception:  # ruff: ignore[blind-except]
                self.failed.emit(str(fetch_error))
                return

        try:
            data: object = json.loads(raw.decode('utf-8'))
            games = _normalize_games(data)
            self.done.emit(games)
        except Exception as e:  # ruff: ignore[blind-except]
            self.failed.emit(str(e))


class _CardMetaWorker(QThread):
    """Fetches real game name + dates for one card via Roblox API."""

    name_ready = Signal(str, str, str)  # name, created, updated

    def __init__(self, place_id: int, fallback_cr: str, fallback_up: str) -> None:
        super().__init__()
        self._pid = place_id
        self._cr = fallback_cr
        self._up = fallback_up

    def run(self) -> None:
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            r1_payload: object = json.loads(
                _http_get(
                    f'https://apis.roblox.com/universes/v1/places/{self._pid}/universe',
                    timeout=10,
                )
            )
            r1 = _preserve_object_dict(r1_payload)
            universe_id = r1.get('universeId')
            if not universe_id:
                return
            r2_payload: object = json.loads(
                _http_get(
                    f'https://games.roblox.com/v1/games?universeIds={universe_id}',
                    timeout=10,
                )
            )
            r2 = _preserve_object_dict(r2_payload)
            entries = _preserve_object_list(r2.get('data', []))
            if not entries:
                return
            entry = _preserve_object_dict(entries[0])
            name = _preserve_str(entry.get('name') or '')
            created = _preserve_str(entry.get('created') or self._cr)
            updated = _preserve_str(entry.get('updated') or self._up)
            if name:
                _meta_cache[self._pid] = (name, created, updated)
                self.name_ready.emit(name, created, updated)
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass


def _get_default_thumb_bytes() -> bytes | None:
    """Return cached bytes for the default thumbnail, fetching once on first call."""
    if _default_thumb_bytes_cache:
        return _default_thumb_bytes_cache[0]
    try:
        data = _http_get(_DEFAULT_THUMB_URL, timeout=10)
        _default_thumb_bytes_cache.append(data)
        return data  # ruff: ignore[try-consider-else]
    except Exception:  # ruff: ignore[blind-except]
        return None


# Pre-fetch the default thumbnail in the background as soon as the module loads
threading.Thread(target=_get_default_thumb_bytes, daemon=True).start()


class _CardThumbWorker(QThread):
    """Fetches the thumbnail for one card via Roblox thumbnails API."""

    thumb_ready = Signal(QPixmap)

    def __init__(self, place_id: int) -> None:
        super().__init__()
        self._pid = place_id

    def run(self) -> None:
        img_bytes = None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            meta_payload: object = json.loads(
                _http_get(
                    f'https://thumbnails.roblox.com/v1/places/gameicons'
                    f'?placeIds={self._pid}&size=512x512&format=Png',
                    timeout=10,
                )
            )
            meta = _preserve_object_dict(meta_payload)
            thumb_data = meta.get('data')
            thumb_entries: list[object] = _preserve_object_list(thumb_data) if thumb_data else [{}]
            first_thumb_source: object = thumb_entries[0]
            first_thumb = _preserve_object_dict(first_thumb_source)
            img_url = _preserve_str(first_thumb.get('imageUrl') or '')
            if img_url:
                img_bytes = _http_get(img_url, timeout=10)
                _thumb_bytes_cache[self._pid] = img_bytes
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass

        if not img_bytes:
            img_bytes = _get_default_thumb_bytes()

        if img_bytes:
            pix = QPixmap()
            if pix.loadFromData(img_bytes):
                self.thumb_ready.emit(pix)


class _JsonFetchWorker(QThread):
    """Downloads a JSON file from a URL."""

    done = Signal(object, str)
    failed = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url

    def run(self) -> None:
        try:
            raw = _http_get(self._url, timeout=15)
            data = _preserve_json(json.loads(raw.decode('utf-8')))
            filename = self._url.rsplit('/', 1)[-1] or 'data.json'
            self.done.emit(data, filename)
        except Exception as e:  # ruff: ignore[blind-except]
            self.failed.emit(str(e))


# Card constants

_CARD_W = 210
_CARD_H = 272
_THUMB_W = 196
_THUMB_H = 128


# Game Card Widget


class GameCard(QFrame):
    """A single game card: thumbnail + name + dates + action buttons."""

    def _apply_style(self, hover: bool = False) -> None:
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        border = 'rgba(255,255,255,0.22)' if dark else 'rgba(0,0,0,0.18)'
        bg = (
            ('rgba(255,255,255,0.07)' if hover else 'rgba(255,255,255,0.04)')
            if dark
            else ('rgba(0,0,0,0.06)' if hover else 'transparent')
        )
        self.setStyleSheet(f'GameCard {{ border: 1px solid {border}; background: {bg}; }}')

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_style()
        self._game_name = ''
        self._dump_file: Path | None = None
        self._on_delete: Callable[[GameCard], object] | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedHeight(_THUMB_H)
        self.thumb_label.setMinimumWidth(_THUMB_W)
        self.thumb_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setScaledContents(True)
        self.thumb_label.setStyleSheet(
            'background: palette(alternate-base); border-radius: 4px; color: palette(placeholder-text); font-size: 8pt;'
        )
        layout.addWidget(self.thumb_label)
        # Apply the default thumbnail immediately if already cached
        default_bytes = _default_thumb_bytes_cache[0] if _default_thumb_bytes_cache else None
        if default_bytes:
            pix = QPixmap()
            if pix.loadFromData(default_bytes):
                try:
                    pix = _make_rounded_pixmap(pix, _THUMB_W, _THUMB_H, radius=6)
                except Exception:  # ruff: ignore[blind-except, try-except-pass]
                    pass
                self.thumb_label.setPixmap(pix)
                self.thumb_label.setStyleSheet('background: transparent;')

        self.name_label = QLabel(tr('ui.gui.prejsons_dialog.unknown'))
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(38)
        f = QFont()
        f.setBold(True)
        self.name_label.setFont(f)
        layout.addWidget(self.name_label)

        self.created_label = QLabel('')
        self.created_label.setStyleSheet('color: palette(placeholder-text); font-size: 7pt;')
        layout.addWidget(self.created_label)

        self.updated_label = QLabel('')
        self.updated_label.setStyleSheet('color: palette(placeholder-text); font-size: 7pt;')
        layout.addWidget(self.updated_label)

        self.credit_label = QLabel('')
        self.credit_label.setStyleSheet('color: palette(placeholder-text); font-size: 7pt;')
        layout.addWidget(self.credit_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.assets_btn = QPushButton(tr('ui.gui.prejsons_dialog.assets'))
        self.assets_btn.setVisible(False)
        self.assets_btn.setFixedHeight(22)
        btn_row.addWidget(self.assets_btn)

        self.replacements_btn = QPushButton(tr('ui.gui.prejsons_dialog.replacements'))
        self.replacements_btn.setVisible(False)
        self.replacements_btn.setFixedHeight(22)
        btn_row.addWidget(self.replacements_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def set_data(self, name: str, created: str = '', updated: str = '', credit: str = '') -> None:
        self._game_name = name
        self.name_label.setText(name)
        if created:
            self.created_label.setText(
                tr('ui.gui.prejsons_dialog.created_value', value0=created[:10])
            )
        if updated:
            self.updated_label.setText(
                tr('ui.gui.prejsons_dialog.updated_value', value0=updated[:10])
            )
        if credit:
            self.credit_label.setText(tr('ui.gui.prejsons_dialog.credit_value', value0=credit))

    def set_thumbnail(self, pix: QPixmap) -> None:
        if not pix or pix.isNull():
            return
        try:
            baked = _make_rounded_pixmap(pix, _THUMB_W, _THUMB_H, radius=6)
        except Exception:  # ruff: ignore[blind-except]
            baked = pix
        self.thumb_label.setPixmap(baked)
        self.thumb_label.setText('')
        self.thumb_label.setStyleSheet('background: transparent;')

    def enable_delete_menu(self, dump_file: Path, on_delete: Callable[[GameCard], object]) -> None:
        """Wire up right-click → Delete for custom dump cards."""
        self._dump_file = dump_file
        self._on_delete = on_delete
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction(tr('ui.gui.prejsons_dialog.delete'))
        action = menu.exec(self.mapToGlobal(pos))
        if action == delete_action and self._on_delete:
            self._on_delete(self)

    def enterEvent(self, event: QEnterEvent) -> None:  # ruff: ignore[invalid-function-name]
        self._apply_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # ruff: ignore[invalid-function-name]
        self._apply_style()
        super().leaveEvent(event)


# Add Card Widget  (the "+" button at the end of the grid)


class AddCard(QFrame):
    """Clickable '+' card that opens the import dialog."""

    clicked = Signal()

    def _apply_style(self, hover: bool = False) -> None:
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        border = 'rgba(255,255,255,0.22)' if dark else 'rgba(0,0,0,0.18)'
        bg = (
            ('rgba(255,255,255,0.07)' if hover else 'rgba(255,255,255,0.04)')
            if dark
            else ('rgba(0,0,0,0.06)' if hover else 'transparent')
        )
        self.setStyleSheet(f'AddCard {{ border: 1px solid {border}; background: {bg}; }}')

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_style()
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        plus = QLabel(tr('ui.gui.prejsons_dialog.text'))
        plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plus.setStyleSheet('font-size: 36pt; color: palette(placeholder-text);')
        layout.addWidget(plus)

        sub = QLabel(tr('ui.gui.prejsons_dialog.add_custom_dump'))
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet('color: palette(placeholder-text); font-size: 9pt;')
        layout.addWidget(sub)

        self.setLayout(layout)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # ruff: ignore[invalid-function-name]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # ruff: ignore[invalid-function-name]
        self._apply_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # ruff: ignore[invalid-function-name]
        self._apply_style()
        super().leaveEvent(event)


# PreJsons Dialog


class PreJsonsDialog(QDialog):
    """Browse available PreJsons as interactive game cards with live thumbnails."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.prejsons_dialog.scraped_games'))
        self.resize(760, 580)
        self.setMinimumSize(640, 480)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._cards: list[GameCard] = []
        self._workers: list[QThread] = []
        self._viewers: list[QDialog] = []
        self._load_generation = 0
        self._thumbs_pending = 0
        self._thumbs_finished = 0

        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_cols = 0

        self._setup_ui()
        self._set_icon()
        self._start_load()

    def _set_icon(self) -> None:
        if path := get_icon_path():
            self.setWindowIcon(QIcon(str(path)))

    def _setup_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(tr('ui.gui.prejsons_dialog.search')))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr('ui.gui.prejsons_dialog.filter_by_game_name'))
        self.search_edit.textChanged.connect(lambda: self._search_timer.start(80))
        bar.addWidget(self.search_edit)
        self.refresh_btn = QPushButton(tr('ui.gui.prejsons_dialog.refresh'))
        self.refresh_btn.clicked.connect(self._do_refresh)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        self.status_label = QLabel(tr('ui.gui.prejsons_dialog.loading'))
        self.status_label.setStyleSheet(
            'color: palette(placeholder-text); font-size: 8pt; padding-left: 2px;'
        )
        root.addWidget(self.status_label)

        scroll_area = QScrollArea()
        setattr(self, 'scroll', scroll_area)  # ruff: ignore[set-attr-with-constant]
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.container = QWidget()
        self.container.setAutoFillBackground(True)
        self.container.setBackgroundRole(QPalette.ColorRole.Base)
        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.container.setLayout(self.grid)
        scroll_area.setWidget(self.container)
        root.addWidget(scroll_area)

        # Create the permanent add card
        self._add_card = AddCard(self.container)
        self._add_card.clicked.connect(self._open_add_dump_dialog)

        self.setLayout(root)

    # Load

    def _start_load(self) -> None:
        self._load_generation += 1
        self._thumbs_pending = 0
        self._thumbs_finished = 0
        self.refresh_btn.setEnabled(False)
        self.status_label.setText(tr('ui.gui.prejsons_dialog.fetching_game_list'))
        worker = _ClogWorker()
        worker.done.connect(self._on_clog_done)
        worker.failed.connect(self._on_clog_failed)
        self._workers.append(worker)
        worker.start()

    def _on_clog_done(self, games: list[GameEntry]) -> None:
        count = len(games)
        self.status_label.setText(
            tr(
                'prejsons.games_fetching_thumbnails.one'
                if count == 1
                else 'prejsons.games_fetching_thumbnails.other',
                count=count,
            )
        )
        self.refresh_btn.setEnabled(True)
        self._populate(games)
        self._update_thumbnail_status()

    def _on_clog_failed(self, err: str) -> None:
        self.status_label.setText(tr('ui.gui.prejsons_dialog.failed_to_load_value', value0=err))
        self.refresh_btn.setEnabled(True)
        # Still show custom dumps and the add card even if CLOG fails
        self._load_custom_cards()
        self._place_all()

    # Card population

    def _populate(self, games: list[GameEntry]) -> None:
        for g in games:
            card = self._make_card(g)
            self._cards.append(card)
            place_id = g['placeId']
            if place_id:
                self._start_card_meta(card, place_id, g['created'], g['updated'])

        self._load_custom_cards()
        self._place_all()

    def _make_card(self, g: GameEntry, dump_file: Path | None = None) -> GameCard:
        """Build a GameCard from a normalised game dict."""
        card = GameCard(self.container)
        card.set_data(g['name'], g['created'], g['updated'], g['credit'])

        gh_url = g['github'].strip()
        rep_url = g['replacement'].strip()

        if gh_url:
            card.assets_btn.setVisible(True)

            def open_assets(_checked: bool = False, url: str = gh_url) -> None:
                self._fetch_and_open(url)

            card.assets_btn.clicked.connect(open_assets)
        if rep_url:
            card.replacements_btn.setVisible(True)

            def open_replacements(_checked: bool = False, url: str = rep_url) -> None:
                self._fetch_and_open(url)

            card.replacements_btn.clicked.connect(open_replacements)

        if dump_file is not None:
            card.enable_delete_menu(dump_file, self._delete_custom_card)

        return card

    def _load_custom_cards(self) -> None:
        """Append cards for all saved custom dump files."""
        for g, fp in _load_custom_dumps():
            card = self._make_card(g, dump_file=fp)
            self._cards.append(card)
            place_id = g['placeId']
            if place_id:
                self._start_card_meta(card, place_id, g['created'], g['updated'])

    def _start_card_meta(self, card: GameCard, place_id: int, cr: str, up: str) -> None:
        # Serve from cache if available — no network round-trip needed
        if place_id in _meta_cache:
            card.set_data(*_meta_cache[place_id])
        else:
            meta_w = _CardMetaWorker(place_id, cr, up)
            meta_w.name_ready.connect(card.set_data)
            self._workers.append(meta_w)
            meta_w.start()

        if place_id in _thumb_bytes_cache:
            pix = QPixmap()
            if pix.loadFromData(_thumb_bytes_cache[place_id]):
                card.set_thumbnail(pix)
        else:
            self._thumbs_pending += 1
            generation = self._load_generation
            thumb_w = _CardThumbWorker(place_id)
            thumb_w.thumb_ready.connect(card.set_thumbnail)
            thumb_w.finished.connect(lambda g=generation: self._on_thumb_worker_finished(g))
            self._workers.append(thumb_w)
            thumb_w.start()

    def _on_thumb_worker_finished(self, generation: int) -> None:
        if generation != self._load_generation:
            return
        self._thumbs_finished += 1
        self._update_thumbnail_status()

    def _update_thumbnail_status(self) -> None:
        total_games = len(self._cards)
        if self._thumbs_pending <= 0:
            self.status_label.setText(
                tr(
                    'prejsons.games_count.one'
                    if total_games == 1
                    else 'prejsons.games_count.other',
                    count=total_games,
                )
            )
            return
        if self._thumbs_finished >= self._thumbs_pending:
            self.status_label.setText(
                tr(
                    'prejsons.games_count.one'
                    if total_games == 1
                    else 'prejsons.games_count.other',
                    count=total_games,
                )
            )
            return
        self.status_label.setText(
            tr(
                'prejsons.games_fetching_thumbnails.one'
                if total_games == 1
                else 'prejsons.games_fetching_thumbnails.other',
                count=total_games,
            )
        )

    # Grid layout helpers

    def _get_cols(self) -> int:
        vp = _scroll_area(self).viewport()
        available = vp.width() if vp else (self.width() - 30)
        return max(1, available // (_CARD_W + self.grid.spacing()))

    def _place_all(self) -> None:
        """Layout all cards, respecting the current search filter."""
        text = self.search_edit.text().strip().lower()
        visible: list[GameCard] = []
        for card in self._cards:
            show = not text or text in _card_game_name(card).lower()
            card.setVisible(show)
            if show:
                visible.append(card)
        self._place_cards(visible)

    def _place_cards(self, visible: list[GameCard]) -> None:
        """Remove all widgets from grid, re-add visible data cards, then add card."""
        for card in self._cards:
            self.grid.removeWidget(card)
        self.grid.removeWidget(self._add_card)

        cols = self._get_cols()
        self._last_cols = cols

        for c in range(max(self.grid.columnCount(), cols) + 1):
            self.grid.setColumnStretch(c, 0)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

        for i, card in enumerate(visible):
            self.grid.addWidget(card, i // cols, i % cols)

        n = len(visible)
        self.grid.addWidget(self._add_card, n // cols, n % cols)

    # Search / filter

    def _apply_filter(self) -> None:
        self._place_all()

    def resizeEvent(self, event: QResizeEvent) -> None:  # ruff: ignore[invalid-function-name]
        super().resizeEvent(event)
        self._resize_timer.start(60)

    def _on_resize_settled(self) -> None:
        cols = self._get_cols()
        if cols == self._last_cols:
            return
        visible = [c for c in self._cards if c.isVisible()]
        self._place_cards(visible)

    # Refresh

    def _do_refresh(self) -> None:
        self.search_edit.clear()
        self.grid.removeWidget(self._add_card)
        for card in self._cards:
            self.grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._start_load()

    # Custom dump — add dialog

    def _open_add_dump_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr('ui.gui.prejsons_dialog.import_custom_game_dump'))
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 12, 12, 12)

        # Example format
        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.expected_json_format')))
        example = QTextEdit()
        example.setReadOnly(True)
        example.setMaximumHeight(110)
        example.setStyleSheet("font-family: 'Courier New', monospace; font-size: 9pt;")
        example.setPlainText(
            '{\n'
            '  "name": "My Game",\n'
            '  "placeId": 12345,\n'
            '  "credit": "YourName",\n'
            '  "github": "https://raw.githubusercontent.com/.../assets.json",\n'
            '  "replacement": "https://raw.githubusercontent.com/.../replacements.json"\n'
            '}'
        )
        layout.addWidget(example)

        # Manual form
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)
        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.fill_in_manually')))

        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.name')))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr('ui.gui.prejsons_dialog.my_game'))
        layout.addWidget(name_edit)

        layout.addWidget(
            QLabel(tr('ui.gui.prejsons_dialog.place_id_optional_fetches_real_name_thumbnail'))
        )
        placeid_edit = QLineEdit()
        placeid_edit.setPlaceholderText(tr('ui.gui.prejsons_dialog.12345'))
        layout.addWidget(placeid_edit)

        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.assets_url_github')))
        assets_row = QHBoxLayout()
        assets_edit = FileDropLineEdit()
        assets_edit.setPlaceholderText(
            tr('ui.gui.prejsons_dialog.https_raw_githubusercontent_com_assets_json')
        )
        assets_row.addWidget(assets_edit)
        assets_browse = QPushButton(tr('ui.gui.prejsons_dialog.browse'))
        assets_browse.setMinimumWidth(max(80, assets_browse.sizeHint().width()))
        assets_row.addWidget(assets_browse)
        layout.addLayout(assets_row)
        assets_browse.clicked.connect(
            lambda: (
                path := QFileDialog.getOpenFileName(
                    dlg,
                    tr('ui.gui.prejsons_dialog.select_assets_json'),
                    '',
                    tr('ui.gui.prejsons_dialog.json_files_json_all_files'),
                )[0],
                assets_edit.setText(path) if path else None,
            )
        )

        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.replacements_url_replacement')))
        rep_row = QHBoxLayout()
        rep_edit = FileDropLineEdit()
        rep_edit.setPlaceholderText(
            tr('ui.gui.prejsons_dialog.https_raw_githubusercontent_com_replacements_json')
        )
        rep_row.addWidget(rep_edit)
        rep_browse = QPushButton(tr('ui.gui.prejsons_dialog.browse'))
        rep_browse.setMinimumWidth(max(80, rep_browse.sizeHint().width()))
        rep_row.addWidget(rep_browse)
        layout.addLayout(rep_row)
        rep_browse.clicked.connect(
            lambda: (
                path := QFileDialog.getOpenFileName(
                    dlg,
                    tr('ui.gui.prejsons_dialog.select_replacements_json'),
                    '',
                    tr('ui.gui.prejsons_dialog.json_files_json_all_files'),
                )[0],
                rep_edit.setText(path) if path else None,
            )
        )

        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.credit_optional')))
        credit_edit = QLineEdit()
        credit_edit.setPlaceholderText(tr('ui.gui.prejsons_dialog.your_name'))
        layout.addWidget(credit_edit)

        # OR import from URL / file
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)
        layout.addWidget(QLabel(tr('ui.gui.prejsons_dialog.or_import_from_url_file')))

        url_edit = FileDropLineEdit()
        url_edit.setPlaceholderText(
            tr('ui.gui.prejsons_dialog.https_raw_githubusercontent_com_dump_json')
        )
        layout.addWidget(url_edit)

        file_btn = QPushButton(tr('ui.gui.prejsons_dialog.import_from_file'))
        layout.addWidget(file_btn)

        def pick_file() -> None:
            path, _ = QFileDialog.getOpenFileName(
                dlg,
                tr('ui.gui.prejsons_dialog.select_json_dump'),
                '',
                tr('ui.gui.prejsons_dialog.json_files_json_all_files'),
            )
            if path:
                url_edit.setText(path)

        file_btn.clicked.connect(pick_file)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr('ui.gui.prejsons_dialog.import'))
        cancel_btn = QPushButton(tr('ui.gui.prejsons_dialog.cancel'))
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        cancel_btn.clicked.connect(dlg.reject)

        def do_import() -> None:
            name_text = name_edit.text().strip()
            placeid_text = placeid_edit.text().strip()

            if name_text or placeid_text:
                # Build from form fields
                form_data: dict[str, object] = {
                    'name': name_text or (f'Place {placeid_text}' if placeid_text else 'Unknown')
                }
                if placeid_text:
                    with contextlib.suppress(ValueError):
                        form_data['placeId'] = int(placeid_text)
                if assets_edit.text().strip():
                    form_data['github'] = assets_edit.text().strip()
                if rep_edit.text().strip():
                    form_data['replacement'] = rep_edit.text().strip()
                if credit_edit.text().strip():
                    form_data['credit'] = credit_edit.text().strip()
                data: object = form_data
            else:
                url_text = url_edit.text().strip()
                if not url_text:
                    QMessageBox.warning(
                        dlg,
                        tr('ui.gui.prejsons_dialog.import_failed'),
                        tr('ui.gui.prejsons_dialog.fill_in_the_name_field_or_provide'),
                    )
                    return

                if Path(url_text).is_file():
                    try:
                        data = json.loads(
                            Path(url_text).read_text(encoding='utf-8', errors='ignore')
                        )
                    except Exception as e:  # ruff: ignore[blind-except]
                        QMessageBox.warning(
                            dlg,
                            tr('ui.gui.prejsons_dialog.import_failed'),
                            tr('ui.gui.prejsons_dialog.could_not_read_file_value', value0=e),
                        )
                        return
                elif url_text.startswith(('http://', 'https://')):
                    try:
                        raw = _http_get(url_text, timeout=15)
                        data = json.loads(raw.decode('utf-8'))
                    except Exception as e:  # ruff: ignore[blind-except]
                        QMessageBox.warning(
                            dlg,
                            tr('ui.gui.prejsons_dialog.import_failed'),
                            tr('ui.gui.prejsons_dialog.could_not_fetch_json_value', value0=e),
                        )
                        return
                else:
                    QMessageBox.warning(
                        dlg,
                        tr('ui.gui.prejsons_dialog.import_failed'),
                        tr('ui.gui.prejsons_dialog.enter_a_url_or_path_to_a'),
                    )
                    return

            # Wrap bare single-entry dicts so _normalize_games handles them
            data_source: object = data
            if _is_dict(data_source):
                data_dict = _preserve_object_dict(data_source)
                if 'games' not in data_dict and (
                    isinstance(data_dict.get('name'), str) or data_dict.get('placeId') is not None
                ):
                    wrapped: object = {'games': {'_': data_dict}}
                else:
                    wrapped = data
            else:
                wrapped = data

            games = _normalize_games(wrapped)
            if not games:
                QMessageBox.warning(
                    dlg,
                    tr('ui.gui.prejsons_dialog.import_failed'),
                    tr('ui.gui.prejsons_dialog.no_valid_game_entries_found_check_the'),
                )
                return

            CUSTOM_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
            dump_path = CUSTOM_DUMPS_DIR / f'{uuid.uuid4().hex}.json'
            try:
                dump_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            except Exception as e:  # ruff: ignore[blind-except]
                QMessageBox.warning(
                    dlg,
                    tr('ui.gui.prejsons_dialog.import_failed'),
                    tr('ui.gui.prejsons_dialog.could_not_save_value', value0=e),
                )
                return

            # Save originals/replacements for each game entry so they appear
            # in the PreJsons system just like official downloads.
            # Also update the paths in each game entry to point to the copied
            # files so the cards and the saved dump use the right location.
            ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
            REPLACEMENTS_DIR.mkdir(parents=True, exist_ok=True)
            for g in games:
                raw_name = g['name'] or (f'Place {g["placeId"]}' if g['placeId'] else None)
                if not raw_name:
                    continue
                fname = _safe_filename(raw_name)
                url_targets: tuple[tuple[Literal['github', 'replacement'], Path], ...] = (
                    ('github', ORIGINALS_DIR),
                    ('replacement', REPLACEMENTS_DIR),
                )
                for url_key, dest_dir in url_targets:
                    url_or_path = _entry_url(g, url_key).strip()
                    if not url_or_path:
                        continue
                    dest_path = dest_dir / f'{fname}.json'
                    try:
                        content = _fetch_or_read(url_or_path)
                        dest_path.write_bytes(content)
                        # Update the game entry to point to the copied file
                        _set_entry_url(g, url_key, str(dest_path))
                    except Exception:  # ruff: ignore[blind-except, try-except-pass]
                        pass  # Non-fatal — keep original path if copy fails

            # Re-save the dump with updated paths so they survive dialog restarts
            try:
                dump_path.write_text(
                    json.dumps(
                        {'games': {'_': games[0]}}
                        if len(games) == 1
                        else {'games': {g['name']: g for g in games}},
                        indent=2,
                    ),
                    encoding='utf-8',
                )
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass

            for g in games:
                card = self._make_card(g, dump_file=dump_path)
                self._cards.append(card)
                place_id = g['placeId']
                if place_id:
                    self._start_card_meta(card, place_id, g['created'], g['updated'])

            self._place_all()
            dlg.accept()

        ok_btn.clicked.connect(do_import)
        dlg.exec()

    # Custom dump — delete

    def _delete_custom_card(self, card: GameCard) -> None:
        dump_file = _card_dump_file(card)
        if dump_file:
            try:
                dump_file.unlink(missing_ok=True)
            except Exception as e:  # ruff: ignore[blind-except]
                print(f'[CustomDump] Delete failed: {e}')

        if card in self._cards:
            self._cards.remove(card)
        self.grid.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._place_all()

    # Open JSON in tree viewer

    def _fetch_and_open(self, url: str) -> None:
        parent = self.parent()
        cfg = _parent_config_manager(parent)
        if cfg is None or cfg.close_scraped_games_menu_on_open:
            self.close()

        # Local file path - read directly
        p = Path(url)
        if p.is_file():
            try:
                data = _preserve_json(json.loads(p.read_text(encoding='utf-8', errors='ignore')))
                self._open_viewer(data, p.name)
            except Exception as e:  # ruff: ignore[blind-except]
                QMessageBox.warning(
                    self,
                    tr('ui.gui.prejsons_dialog.error'),
                    tr('ui.gui.prejsons_dialog.failed_to_read_file_value', value0=e),
                )
            return

        fetch_w = _JsonFetchWorker(url)
        fetch_w.done.connect(self._open_viewer)
        fetch_w.failed.connect(self._on_json_fetch_failed)
        self._workers.append(fetch_w)
        fetch_w.start()

    def _on_json_fetch_failed(self, err: str) -> None:
        QMessageBox.warning(
            self,
            tr('ui.gui.prejsons_dialog.error'),
            tr('ui.gui.prejsons_dialog.failed_to_load_json_value', value0=err),
        )

    def _open_viewer(self, data: JsonValue, filename: str) -> None:
        from .json_viewer import JsonTreeViewer  # ruff: ignore[import-outside-top-level]

        parent = self.parent()

        def on_ids(ids: list[ImportValue]) -> None:
            _append_replace_ids(parent, ids)

        def on_repl(val: ImportValue) -> None:
            _set_replacement_value(parent, val)

        config_manager = _parent_config_manager(parent)
        viewer = JsonTreeViewer(
            None,
            data,
            filename,
            on_import_ids=on_ids,
            on_import_replacement=on_repl,
            config_manager=config_manager,
        )
        viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        viewer.destroyed.connect(self._on_viewer_destroyed)
        viewer.show()
        self._viewers.append(viewer)

    def _on_viewer_destroyed(self, *_: object) -> None:
        """Remove a JSON viewer after it closes."""
        viewer = _preserve_dialog(self.sender())
        if viewer is not None and viewer in self._viewers:
            self._viewers.remove(viewer)

    def closeEvent(self, event: QCloseEvent) -> None:  # ruff: ignore[invalid-function-name]
        """Close any open JSON viewer windows with the dialog."""
        for viewer in self._viewers[:]:
            try:
                viewer.close()
            except Exception:  # ruff: ignore[blind-except, try-except-pass]
                pass
        self._viewers.clear()
        super().closeEvent(event)
