"""PreJsons browser dialog - shows game configs as interactive cards with thumbnails."""

import io
import json
import threading
import uuid
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QPalette, QPixmap, QImage, QFont, QIcon
from PyQt6.QtWidgets import (
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

from ..utils import CLOG_URL, PREJSONS_DIR, ORIGINALS_DIR, REPLACEMENTS_DIR, APP_NAME, get_icon_path

CUSTOM_DUMPS_DIR = PREJSONS_DIR / "custom_dumps"

_DEFAULT_THUMB_URL = (
    "https://static.wikia.nocookie.net/roblox/images/5/54/Default_Thumbnail_1_updated.png"
    "/revision/latest/scale-to-width-down/1000?cb=20250523160858"
)
_default_thumb_bytes_cache: list[bytes] = []  # single-element list so it's mutable

# Module-level caches (persist across dialog instances)

# place_id -> (name, created, updated)
_meta_cache: dict[int, tuple[str, str, str]] = {}
# place_id -> raw PNG bytes (QPixmap reconstructed in main thread from these)
_thumb_bytes_cache: dict[int, bytes] = {}


# HTTP helper

def _http_get(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "FleasionNT/1.2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_or_read(url_or_path: str, timeout: int = 15) -> bytes:
    """Fetch a URL or read a local file, returning raw bytes."""
    if url_or_path.startswith(("http://", "https://")):
        return _http_get(url_or_path, timeout=timeout)
    return Path(url_or_path).read_bytes()


def _safe_filename(name: str) -> str:
    """Strip characters that are invalid in Windows filenames."""
    import re
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:128] or "dump"


# PIL-based rounded thumbnail helper

def _make_rounded_pixmap(pix: QPixmap, w: int, h: int, radius: int = 6) -> QPixmap:
    """Scale-crop pixmap to (w × h) with rounded corners via PIL."""
    qimg = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = qimg.bits()
    ptr.setsize(qimg.width() * qimg.height() * 4)
    img = Image.frombytes("RGBA", (qimg.width(), qimg.height()), bytes(ptr))

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
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.width, img.height), radius=radius * scale, fill=255)
    img.putalpha(mask)
    img = img.resize((w, h), Image.LANCZOS)

    out = QImage(
        img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format.Format_RGBA8888
    )
    return QPixmap.fromImage(out)


def _preprocess_thumb_bytes(raw: bytes, w: int, h: int, radius: int = 6) -> tuple[bytes, int, int] | None:
    """Crop, resize, and round-corner raw image bytes using PIL only.

    Safe to call from a background thread — no Qt objects involved.
    Returns (rgba_bytes, w, h) ready to hand to QImage on the main thread.
    """
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
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
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
        mask = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, img.width, img.height), radius=radius * scale, fill=255)
        img.putalpha(mask)
        img = img.resize((w, h), Image.LANCZOS)
        return img.tobytes("raw", "RGBA"), w, h
    except Exception:
        return None

# Normalize game entry

def _normalize_entry(e: dict) -> dict | None:
    """Normalize a single game entry dict. Returns None if unusable."""
    if not isinstance(e, dict):
        return None
    name = e.get("name") or e.get("game") or ""
    pid = e.get("placeId") or e.get("place_id") or e.get("id")
    try:
        pid = int(pid) if pid is not None else None
    except Exception:
        pid = None
    if not name and pid:
        name = f"Place {pid}"
    if not name:
        return None
    credit = (
        e.get("credit") or e.get("Credit") or
        e.get("Owner") or e.get("owner") or
        e.get("author") or e.get("Author") or ""
    )
    return {
        "name": str(name),
        "created": str(e.get("created") or ""),
        "updated": str(e.get("updated") or ""),
        "credit": str(credit),
        "placeId": pid,
        "github": e.get("github") or "",
        "replacement": e.get("replacement") or e.get("Replacement") or "",
    }


def _normalize_games(data: dict) -> list[dict]:
    """Convert CLOG.json into a flat list of normalized game dicts."""
    if not isinstance(data, dict):
        return []
    raw = data.get("games", {})
    entries: list[dict] = []
    if isinstance(raw, dict):
        for name, cfg in raw.items():
            if isinstance(cfg, dict):
                e = dict(cfg)
                e.setdefault("name", name)
                entries.append(e)
            else:
                entries.append({"name": str(name)})
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]

    return [g for e in entries if (g := _normalize_entry(e)) is not None]


def _load_custom_dumps() -> list[tuple[dict, Path]]:
    """Load all valid custom dump JSON files. Returns (game_dict, file_path) tuples."""
    results = []
    try:
        CUSTOM_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
        for fp in sorted(CUSTOM_DUMPS_DIR.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8", errors="ignore"))
                # Support both single-entry {"name":...} and {"games":{...}} wrappers
                if isinstance(data, dict) and "games" not in data and (
                    isinstance(data.get("name"), str) or data.get("placeId") is not None
                ):
                    data = {"games": {"_": data}}
                games = _normalize_games(data)
                for g in games:
                    results.append((g, fp))
            except Exception as e:
                print(f"[CustomDump] Failed to load {fp.name}: {e}")
    except Exception as e:
        print(f"[CustomDump] Failed to scan dir: {e}")
    return results


# Worker threads

class _ClogWorker(QThread):
    """Fetches CLOG.json and builds the normalised game list."""
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            raw = _http_get(CLOG_URL, timeout=15)
            data = json.loads(raw.decode("utf-8"))
            games = _normalize_games(data)
            self.done.emit(games)
        except Exception as e:
            self.failed.emit(str(e))


class _CardMetaWorker(QThread):
    """Fetches real game name + dates for one card via Roblox API."""
    name_ready = pyqtSignal(str, str, str)   # name, created, updated

    def __init__(self, place_id: int, fallback_cr: str, fallback_up: str):
        super().__init__()
        self._pid = place_id
        self._cr = fallback_cr
        self._up = fallback_up

    def run(self):
        try:
            r1 = json.loads(_http_get(
                f"https://apis.roblox.com/universes/v1/places/{self._pid}/universe",
                timeout=10,
            ))
            universe_id = r1.get("universeId")
            if not universe_id:
                return
            r2 = json.loads(_http_get(
                f"https://games.roblox.com/v1/games?universeIds={universe_id}",
                timeout=10,
            ))
            entries = r2.get("data", [])
            if not entries:
                return
            e = entries[0]
            name = e.get("name") or ""
            created = e.get("created") or self._cr
            updated = e.get("updated") or self._up
            if name:
                _meta_cache[self._pid] = (name, created, updated)
                self.name_ready.emit(name, created, updated)
        except Exception:
            pass


def _get_default_thumb_bytes() -> bytes | None:
    """Return cached bytes for the default thumbnail, fetching once on first call."""
    if _default_thumb_bytes_cache:
        return _default_thumb_bytes_cache[0]
    try:
        data = _http_get(_DEFAULT_THUMB_URL, timeout=10)
        _default_thumb_bytes_cache.append(data)
        return data
    except Exception:
        return None

# Pre-fetch the default thumbnail in the background as soon as the module loads
threading.Thread(target=_get_default_thumb_bytes, daemon=True).start()


class _CardThumbWorker(QThread):
    """Fetches the thumbnail for one card via Roblox thumbnails API."""
    thumb_ready = pyqtSignal(QPixmap)

    def __init__(self, place_id: int):
        super().__init__()
        self._pid = place_id

    def run(self):
        img_bytes = None
        try:
            meta = json.loads(_http_get(
                f"https://thumbnails.roblox.com/v1/places/gameicons"
                f"?placeIds={self._pid}&size=512x512&format=Png",
                timeout=10,
            ))
            img_url = (meta.get("data") or [{}])[0].get("imageUrl") or ""
            if img_url:
                img_bytes = _http_get(img_url, timeout=10)
                _thumb_bytes_cache[self._pid] = img_bytes
        except Exception:
            pass

        if not img_bytes:
            img_bytes = _get_default_thumb_bytes()

        if img_bytes:
            pix = QPixmap()
            if pix.loadFromData(img_bytes):
                self.thumb_ready.emit(pix)


class _JsonFetchWorker(QThread):
    """Downloads a JSON file from a URL."""
    done = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            raw = _http_get(self._url, timeout=15)
            data = json.loads(raw.decode("utf-8"))
            filename = self._url.rsplit("/", 1)[-1] or "data.json"
            self.done.emit(data, filename)
        except Exception as e:
            self.failed.emit(str(e))


# Card constants

_CARD_W = 210
_CARD_H = 292
_THUMB_W = 196
_THUMB_H = 128


# Game Card Widget

class GameCard(QFrame):
    """A single game card: thumbnail + name + dates + action buttons."""

    def _apply_style(self, hover=False):
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        border = "rgba(255,255,255,0.22)" if dark else "rgba(0,0,0,0.18)"
        bg = ("rgba(255,255,255,0.07)" if hover else "rgba(255,255,255,0.04)") if dark else ("rgba(0,0,0,0.06)" if hover else "transparent")
        self.setStyleSheet(f"GameCard {{ border: 1px solid {border}; background: {bg}; }}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_style()
        self._game_name = ""
        self._dump_file: Path | None = None
        self._on_delete = None
        self._setup_ui()

    def _setup_ui(self):
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
            "background: palette(alternate-base); border-radius: 4px; color: palette(placeholder-text); font-size: 8pt;"
        )
        layout.addWidget(self.thumb_label)
        # Apply the default thumbnail immediately if already cached
        default_bytes = _default_thumb_bytes_cache[0] if _default_thumb_bytes_cache else None
        if default_bytes:
            _pix = QPixmap()
            if _pix.loadFromData(default_bytes):
                try:
                    _pix = _make_rounded_pixmap(_pix, _THUMB_W, _THUMB_H, radius=6)
                except Exception:
                    pass
                self.thumb_label.setPixmap(_pix)
                self.thumb_label.setStyleSheet("background: transparent;")

        self.name_label = QLabel("Unknown")
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(38)
        f = QFont()
        f.setBold(True)
        self.name_label.setFont(f)
        layout.addWidget(self.name_label)

        self.created_label = QLabel("")
        self.created_label.setStyleSheet("color: palette(placeholder-text); font-size: 7pt;")
        layout.addWidget(self.created_label)

        self.updated_label = QLabel("")
        self.updated_label.setStyleSheet("color: palette(placeholder-text); font-size: 7pt;")
        layout.addWidget(self.updated_label)

        self.credit_label = QLabel("")
        self.credit_label.setStyleSheet("color: palette(placeholder-text); font-size: 7pt;")
        layout.addWidget(self.credit_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.assets_btn = QPushButton("Assets")
        self.assets_btn.setVisible(False)
        self.assets_btn.setFixedHeight(22)
        btn_row.addWidget(self.assets_btn)

        self.replacements_btn = QPushButton("Replacements")
        self.replacements_btn.setVisible(False)
        self.replacements_btn.setFixedHeight(22)
        btn_row.addWidget(self.replacements_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def set_data(self, name: str, created: str = "", updated: str = "", credit: str = ""):
        self._game_name = name
        self.name_label.setText(name)
        if created:
            self.created_label.setText("Created: " + created[:10])
        if updated:
            self.updated_label.setText("Updated: " + updated[:10])
        if credit:
            self.credit_label.setText("Credit: " + credit)

    def set_thumbnail(self, pix: QPixmap):
        if not pix or pix.isNull():
            return
        try:
            baked = _make_rounded_pixmap(pix, _THUMB_W, _THUMB_H, radius=6)
        except Exception:
            baked = pix
        self.thumb_label.setPixmap(baked)
        self.thumb_label.setText("")
        self.thumb_label.setStyleSheet("background: transparent;")

    def enable_delete_menu(self, dump_file: Path, on_delete):
        """Wire up right-click → Delete for custom dump cards."""
        self._dump_file = dump_file
        self._on_delete = on_delete
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        delete_action = menu.addAction("Delete")
        action = menu.exec(self.mapToGlobal(pos))
        if action == delete_action and self._on_delete:
            self._on_delete(self)

    def enterEvent(self, event):
        self._apply_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_style()
        super().leaveEvent(event)


# Add Card Widget  (the "+" button at the end of the grid)

class AddCard(QFrame):
    """Clickable '+' card that opens the import dialog."""
    clicked = pyqtSignal()

    def _apply_style(self, hover=False):
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        border = "rgba(255,255,255,0.22)" if dark else "rgba(0,0,0,0.18)"
        bg = ("rgba(255,255,255,0.07)" if hover else "rgba(255,255,255,0.04)") if dark else ("rgba(0,0,0,0.06)" if hover else "transparent")
        self.setStyleSheet(f"AddCard {{ border: 1px solid {border}; background: {bg}; }}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_style()
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        plus = QLabel("+")
        plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plus.setStyleSheet("font-size: 36pt; color: palette(placeholder-text);")
        layout.addWidget(plus)

        sub = QLabel("Add custom dump")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
        layout.addWidget(sub)

        self.setLayout(layout)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._apply_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_style()
        super().leaveEvent(event)


# PreJsons Dialog

class PreJsonsDialog(QDialog):
    """Browse available PreJsons as interactive game cards with live thumbnails."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - Scraped Games")
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

    def _set_icon(self):
        if path := get_icon_path():
            self.setWindowIcon(QIcon(str(path)))

    def _setup_ui(self):
        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by game name…")
        self.search_edit.textChanged.connect(lambda: self._search_timer.start(80))
        bar.addWidget(self.search_edit)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._do_refresh)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        self.status_label = QLabel("Loading…")
        self.status_label.setStyleSheet("color: palette(placeholder-text); font-size: 8pt; padding-left: 2px;")
        root.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.container = QWidget()
        self.container.setAutoFillBackground(True)
        self.container.setBackgroundRole(QPalette.ColorRole.Base)
        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.container.setLayout(self.grid)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll)

        # Create the permanent add card
        self._add_card = AddCard(self.container)
        self._add_card.clicked.connect(self._open_add_dump_dialog)

        self.setLayout(root)

    # Load

    def _start_load(self):
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Fetching game list…")
        worker = _ClogWorker()
        worker.done.connect(self._on_clog_done)
        worker.failed.connect(self._on_clog_failed)
        self._workers.append(worker)
        worker.start()

    def _on_clog_done(self, games: list):
        count = len(games)
        self.status_label.setText(
            f"{count} game{'s' if count != 1 else ''} — fetching thumbnails…"
        )
        self.refresh_btn.setEnabled(True)
        self._populate(games)

    def _on_clog_failed(self, err: str):
        self.status_label.setText(f"Failed to load: {err}")
        self.refresh_btn.setEnabled(True)
        # Still show custom dumps and the add card even if CLOG fails
        self._load_custom_cards()
        self._place_all()

    # Card population

    def _populate(self, games: list[dict]):
        for g in games:
            card = self._make_card(g)
            self._cards.append(card)
            if g.get("placeId"):
                self._start_card_meta(card, g["placeId"], g.get("created", ""), g.get("updated", ""))

        self._load_custom_cards()
        self._place_all()

    def _make_card(self, g: dict, dump_file: Path | None = None) -> GameCard:
        """Build a GameCard from a normalised game dict."""
        card = GameCard(self.container)
        card.set_data(g["name"], g.get("created", ""), g.get("updated", ""), g.get("credit", ""))

        gh_url = (g.get("github") or "").strip()
        rep_url = (g.get("replacement") or "").strip()

        if gh_url:
            card.assets_btn.setVisible(True)
            card.assets_btn.clicked.connect(
                lambda _=False, u=gh_url: self._fetch_and_open(u)
            )
        if rep_url:
            card.replacements_btn.setVisible(True)
            card.replacements_btn.clicked.connect(
                lambda _=False, u=rep_url: self._fetch_and_open(u)
            )

        if dump_file is not None:
            card.enable_delete_menu(dump_file, self._delete_custom_card)

        return card

    def _load_custom_cards(self):
        """Append cards for all saved custom dump files."""
        for g, fp in _load_custom_dumps():
            card = self._make_card(g, dump_file=fp)
            self._cards.append(card)
            if g.get("placeId"):
                self._start_card_meta(card, g["placeId"], g.get("created", ""), g.get("updated", ""))

    def _start_card_meta(self, card: GameCard, place_id: int, cr: str, up: str):
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
            thumb_w = _CardThumbWorker(place_id)
            thumb_w.thumb_ready.connect(card.set_thumbnail)
            self._workers.append(thumb_w)
            thumb_w.start()

    # Grid layout helpers

    def _get_cols(self) -> int:
        vp = self.scroll.viewport()
        available = vp.width() if vp else (self.width() - 30)
        return max(1, available // (_CARD_W + self.grid.spacing()))

    def _place_all(self):
        """Layout all cards, respecting the current search filter."""
        text = self.search_edit.text().strip().lower()
        visible = []
        for card in self._cards:
            show = not text or text in card._game_name.lower()
            card.setVisible(show)
            if show:
                visible.append(card)
        self._place_cards(visible)

    def _place_cards(self, visible: list):
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

    def _apply_filter(self):
        self._place_all()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(60)

    def _on_resize_settled(self):
        cols = self._get_cols()
        if cols == self._last_cols:
            return
        visible = [c for c in self._cards if c.isVisible()]
        self._place_cards(visible)

    # Refresh

    def _do_refresh(self):
        self.search_edit.clear()
        self.grid.removeWidget(self._add_card)
        for card in self._cards:
            self.grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._start_load()

    # Custom dump — add dialog

    def _open_add_dump_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Import custom game dump")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 12, 12, 12)

        # Example format
        layout.addWidget(QLabel("Expected JSON format:"))
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
        layout.addWidget(QLabel("Fill in manually:"))

        layout.addWidget(QLabel("Name:"))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("My Game")
        layout.addWidget(name_edit)

        layout.addWidget(QLabel("Place ID (optional — fetches real name + thumbnail automatically):"))
        placeid_edit = QLineEdit()
        placeid_edit.setPlaceholderText("12345")
        layout.addWidget(placeid_edit)

        layout.addWidget(QLabel("Assets URL (github):"))
        assets_row = QHBoxLayout()
        assets_edit = QLineEdit()
        assets_edit.setPlaceholderText("https://raw.githubusercontent.com/.../assets.json")
        assets_row.addWidget(assets_edit)
        assets_browse = QPushButton("Browse…")
        assets_browse.setFixedWidth(80)
        assets_row.addWidget(assets_browse)
        layout.addLayout(assets_row)
        assets_browse.clicked.connect(lambda: (
            path := QFileDialog.getOpenFileName(dlg, "Select Assets JSON", "", "JSON Files (*.json);;All Files (*)")[0],
            assets_edit.setText(path) if path else None,
        ))

        layout.addWidget(QLabel("Replacements URL (replacement):"))
        rep_row = QHBoxLayout()
        rep_edit = QLineEdit()
        rep_edit.setPlaceholderText("https://raw.githubusercontent.com/.../replacements.json")
        rep_row.addWidget(rep_edit)
        rep_browse = QPushButton("Browse…")
        rep_browse.setFixedWidth(80)
        rep_row.addWidget(rep_browse)
        layout.addLayout(rep_row)
        rep_browse.clicked.connect(lambda: (
            path := QFileDialog.getOpenFileName(dlg, "Select Replacements JSON", "", "JSON Files (*.json);;All Files (*)")[0],
            rep_edit.setText(path) if path else None,
        ))

        layout.addWidget(QLabel("Credit (optional):"))
        credit_edit = QLineEdit()
        credit_edit.setPlaceholderText("Your name")
        layout.addWidget(credit_edit)

        # OR import from URL / file
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)
        layout.addWidget(QLabel("OR import from URL / file:"))

        url_edit = QLineEdit()
        url_edit.setPlaceholderText("https://raw.githubusercontent.com/.../dump.json")
        layout.addWidget(url_edit)

        file_btn = QPushButton("Import from file…")
        layout.addWidget(file_btn)

        def pick_file():
            path, _ = QFileDialog.getOpenFileName(dlg, "Select JSON dump", "", "JSON Files (*.json);;All Files (*)")
            if path:
                url_edit.setText(path)
        file_btn.clicked.connect(pick_file)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Import")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        cancel_btn.clicked.connect(dlg.reject)

        def do_import():
            name_text = name_edit.text().strip()
            placeid_text = placeid_edit.text().strip()

            if name_text or placeid_text:
                # Build from form fields
                data: dict = {"name": name_text or (f"Place {placeid_text}" if placeid_text else "Unknown")}
                if placeid_text:
                    try:
                        data["placeId"] = int(placeid_text)
                    except ValueError:
                        pass
                if assets_edit.text().strip():
                    data["github"] = assets_edit.text().strip()
                if rep_edit.text().strip():
                    data["replacement"] = rep_edit.text().strip()
                if credit_edit.text().strip():
                    data["credit"] = credit_edit.text().strip()
            else:
                url_text = url_edit.text().strip()
                if not url_text:
                    QMessageBox.warning(dlg, "Import failed", "Fill in the Name field, or provide a URL / file path.")
                    return

                if Path(url_text).is_file():
                    try:
                        data = json.loads(Path(url_text).read_text(encoding="utf-8", errors="ignore"))
                    except Exception as e:
                        QMessageBox.warning(dlg, "Import failed", f"Could not read file:\n{e}")
                        return
                elif url_text.startswith(("http://", "https://")):
                    try:
                        raw = _http_get(url_text, timeout=15)
                        data = json.loads(raw.decode("utf-8"))
                    except Exception as e:
                        QMessageBox.warning(dlg, "Import failed", f"Could not fetch JSON:\n{e}")
                        return
                else:
                    QMessageBox.warning(dlg, "Import failed", "Enter a URL or path to a local JSON file.")
                    return

            # Wrap bare single-entry dicts so _normalize_games handles them
            if isinstance(data, dict) and "games" not in data and (
                isinstance(data.get("name"), str) or data.get("placeId") is not None
            ):
                wrapped = {"games": {"_": data}}
            else:
                wrapped = data

            games = _normalize_games(wrapped)
            if not games:
                QMessageBox.warning(dlg, "Import failed", "No valid game entries found.\nCheck the format.")
                return

            CUSTOM_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
            dump_path = CUSTOM_DUMPS_DIR / f"{uuid.uuid4().hex}.json"
            try:
                dump_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(dlg, "Import failed", f"Could not save:\n{e}")
                return

            # Save originals/replacements for each game entry so they appear
            # in the PreJsons system just like official downloads.
            # Also update the paths in each game entry to point to the copied
            # files so the cards and the saved dump use the right location.
            ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
            REPLACEMENTS_DIR.mkdir(parents=True, exist_ok=True)
            for g in games:
                raw_name = g.get("name") or (f"Place {g['placeId']}" if g.get("placeId") else None)
                if not raw_name:
                    continue
                fname = _safe_filename(raw_name)
                for url_key, dest_dir in (("github", ORIGINALS_DIR), ("replacement", REPLACEMENTS_DIR)):
                    url_or_path = g.get(url_key, "").strip()
                    if not url_or_path:
                        continue
                    dest_path = dest_dir / f"{fname}.json"
                    try:
                        content = _fetch_or_read(url_or_path)
                        dest_path.write_bytes(content)
                        # Update the game entry to point to the copied file
                        g[url_key] = str(dest_path)
                    except Exception:
                        pass  # Non-fatal — keep original path if copy fails

            # Re-save the dump with updated paths so they survive dialog restarts
            try:
                dump_path.write_text(json.dumps({"games": {"_": games[0]}} if len(games) == 1 else {"games": {g["name"]: g for g in games}}, indent=2), encoding="utf-8")
            except Exception:
                pass

            for g in games:
                card = self._make_card(g, dump_file=dump_path)
                self._cards.append(card)
                if g.get("placeId"):
                    self._start_card_meta(card, g["placeId"], g.get("created", ""), g.get("updated", ""))

            self._place_all()
            dlg.accept()

        ok_btn.clicked.connect(do_import)
        dlg.exec()

    # Custom dump — delete

    def _delete_custom_card(self, card: GameCard):
        if card._dump_file:
            try:
                card._dump_file.unlink(missing_ok=True)
            except Exception as e:
                print(f"[CustomDump] Delete failed: {e}")

        if card in self._cards:
            self._cards.remove(card)
        self.grid.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._place_all()

    # Open JSON in tree viewer

    def _fetch_and_open(self, url: str):
        cfg = getattr(self.parent(), 'config_manager', None)
        if cfg is None or cfg.close_scraped_games_on_open:
            self.close()

        # Local file path - read directly
        p = Path(url)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                self._open_viewer(data, p.name)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to read file:\n{e}")
            return

        fetch_w = _JsonFetchWorker(url)
        fetch_w.done.connect(self._open_viewer)
        fetch_w.failed.connect(
            lambda err: QMessageBox.warning(self, "Error", f"Failed to load JSON:\n{err}")
        )
        self._workers.append(fetch_w)
        fetch_w.start()

    def _open_viewer(self, data: object, filename: str):
        from .json_viewer import JsonTreeViewer

        parent = self.parent()

        def on_ids(ids):
            if hasattr(parent, 'replace_entry'):
                cur = parent.replace_entry.text()
                parent.replace_entry.setText(
                    (cur + ', ' if cur.strip() else '') + ', '.join(str(x) for x in ids)
                )

        def on_repl(val):
            if hasattr(parent, 'replacement_entry'):
                parent.replacement_entry.setText(str(val))

        config_manager = getattr(parent, 'config_manager', None)
        viewer = JsonTreeViewer(
            self,
            data,
            filename,
            on_import_ids=on_ids,
            on_import_replacement=on_repl,
            config_manager=config_manager,
        )
        viewer.show()
        self._viewers.append(viewer)
