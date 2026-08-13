"""Cache browser model and operations for the QML dashboard."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

from PySide6.QtCore import QMimeData, QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement

from ..cache.cache_manager import CacheManager
from ..cache.roblox_document import classify_roblox_document
from .animation_preview import AnimationPreviewApi
from .font_preview import FontPreviewApi
from ..utils import open_folder
from .models import DictListModel, SelectionModel
from .roblox_document_preview import RobloxDocumentPreviewApi
from .tasks import TaskState
from .texture_pack_preview import TexturePackPreviewApi

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_ASSET_ROLES: Final = (
    'key',
    'assetId',
    'assetType',
    'typeName',
    'name',
    'creator',
    'creatorId',
    'creatorType',
    'creatorUrl',
    'hash',
    'size',
    'sizeText',
    'cachedAt',
    'cachedAtText',
    'updatedAt',
    'createdAt',
    'url',
    'sourceUrl',
    'previewUrl',
    'searchText',
)

_ASSET_ID_SEPARATOR: Final = re.compile(r'[\s,;]+')
_ROBLOX_COOKIE_DOMAIN: Final = '.roblox.com'
_TRUSTED_ASSET_DOMAINS: Final = ('roblox.com', 'rbxcdn.com')
_MAX_ASSET_REDIRECTS: Final = 5
_SORT_KEYS: Final = frozenset({'typeName', 'name', 'creator', 'assetId', 'size', 'cachedAt'})
_SEARCH_COLUMNS: Final = frozenset(
    {'id', 'type', 'name', 'creator', 'hash', 'cached_at', 'updated_at', 'created_at', 'url'}
)
_DEFAULT_SEARCH_COLUMNS: Final = frozenset(
    {'id', 'type', 'name', 'creator', 'hash', 'cached_at'}
)
_VISIBLE_COLUMNS: Final = frozenset({'type', 'asset', 'size', 'cached_at'})
_VIEW_SETTINGS_DELAY_MS: Final = 400


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or suffix == 'GB':
            return f'{size:.0f} {suffix}' if suffix == 'B' else f'{size:.1f} {suffix}'
        size /= 1024
    return '0 B'


def _format_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime('%b %d, %H:%M')


def _parse_asset_ids(value: str | list[str]) -> set[str]:
    parts = value if isinstance(value, list) else _ASSET_ID_SEPARATOR.split(value)
    normalized: set[str] = set()
    for part in parts:
        candidate = str(part).strip()
        if not candidate.isdecimal():
            continue
        asset_id = str(int(candidate))
        if asset_id != '0':
            normalized.add(asset_id)
    return normalized


def _is_trusted_asset_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or '').casefold().rstrip('.')
    return parsed.scheme.casefold() == 'https' and any(
        host == domain or host.endswith(f'.{domain}') for domain in _TRUSTED_ASSET_DOMAINS
    )


def _roblox_session(cookie: str | None) -> Any:
    import requests

    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    session.headers.update({'User-Agent': 'Roblox/WinInet'})
    if cookie:
        session.cookies.set(
            '.ROBLOSECURITY',
            cookie,
            domain=_ROBLOX_COOKIE_DOMAIN,
            path='/',
            secure=True,
        )
    return session


def _copy_file_urls(paths: list[Path]) -> bool:
    clipboard = QGuiApplication.clipboard()
    if clipboard is None or not paths:
        return False
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    clipboard.setMimeData(mime_data)
    return True


def _creator_details(asset: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, str, str]:
    creator = metadata.get('creator')
    creator_data = creator if isinstance(creator, dict) else {}
    creator_id = str(
        asset.get('creator_id')
        or asset.get('resolved_creator_id')
        or metadata.get('creator_id')
        or metadata.get('creatorTargetId')
        or creator_data.get('targetId')
        or creator_data.get('id')
        or ''
    )
    creator_type = str(
        asset.get('creator_type')
        or asset.get('resolved_creator_type')
        or metadata.get('creator_type')
        or metadata.get('creatorType')
        or creator_data.get('typeId')
        or creator_data.get('type')
        or ''
    )
    if not creator_id:
        return '', creator_type, ''
    if creator_type.casefold() in {'2', 'group'}:
        return creator_id, creator_type, f'https://www.roblox.com/communities/{creator_id}'
    return creator_id, creator_type, f'https://www.roblox.com/users/{creator_id}/profile'


@QmlElement
class CacheApi(QObject):
    """Expose cached assets through a filterable QML list model."""

    modelChanged = Signal()
    statsChanged = Signal()
    queryChanged = Signal()
    viewOptionsChanged = Signal()
    scraperEnabledChanged = Signal()
    blacklistChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)
    sendToReplacerRequested = Signal(str, bool)
    previewChanged = Signal()

    def __init__(
        self,
        cache_manager: CacheManager | None = None,
        cache_scraper: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache_manager or CacheManager()
        self._scraper = cache_scraper
        self._model = DictListModel(_ASSET_ROLES, parent=self)
        self._selection = SelectionModel(self)
        self._task = TaskState(self)
        settings = self._view_settings()
        self._query = str(settings.get('qml_cache_query') or '')
        self._type_filter = str(settings.get('qml_cache_type_filter') or '')
        saved_sort_key = str(settings.get('qml_cache_sort_key') or 'cachedAt')
        self._sort_key = saved_sort_key if saved_sort_key in _SORT_KEYS else 'cachedAt'
        self._sort_descending = bool(settings.get('qml_cache_sort_descending', True))
        saved_search_columns = settings.get('scraper_search_columns')
        self._search_columns = (
            {str(value) for value in saved_search_columns if str(value) in _SEARCH_COLUMNS}
            if isinstance(saved_search_columns, list)
            else set(_DEFAULT_SEARCH_COLUMNS)
        )
        if not self._search_columns:
            self._search_columns = set(_DEFAULT_SEARCH_COLUMNS)
        visibility = settings.get('scraper_column_visibility')
        saved_visibility = visibility if isinstance(visibility, dict) else {}
        self._visible_columns = {'asset'}
        for column, legacy_key in (
            ('type', 'type'),
            ('size', 'size'),
            ('cached_at', 'cached_at'),
        ):
            if bool(saved_visibility.get(legacy_key, True)):
                self._visible_columns.add(column)
        configured_blacklist = getattr(
            getattr(self._cache, 'config_manager', None),
            'scraper_blacklist',
            [],
        )
        self._blacklisted_ids = _parse_asset_ids(configured_blacklist)
        self._total_assets = 0
        self._total_size = 0
        self._asset_types: list[str] = []
        self._source_count = -1
        self._task_action = ''
        self._preview_kind = 'none'
        self._preview_text = ''
        self._preview_source = ''
        self._mesh_geometry: QObject | None = None
        self._font_preview = FontPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._document_preview = RobloxDocumentPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._document_preview.set_export_directory(getattr(self._cache, 'export_dir', None))
        self._animation_preview = AnimationPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._animation_preview.set_export_directory(getattr(self._cache, 'export_dir', None))
        self._texture_pack_preview = TexturePackPreviewApi(  # pyright: ignore[reportCallIssue]
            self._cache,
            self,
        )
        self._texture_pack_preview.set_export_directory(getattr(self._cache, 'export_dir', None))
        self._document_preview.errorOccurred.connect(self.errorOccurred)
        self._document_preview.notificationRequested.connect(self.notificationRequested)
        self._animation_preview.errorOccurred.connect(self.errorOccurred)
        self._animation_preview.notificationRequested.connect(self.notificationRequested)
        self._texture_pack_preview.errorOccurred.connect(self.errorOccurred)
        self._texture_pack_preview.notificationRequested.connect(self.notificationRequested)
        self._texture_pack_preview.loadRequested.connect(self._load_texture_map)
        self._preview_files: list[Path] = []
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(_VIEW_SETTINGS_DELAY_MS)
        self._settings_timer.timeout.connect(self._persist_view_settings)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._refresh_if_changed)
        self._poll_timer.start()
        self._task.succeeded.connect(self._on_task_succeeded)
        self._task.failed.connect(self._on_task_failed)
        self.refresh()

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(QObject, constant=True)
    def selection(self) -> QObject:
        return self._selection

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        if value == self._query:
            return
        self._query = value
        self.queryChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Property(str, notify=queryChanged)
    def typeFilter(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._type_filter

    @typeFilter.setter  # pyright: ignore[reportRedeclaration]
    def typeFilter(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip()
        if normalized == self._type_filter:
            return
        self._type_filter = normalized
        self.queryChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Property(str, notify=viewOptionsChanged)
    def sortKey(self) -> str:  # noqa: N802
        return self._sort_key

    @Property(bool, notify=viewOptionsChanged)
    def sortDescending(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._sort_descending

    @sortDescending.setter  # pyright: ignore[reportRedeclaration]
    def sortDescending(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._sort_descending:
            return
        self._sort_descending = value
        self.viewOptionsChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Property(list, notify=viewOptionsChanged)
    def visibleColumns(self) -> list[str]:  # noqa: N802
        return sorted(self._visible_columns)

    @Property(list, notify=viewOptionsChanged)
    def searchColumns(self) -> list[str]:  # noqa: N802
        return sorted(self._search_columns)

    @Slot(str)
    def toggleSort(self, key: str) -> None:  # noqa: N802
        if key not in _SORT_KEYS:
            return
        if key == self._sort_key:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_key = key
            self._sort_descending = key in {'size', 'cachedAt'}
        self.viewOptionsChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Slot(str)
    def setSortKey(self, key: str) -> None:  # noqa: N802
        if key not in _SORT_KEYS or key == self._sort_key:
            return
        self._sort_key = key
        self.viewOptionsChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Slot(str, bool)
    def setColumnVisible(self, key: str, visible: bool) -> None:  # noqa: N802
        if key not in _VISIBLE_COLUMNS or key == 'asset':
            return
        changed = False
        if visible and key not in self._visible_columns:
            self._visible_columns.add(key)
            changed = True
        elif not visible and key in self._visible_columns:
            self._visible_columns.remove(key)
            changed = True
        if changed:
            self.viewOptionsChanged.emit()
            self._schedule_view_settings_save()

    @Slot(str, bool)
    def setSearchColumnEnabled(self, key: str, enabled: bool) -> None:  # noqa: N802
        if key not in _SEARCH_COLUMNS:
            return
        updated = set(self._search_columns)
        if enabled:
            updated.add(key)
        elif len(updated) > 1:
            updated.discard(key)
        if updated == self._search_columns:
            return
        self._search_columns = updated
        self.viewOptionsChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Slot()
    def resetViewOptions(self) -> None:  # noqa: N802
        self._sort_key = 'cachedAt'
        self._sort_descending = True
        self._visible_columns = {'asset', 'type', 'size', 'cached_at'}
        self._search_columns = set(_DEFAULT_SEARCH_COLUMNS)
        self.viewOptionsChanged.emit()
        self._schedule_view_settings_save()
        self.refresh()

    @Property(int, notify=statsChanged)
    def totalAssets(self) -> int:  # noqa: N802
        return self._total_assets

    @Property(str, notify=statsChanged)
    def totalSizeText(self) -> str:  # noqa: N802
        return _format_bytes(self._total_size)

    @Property(list, notify=statsChanged)
    def assetTypes(self) -> list[str]:  # noqa: N802
        return list(self._asset_types)

    @Property(str, notify=blacklistChanged)
    def blacklistText(self) -> str:  # noqa: N802
        return ', '.join(sorted(self._blacklisted_ids, key=int))

    @Property(int, notify=blacklistChanged)
    def blacklistCount(self) -> int:  # noqa: N802
        return len(self._blacklisted_ids)

    @Property(bool, notify=scraperEnabledChanged)
    def scraperEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if self._scraper is None:
            return False
        for attribute in ('enabled', 'is_enabled', 'running'):
            value = getattr(self._scraper, attribute, None)
            if isinstance(value, bool):
                return value
        return True

    @scraperEnabled.setter  # pyright: ignore[reportRedeclaration]
    def scraperEnabled(self, enabled: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if self._scraper is None:
            return
        setter = getattr(self._scraper, 'set_enabled', None)
        if callable(setter):
            setter(enabled)
        elif hasattr(self._scraper, 'enabled'):
            self._scraper.enabled = enabled
        self.scraperEnabledChanged.emit()

    @Property(str, notify=previewChanged)
    def previewKind(self) -> str:  # noqa: N802
        return self._preview_kind

    @Property(str, notify=previewChanged)
    def previewText(self) -> str:  # noqa: N802
        return self._preview_text

    @Property(str, notify=previewChanged)
    def previewSource(self) -> str:  # noqa: N802
        return self._preview_source

    @Property(QObject, notify=previewChanged)
    def meshGeometry(self) -> QObject | None:  # noqa: N802
        return self._mesh_geometry

    @Property(QObject, constant=True)
    def fontPreview(self) -> QObject:  # noqa: N802
        return self._font_preview

    @Property(QObject, constant=True)
    def documentPreview(self) -> QObject:  # noqa: N802
        return self._document_preview

    @Property(QObject, constant=True)
    def animationPreview(self) -> QObject:  # noqa: N802
        return self._animation_preview

    @Property(QObject, constant=True)
    def texturePackPreview(self) -> QObject:  # noqa: N802
        return self._texture_pack_preview

    @Slot()
    def refresh(self) -> None:
        assets = self._cache.list_assets()
        self._source_count = len(assets)
        asset_types = sorted(
            {
                str(asset.get('type_name', 'Unknown'))
                for asset in assets
                if str(asset.get('id', '')) not in self._blacklisted_ids
            },
            key=str.casefold,
        )
        asset_types_changed = asset_types != self._asset_types
        self._asset_types = asset_types
        rows: list[dict[str, Any]] = []
        for asset in assets:
            metadata = asset.get('metadata') or {}
            name = str(asset.get('resolved_name') or metadata.get('name') or '')
            creator = str(
                asset.get('resolved_creator_name')
                or asset.get('creator_name')
                or metadata.get('creator_name')
                or metadata.get('creator')
                or ''
            )
            creator_id, creator_type, creator_url = _creator_details(asset, metadata)
            type_name = str(asset.get('type_name', 'Unknown'))
            asset_id = str(asset.get('id', ''))
            if asset_id in self._blacklisted_ids:
                continue
            size = int(asset.get('raw_size', asset.get('size', 0)) or 0)
            cached_at = str(asset.get('cached_at', ''))
            updated_at = str(asset.get('updated_at') or metadata.get('updated_at') or '')
            created_at = str(asset.get('created_at') or metadata.get('created_at') or '')
            url = str(asset.get('url', ''))
            row = {
                'key': f'{asset.get("type", 0)}_{asset_id}',
                'assetId': asset_id,
                'assetType': int(asset.get('type', 0)),
                'typeName': type_name,
                'name': name,
                'creator': creator,
                'creatorId': creator_id,
                'creatorType': creator_type,
                'creatorUrl': creator_url,
                'hash': str(asset.get('hash', '')),
                'size': size,
                'sizeText': _format_bytes(size),
                'cachedAt': cached_at,
                'cachedAtText': _format_timestamp(cached_at),
                'updatedAt': updated_at,
                'createdAt': created_at,
                'url': url,
                'sourceUrl': url,
                'previewUrl': (
                    f'image://fleasion-cache/{asset.get("type", 0)}/{asset_id}'
                    f'?v={asset.get("hash", "")}'
                ),
            }
            row['searchText'] = self._search_text(row)
            if self._type_filter and type_name != self._type_filter:
                continue
            query = self._query.strip().casefold()
            if query and query not in row['searchText'].casefold():
                continue
            rows.append(row)
        self._sort_rows(rows)
        self._model.replace_items(rows)
        stats = self._cache.get_cache_stats()
        total_assets = int(stats.get('total_assets', 0))
        total_size = int(stats.get('total_size', 0))
        if (
            total_assets != self._total_assets
            or total_size != self._total_size
            or asset_types_changed
        ):
            self._total_assets = total_assets
            self._total_size = total_size
            self.statsChanged.emit()
        self.modelChanged.emit()

    @Slot(str, result=int)
    def applyBlacklist(self, value: str) -> int:  # noqa: N802
        updated = _parse_asset_ids(value)
        if updated == self._blacklisted_ids:
            return len(updated)
        self._blacklisted_ids = updated
        config = getattr(self._cache, 'config_manager', None)
        if config is not None:
            config.scraper_blacklist = sorted(updated, key=int)
        self._selection.clear()
        self.blacklistChanged.emit()
        self.refresh()
        self.notificationRequested.emit(
            'Cache filter updated',
            f'{len(updated)} asset IDs hidden from the cache browser',
            'success',
        )
        return len(updated)

    @Slot()
    def clearBlacklist(self) -> None:  # noqa: N802
        self.applyBlacklist('')

    @Slot()
    def openCacheFolder(self) -> None:  # noqa: N802
        self._open_folder(getattr(self._cache, 'cache_dir', None), 'cache')

    @Slot()
    def openExportsFolder(self) -> None:  # noqa: N802
        self._open_folder(getattr(self._cache, 'export_dir', None), 'exports')

    @Slot(result=bool)
    def clearCache(self) -> bool:  # noqa: N802
        if self._task.busy:
            return False

        def clear() -> dict[str, int | str]:
            reset_scraper = getattr(self._scraper, 'reset_for_cache_clear', None)
            if callable(reset_scraper):
                reset_scraper()
            else:
                clear_tracking = getattr(self._scraper, 'clear_tracking', None)
                if callable(clear_tracking):
                    clear_tracking()
            clear_memory = getattr(self._cache, 'clear_memory_cache', None)
            if callable(clear_memory):
                clear_memory()
            return {'action': 'clear', 'count': int(self._cache.clear_cache())}

        self._task_action = 'clear'
        return self._task.run('Clearing cached assets…', clear)

    @Slot(list, result=list)
    def commonExportFormats(self, keys: list[str]) -> list[str]:  # noqa: N802
        rows = [self._row_for_key(key) for key in keys]
        rows = [row for row in rows if row]
        if not rows:
            return []
        formats = self._cache.get_available_export_formats_for_asset(
            str(rows[0]['assetId']), int(rows[0]['assetType'])
        )
        common = set(formats)
        for row in rows[1:]:
            common.intersection_update(
                self._cache.get_available_export_formats_for_asset(
                    str(row['assetId']), int(row['assetType'])
                )
            )
        return [format_name for format_name in formats if format_name in common]

    @Slot(list, str, result=bool)
    def exportAssets(self, keys: list[str], format_name: str) -> bool:  # noqa: N802
        if self._task.busy:
            return False
        rows = [self._row_for_key(key) for key in keys]
        rows = [row for row in rows if row]
        if not rows:
            self.errorOccurred.emit('Select at least one cached asset to export.')
            return False

        def export() -> dict[str, int | str]:
            exported = 0
            failed = 0
            for row in rows:
                try:
                    result = self._cache.export_asset(
                        str(row['assetId']),
                        int(row['assetType']),
                        resolved_name=str(row.get('name') or ''),
                        export_format=format_name or 'converted',
                    )
                except Exception:
                    result = None
                if result is None:
                    failed += 1
                else:
                    exported += 1
            return {
                'action': 'export',
                'count': exported,
                'failed': failed,
            }

        self._task_action = 'export'
        return self._task.run(f'Exporting {len(rows)} cached assets…', export)

    @Slot(str, result=bool)
    def convertedCopyAvailable(self, key: str) -> bool:  # noqa: N802
        row = self._row_for_key(key)
        return bool(row and self._preferred_converted_format(row))

    @Slot(list, result=bool)
    def copyConvertedAssets(self, keys: list[str]) -> bool:  # noqa: N802
        if self._task.busy:
            return False
        rows: list[dict[str, Any]] = []
        for key in keys:
            row = self._row_for_key(key)
            if not row:
                continue
            format_name = self._preferred_converted_format(row)
            if format_name:
                row['_copyFormat'] = format_name
                rows.append(row)
        if not rows:
            self.errorOccurred.emit('The selection has no supported converted file format.')
            return False

        def convert() -> dict[str, object]:
            paths: list[str] = []
            failed = 0
            for row in rows:
                format_name = str(row.get('_copyFormat') or '')
                if not format_name:
                    failed += 1
                    continue
                try:
                    result = self._cache.export_asset(
                        str(row['assetId']),
                        int(row['assetType']),
                        resolved_name=str(row.get('name') or ''),
                        export_format=format_name,
                    )
                except Exception:
                    result = None
                if result is None:
                    failed += 1
                else:
                    paths.append(str(result))
            return {
                'action': 'copy-converted',
                'paths': paths,
                'failed': failed,
            }

        self._task_action = 'copy-converted'
        return self._task.run(f'Preparing {len(rows)} converted file(s)…', convert)

    @Slot(list, str, result=bool)
    def exportGameDump(self, keys: list[str], destination_value: str) -> bool:  # noqa: N802
        rows = [self._row_for_key(key) for key in keys]
        rows = [row for row in rows if row]
        if not rows:
            self.errorOccurred.emit('Select at least one cached asset for the game dump.')
            return False

        by_type: dict[str, dict[str, int]] = {}
        name_counts: dict[tuple[str, str], int] = {}
        for row in rows:
            asset_id = str(row.get('assetId') or '')
            if not asset_id.isdecimal():
                continue
            type_name = str(row.get('typeName') or 'Unknown')
            name = str(row.get('name') or row.get('hash') or f'Asset {asset_id}')
            bucket = by_type.setdefault(type_name, {})
            count_key = (type_name, name)
            if name in bucket:
                name_counts[count_key] = name_counts.get(count_key, 1) + 1
                name = f'{name} ({name_counts[count_key]})'
            bucket[name] = int(asset_id)
        if not by_type:
            self.errorOccurred.emit('The selected rows did not contain valid Roblox asset IDs.')
            return False

        destination = self._local_path(destination_value)
        if not destination.suffix:
            destination = destination.with_suffix('.json')
        try:
            destination.write_text(
                json.dumps(
                    {key: by_type[key] for key in sorted(by_type, key=str.casefold)},
                    indent=2,
                ),
                encoding='utf-8',
            )
        except OSError as exc:
            self.errorOccurred.emit(f'Could not write the game dump: {exc}')
            return False
        total = sum(len(values) for values in by_type.values())
        self.notificationRequested.emit(
            'Game dump exported',
            f'{total} cached assets saved to {destination}',
            'success',
        )
        return True

    @Slot(str, result=bool)
    def loadAssets(self, value: str) -> bool:  # noqa: N802
        if self._task.busy:
            return False
        asset_ids = sorted(_parse_asset_ids(value), key=int)
        if not asset_ids:
            self.errorOccurred.emit('Enter at least one valid Roblox asset ID.')
            return False
        if len(asset_ids) > 100:
            self.errorOccurred.emit('Load at most 100 asset IDs at a time.')
            return False

        def load(cancel_event: threading.Event) -> dict[str, int | str]:
            if cancel_event.is_set():
                return {'action': 'load', 'count': 0, 'failed': 0}
            metadata_by_id = self._fetch_manual_metadata(asset_ids, cancel_event)
            loaded = 0
            failed = 0
            fetch_from_scraper = getattr(self._scraper, '_fetch_from_api', None)
            for asset_id in asset_ids:
                if cancel_event.is_set():
                    break
                metadata = metadata_by_id.get(asset_id, {})
                try:
                    data = (
                        fetch_from_scraper(asset_id)
                        if callable(fetch_from_scraper)
                        else self._fetch_manual_asset(asset_id, cancel_event)
                    )
                    if cancel_event.is_set():
                        break
                    type_value = metadata.get('type') or 1
                    asset_type = int(type_value)
                    stored = bool(
                        isinstance(data, bytes)
                        and data
                        and self._cache.store_asset(
                            asset_id,
                            asset_type,
                            data,
                            url=f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}',
                            metadata=metadata,
                        )
                    )
                except Exception:
                    stored = False
                if stored:
                    loaded += 1
                else:
                    failed += 1
            return {'action': 'load', 'count': loaded, 'failed': failed}

        self._task_action = 'load'
        return self._task.run_cancellable(f'Loading {len(asset_ids)} Roblox assets…', load)

    @Slot(list, result=bool)
    def deleteAssets(self, keys: list[str]) -> bool:  # noqa: N802
        assets: list[tuple[str, int]] = []
        for key in keys:
            row = self._row_for_key(key)
            if row:
                assets.append((str(row['assetId']), int(row['assetType'])))
        if not assets:
            return False
        deleted, failed = self._cache.delete_assets_batch(assets)
        self._selection.clear()
        self.refresh()
        if failed:
            self.errorOccurred.emit(f'{failed} cached assets could not be deleted.')
        self.notificationRequested.emit(
            'Cache updated',
            f'{deleted} cached assets deleted',
            'success' if not failed else 'warning',
        )
        return deleted > 0

    @Slot(str, str, str, result=bool)
    def exportAsset(self, key: str, format_name: str, destination_value: str) -> bool:  # noqa: N802
        row = self._row_for_key(key)
        if not row:
            return False
        destination = self._local_path(destination_value)
        try:
            result = self._cache.export_asset(
                str(row['assetId']),
                int(row['assetType']),
                output_path=destination,
                resolved_name=str(row.get('name') or ''),
                export_format=format_name or 'converted',
            )
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
            return False
        if result is None:
            self.errorOccurred.emit('The asset could not be exported in that format.')
            return False
        self.notificationRequested.emit('Asset exported', str(result), 'success')
        return True

    @Slot(str, result=list)
    def exportFormats(self, key: str) -> list[str]:  # noqa: N802
        row = self._row_for_key(key)
        if not row:
            return []
        return self._cache.get_available_export_formats_for_asset(
            str(row['assetId']), int(row['assetType'])
        )

    @Slot(str, bool)
    def sendToReplacer(self, key: str, as_replacement: bool) -> None:  # noqa: N802
        row = self._row_for_key(key)
        if row:
            self.sendToReplacerRequested.emit(str(row['assetId']), as_replacement)

    @Slot(str, result=dict)
    def asset(self, key: str) -> dict[str, Any]:
        return self._row_for_key(key)

    @Slot(str)
    def loadPreview(self, key: str) -> None:  # noqa: N802
        row = self._row_for_key(key)
        if not row:
            self._set_preview('none', '', '')
            return
        payload = self._cache.get_asset(str(row['assetId']), int(row['assetType']))
        if not payload:
            self._set_preview('unsupported', '', '')
            return
        type_name = str(row.get('typeName') or '').casefold()
        asset_type = int(row.get('assetType') or 0)
        label = str(row.get('name') or row.get('assetId') or key)
        if asset_type == 74 or type_name == 'fontface':
            if self._font_preview.load_bytes(payload):
                self._set_preview('font', '', '')
            else:
                self._set_preview('unsupported', '', '')
            return
        if asset_type == 24 or type_name == 'animation':
            if self._animation_preview.load_bytes(payload, label):
                self._set_preview('animation', '', '')
                return
        if asset_type == 63 or type_name == 'texturepack':
            if self._texture_pack_preview.load_bytes(payload, str(row.get('assetId') or '')):
                self._set_preview('texturepack', '', '')
            else:
                self._set_preview('unsupported', '', '')
            return
        document_kind = classify_roblox_document(payload)
        if document_kind is not None:
            if self._document_preview.load_bytes(payload, key, label):
                self._set_preview('document', '', '')
                return
        if type_name == 'image':
            self._set_preview('image', '', str(row['previewUrl']))
            return
        if type_name == 'audio':
            self._set_preview('audio', '', self._materialize_audio_preview(payload))
            return
        if type_name in {'mesh', 'meshpart', 'mesh hidden surface removal'}:
            from .mesh_geometry import MeshGeometry

            geometry = MeshGeometry()  # pyright: ignore[reportCallIssue]
            geometry.setParent(self)
            if geometry.load(payload):
                self._release_mesh_geometry()
                self._mesh_geometry = geometry
                self._set_preview('mesh', '', '', force=True)
                return
            geometry.setParent(None)
            geometry.deleteLater()
        if type_name in {'json', 'fontfamily'} or payload.lstrip().startswith((b'{', b'[')):
            try:
                import json

                value = json.loads(payload)
                text = json.dumps(value, indent=2, ensure_ascii=False)
            except UnicodeDecodeError, json.JSONDecodeError:
                text = payload.decode('utf-8', errors='replace')
            self._set_preview('text', text[:500_000], '')
            return
        if type_name in {'fontface', 'html', 'text'}:
            self._set_preview('text', payload.decode('utf-8', errors='replace')[:500_000], '')
            return
        self._set_preview(
            'hex',
            ' '.join(f'{byte:02x}' for byte in payload[:4096]),
            '',
        )

    def _row_for_key(self, key: str) -> dict[str, Any]:
        row = self._model.indexOf('key', key)
        return self._model.get(row) if row >= 0 else {}

    def _preferred_converted_format(self, row: dict[str, Any]) -> str:
        formats = self._cache.get_available_export_formats_for_asset(
            str(row['assetId']),
            int(row['assetType']),
        )
        return next((value for value in formats if value.startswith('converted')), '')

    def _search_text(self, row: dict[str, Any]) -> str:
        values = {
            'id': row.get('assetId'),
            'type': row.get('typeName'),
            'name': row.get('name'),
            'creator': ' '.join(
                str(value)
                for value in (row.get('creator'), row.get('creatorId'))
                if value
            ),
            'hash': row.get('hash'),
            'cached_at': ' '.join(
                str(value)
                for value in (row.get('cachedAt'), row.get('cachedAtText'))
                if value
            ),
            'updated_at': row.get('updatedAt'),
            'created_at': row.get('createdAt'),
            'url': row.get('url'),
        }
        return ' '.join(str(values[key]) for key in self._search_columns if values.get(key))

    def _sort_rows(self, rows: list[dict[str, Any]]) -> None:
        def value(row: dict[str, Any]) -> str | int:
            if self._sort_key == 'assetId':
                asset_id = str(row.get('assetId') or '')
                return int(asset_id) if asset_id.isdecimal() else -1
            if self._sort_key == 'size':
                return int(row.get('size') or 0)
            return str(row.get(self._sort_key) or '').casefold()

        populated = [row for row in rows if value(row) not in {'', -1}]
        missing = [row for row in rows if value(row) in {'', -1}]
        populated.sort(key=value, reverse=self._sort_descending)
        rows[:] = populated + missing

    def _view_settings(self) -> dict[str, Any]:
        config = getattr(self._cache, 'config_manager', None)
        settings = getattr(config, 'settings', None)
        return settings if isinstance(settings, dict) else {}

    def _schedule_view_settings_save(self) -> None:
        config = getattr(self._cache, 'config_manager', None)
        if isinstance(getattr(config, 'settings', None), dict):
            self._settings_timer.start()

    @Slot()
    def _persist_view_settings(self) -> None:
        config = getattr(self._cache, 'config_manager', None)
        settings = getattr(config, 'settings', None)
        if config is None or not isinstance(settings, dict):
            return
        settings['qml_cache_query'] = self._query
        settings['qml_cache_type_filter'] = self._type_filter
        settings['qml_cache_sort_key'] = self._sort_key
        settings['qml_cache_sort_descending'] = self._sort_descending
        settings['scraper_search_columns'] = sorted(self._search_columns)
        visibility = settings.get('scraper_column_visibility')
        updated_visibility = dict(visibility) if isinstance(visibility, dict) else {}
        updated_visibility.update(
            {
                'hash_name': True,
                'type': 'type' in self._visible_columns,
                'size': 'size' in self._visible_columns,
                'cached_at': 'cached_at' in self._visible_columns,
            }
        )
        settings['scraper_column_visibility'] = updated_visibility
        save = getattr(config, 'save', None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                self.errorOccurred.emit(f'Could not save cache view options: {exc}')

    @Slot(str)
    def _load_texture_map(self, asset_id: str) -> None:
        self.loadAssets(asset_id)

    def _refresh_if_changed(self) -> None:
        count = len(self._cache.index.get('assets', {}))
        if count != self._source_count:
            self.refresh()

    def _materialize_audio_preview(self, payload: bytes) -> str:
        suffix = (
            '.ogg'
            if payload.startswith(b'OggS')
            else '.mp3'
            if payload.startswith((b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'))
            else '.wav'
            if payload.startswith(b'RIFF')
            else '.bin'
        )
        with NamedTemporaryFile(prefix='fleasion-preview-', suffix=suffix, delete=False) as handle:
            handle.write(payload)
            path = Path(handle.name)
        self._preview_files.append(path)
        while len(self._preview_files) > 3:
            self._preview_files.pop(0).unlink(missing_ok=True)
        return QUrl.fromLocalFile(str(path)).toString()

    def _set_preview(self, kind: str, text: str, source: str, *, force: bool = False) -> None:
        if kind != 'mesh':
            self._release_mesh_geometry()
        if kind != 'font':
            self._font_preview.clear()
        if kind != 'document':
            self._document_preview.detach()
        if kind != 'animation':
            self._animation_preview.clear()
        if kind != 'texturepack':
            self._texture_pack_preview.clear()
        if not force and (
            kind == self._preview_kind
            and text == self._preview_text
            and source == self._preview_source
        ):
            return
        self._preview_kind = kind
        self._preview_text = text
        self._preview_source = source
        self.previewChanged.emit()

    def _release_mesh_geometry(self) -> None:
        geometry = self._mesh_geometry
        self._mesh_geometry = None
        if geometry is not None:
            geometry.setParent(None)
            geometry.deleteLater()

    @Slot()
    def shutdown(self) -> None:
        self._poll_timer.stop()
        if self._settings_timer.isActive():
            self._settings_timer.stop()
            self._persist_view_settings()
        self._task.shutdown()
        self._release_mesh_geometry()
        self._font_preview.shutdown()
        self._document_preview.reset()
        self._animation_preview.shutdown()
        self._texture_pack_preview.clear()
        flush_index = getattr(self._cache, '_flush_index', None)
        if callable(flush_index):
            flush_index()
        for path in self._preview_files:
            path.unlink(missing_ok=True)
        self._preview_files.clear()

    @Slot(object)
    def _on_task_succeeded(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        action = str(payload.get('action') or self._task_action)
        self._task_action = ''
        if action == 'clear':
            deleted_value = payload.get('count')
            deleted = deleted_value if isinstance(deleted_value, int) else 0
            self._selection.clear()
            self._set_preview('none', '', '')
            self.refresh()
            self.notificationRequested.emit(
                'Cache cleared',
                f'{deleted} cached assets removed',
                'success',
            )
            return
        if action == 'export':
            count_value = payload.get('count')
            failed_value = payload.get('failed')
            count = count_value if isinstance(count_value, int) else 0
            failed = failed_value if isinstance(failed_value, int) else 0
            self.notificationRequested.emit(
                'Cache export complete',
                f'{count} assets exported to the Fleasion exports folder'
                + (f'; {failed} could not be converted' if failed else ''),
                'warning' if failed else 'success',
            )
            return
        if action == 'copy-converted':
            path_values = payload.get('paths')
            paths = (
                [Path(value) for value in path_values if isinstance(value, str)]
                if isinstance(path_values, list)
                else []
            )
            failed_value = payload.get('failed')
            failed = failed_value if isinstance(failed_value, int) else 0
            if not _copy_file_urls(paths):
                self.errorOccurred.emit('The converted files could not be placed on the clipboard.')
                return
            self.notificationRequested.emit(
                'Converted files copied',
                f'{len(paths)} converted file(s) are ready to paste'
                + (f'; {failed} could not be converted' if failed else ''),
                'warning' if failed else 'success',
            )
            return
        if action == 'load':
            count_value = payload.get('count')
            failed_value = payload.get('failed')
            count = count_value if isinstance(count_value, int) else 0
            failed = failed_value if isinstance(failed_value, int) else 0
            self.refresh()
            self._texture_pack_preview.refresh()
            self.notificationRequested.emit(
                'Asset load complete',
                f'{count} Roblox assets added to the cache'
                + (f'; {failed} could not be downloaded' if failed else ''),
                'warning' if failed else 'success',
            )

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        self._task_action = ''
        self.errorOccurred.emit(message)

    def _open_folder(self, value: object, label: str) -> None:
        if not isinstance(value, Path):
            self.errorOccurred.emit(f'The {label} folder is unavailable.')
            return
        try:
            open_folder(value)
        except Exception as exc:
            self.errorOccurred.emit(f'Could not open the {label} folder: {exc}')

    @staticmethod
    def _fetch_manual_metadata(
        asset_ids: list[str],
        cancel_event: threading.Event | None = None,
    ) -> dict[str, dict[str, Any]]:
        from ..utils.roblox_auth import get_roblosecurity

        session = _roblox_session(get_roblosecurity())
        session.headers.update(
            {
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            }
        )

        result: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(asset_ids), 50):
            if cancel_event is not None and cancel_event.is_set():
                break
            query = ','.join(asset_ids[offset : offset + 50])
            try:
                response = session.get(
                    f'https://develop.roblox.com/v1/assets?assetIds={query}',
                    timeout=10,
                    allow_redirects=False,
                )
                if cancel_event is not None and cancel_event.is_set():
                    response.close()
                    break
                response.raise_for_status()
                entries = response.json().get('data', [])
            except Exception:
                continue
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or entry.get('id') is None:
                    continue
                creator = entry.get('creator')
                creator_data = creator if isinstance(creator, dict) else {}
                result[str(entry['id'])] = {
                    'name': str(entry.get('name') or ''),
                    'type': entry.get('typeId') or entry.get('assetTypeId') or 1,
                    'creator_id': creator_data.get('targetId') or entry.get('creatorTargetId'),
                    'creator_type': creator_data.get('typeId') or entry.get('creatorType'),
                    'creator_name': str(
                        creator_data.get('name') or creator_data.get('displayName') or ''
                    ),
                    'created_at': str(entry.get('created') or ''),
                    'updated_at': str(entry.get('updated') or ''),
                }
        return result

    @staticmethod
    def _fetch_manual_asset(
        asset_id: str,
        cancel_event: threading.Event | None = None,
    ) -> bytes | None:
        from ..utils.roblox_auth import get_roblosecurity

        session = _roblox_session(get_roblosecurity())
        url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
        for _ in range(_MAX_ASSET_REDIRECTS + 1):
            if cancel_event is not None and cancel_event.is_set():
                return None
            response = session.get(url, timeout=15, allow_redirects=False)
            if cancel_event is not None and cancel_event.is_set():
                response.close()
                return None
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get('Location', '')
                next_url = urljoin(response.url or url, location)
                response.close()
                if not location or not _is_trusted_asset_url(next_url):
                    return None
                url = next_url
                continue
            return response.content if response.status_code == 200 and response.content else None
        return None

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        return Path(url.toLocalFile()) if url.isLocalFile() else Path(value).expanduser()
