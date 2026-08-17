"""Subplace discovery, history, and launch bridge for QML."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlencode, urlparse

import requests
from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QmlElement

from ..utils.logging import log_buffer
from ..utils.paths import CONFIG_DIR
from ..utils.roblox_auth import get_roblosecurity
from .models import DictListModel
from .roblox_target_launcher import launch_roblox_target
from .subplace_join import SubplaceJoinCoordinator
from .tasks import TaskState

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_PLACE_ROLES: Final = (
    'placeId',
    'universeId',
    'rootPlaceId',
    'name',
    'created',
    'updated',
    'thumbnailUrl',
    'isRoot',
    'isFavorite',
    'searchText',
)
_HISTORY_ROLES: Final = ('placeId', 'name')
_SERVER_ROLES: Final = (
    'jobId',
    'playing',
    'maxPlayers',
    'occupancyText',
    'availableSlots',
    'ping',
    'pingText',
    'fps',
    'fpsText',
    'isFull',
)
_SERVER_SORT_MODES: Final = frozenset(
    {'playersAscending', 'playersDescending', 'pingAscending', 'pingDescending'}
)
_SERVER_PAGE_LIMIT: Final = 25


class JsonResponse(Protocol):
    """Small response surface used by the public Roblox API client."""

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


JsonFetcher = Callable[[str], JsonResponse]
PlaceLauncher = Callable[[str], bool]


def extract_place_id(value: str) -> str:
    """Extract a numeric place ID from an ID or Roblox experience URL."""
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    try:
        parsed = urlparse(normalized)
        parts = [part for part in parsed.path.split('/') if part]
        index = parts.index('games')
        candidate = parts[index + 1]
    except ValueError, IndexError:
        return ''
    return candidate if candidate.isdigit() else ''


def build_place_launch_uri(place_id: str, job_id: str = '') -> str:
    """Build the public Roblox deep link for a place and optional server."""
    params = {'placeId': place_id}
    if job_id.strip():
        params['gameInstanceId'] = job_id.strip()
    return f'roblox://experiences/start?{urlencode(params)}'


class SubplaceSettingsStore:
    """Persist subplace history while preserving the legacy JSON schema."""

    def __init__(
        self,
        primary_path: Path | None = None,
        legacy_path: Path | None = None,
    ) -> None:
        self.primary_path = primary_path or CONFIG_DIR / 'subplace_joiner_settings.json'
        self.legacy_path = legacy_path or CONFIG_DIR / 'subplace' / 'settings.json'

    def load(self) -> tuple[list[str], list[str], dict[str, str]]:
        loaded_from: Path | None = None
        data: Mapping[str, Any] = {}
        for path in (self.primary_path, self.legacy_path):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                log_buffer.log('subplace', f'Could not read {path.name}: {exc}')
                continue
            if isinstance(payload, Mapping):
                data = payload
                loaded_from = path
                break

        recent = _normalized_ids(data.get('recent_ids', []))
        favorites = _normalized_ids(data.get('favorites', []))
        raw_names = data.get('custom_names', {})
        names = (
            {
                str(key): str(value).strip()
                for key, value in raw_names.items()
                if str(key).isdigit() and str(value).strip()
            }
            if isinstance(raw_names, Mapping)
            else {}
        )
        if loaded_from == self.legacy_path:
            self.save(recent, favorites, names)
        return recent, favorites, names

    def save(
        self,
        recent_ids: list[str],
        favorites: list[str],
        custom_names: Mapping[str, str],
    ) -> None:
        self.primary_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'recent_ids': recent_ids,
            'favorites': favorites,
            'custom_names': dict(custom_names),
        }
        self.primary_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _normalized_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized.isdigit() and normalized not in result:
            result.append(normalized)
    return result


def _nonnegative_int(value: object, default: int = 0) -> int:
    if not isinstance(value, (str, int, float)):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _optional_number(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class RobloxPlacesClient:
    """Query Roblox's public experience and thumbnail APIs."""

    def __init__(self, fetcher: JsonFetcher | None = None) -> None:
        self._session: requests.Session | None = None
        if fetcher is None:
            session = requests.Session()
            session.trust_env = False
            session.proxies = {}
            session.headers['User-Agent'] = 'Fleasion/2 Subplace Explorer'
            self._session = session
            self._fetcher = self._fetch
        else:
            self._fetcher = fetcher

    def _fetch(self, url: str) -> JsonResponse:
        if self._session is None:
            raise RuntimeError('The Roblox API session is unavailable')
        return self._session.get(url, timeout=15)

    def discover(self, place_id: str) -> dict[str, Any]:
        universe_response = self._fetcher(
            f'https://apis.roblox.com/universes/v1/places/{place_id}/universe'
        )
        universe_response.raise_for_status()
        universe_data = universe_response.json()
        universe_id = str(universe_data.get('universeId') or '')
        if not universe_id.isdigit():
            raise ValueError('Roblox did not return a universe for that place ID')

        details_response = self._fetcher(
            f'https://games.roblox.com/v1/games?universeIds={universe_id}'
        )
        details_response.raise_for_status()
        details_payload = details_response.json()
        games = details_payload.get('data', []) if isinstance(details_payload, Mapping) else []
        root_place_id = str(games[0].get('rootPlaceId') or place_id) if games else place_id

        places: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor = ''
        while True:
            query = {'limit': '100'}
            if cursor:
                query['cursor'] = cursor
            response = self._fetcher(
                f'https://develop.roblox.com/v1/universes/{universe_id}/places?{urlencode(query)}'
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload.get('data', []) if isinstance(payload, Mapping) else []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                item_id = str(entry.get('id') or '')
                if not item_id.isdigit() or item_id in seen:
                    continue
                seen.add(item_id)
                name = str(entry.get('name') or f'Place {item_id}')
                places.append(
                    {
                        'placeId': item_id,
                        'universeId': universe_id,
                        'rootPlaceId': root_place_id,
                        'name': name,
                        'created': str(entry.get('created') or ''),
                        'updated': str(entry.get('updated') or ''),
                        'thumbnailUrl': '',
                        'isRoot': item_id == root_place_id,
                    }
                )
            cursor = (
                str(payload.get('nextPageCursor') or '') if isinstance(payload, Mapping) else ''
            )
            if not cursor:
                break

        thumbnails = self._thumbnail_urls([row['placeId'] for row in places])
        for row in places:
            row['thumbnailUrl'] = thumbnails.get(str(row['placeId']), '')
        return {
            'placeId': place_id,
            'universeId': universe_id,
            'rootPlaceId': root_place_id,
            'places': places,
        }

    def _thumbnail_urls(self, place_ids: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for start in range(0, len(place_ids), 100):
            batch = place_ids[start : start + 100]
            query = urlencode(
                {
                    'placeIds': ','.join(batch),
                    'size': '512x512',
                    'format': 'Png',
                    'isCircular': 'false',
                }
            )
            try:
                response = self._fetcher(
                    f'https://thumbnails.roblox.com/v1/places/gameicons?{query}'
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                log_buffer.log('subplace', f'Could not load place thumbnails: {exc}')
                continue
            entries = payload.get('data', []) if isinstance(payload, Mapping) else []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                target_id = str(entry.get('targetId') or '')
                image_url = str(entry.get('imageUrl') or '')
                if target_id and image_url:
                    result[target_id] = image_url
        return result

    def public_servers(
        self,
        place_id: str,
        *,
        cursor: str = '',
        sort_order: str = 'Asc',
        limit: int = _SERVER_PAGE_LIMIT,
    ) -> dict[str, Any]:
        """Return one page of public Roblox servers for a place."""
        if not place_id.isdigit():
            raise ValueError('A numeric place ID is required to list public servers')
        query = {
            'limit': str(max(10, min(limit, 100))),
            'sortOrder': 'Desc' if sort_order == 'Desc' else 'Asc',
            'excludeFullGames': 'false',
        }
        if cursor:
            query['cursor'] = cursor
        response = self._fetcher(
            f'https://games.roblox.com/v1/games/{place_id}/servers/Public?{urlencode(query)}'
        )
        if response.status_code == 429:
            raise RuntimeError(
                'Roblox is rate-limiting public server requests. Wait a moment and retry.'
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError('Roblox returned an invalid public server listing')
        servers = payload.get('data', [])
        if not isinstance(servers, list):
            raise ValueError('Roblox returned an invalid public server listing')
        return {
            'placeId': place_id,
            'servers': servers,
            'nextCursor': str(payload.get('nextPageCursor') or ''),
        }


@QmlElement
class SubplacesApi(QObject):
    """Expose filterable subplaces and real launch actions to QML."""

    queryChanged = Signal()
    sortModeChanged = Signal()
    stateChanged = Signal()
    favoriteChanged = Signal()
    serverStateChanged = Signal()
    serverSortModeChanged = Signal()
    notificationRequested = Signal(str, str, str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        client: RobloxPlacesClient | None = None,
        settings_store: SubplaceSettingsStore | None = None,
        launcher: PlaceLauncher | None = None,
        join_coordinator: SubplaceJoinCoordinator | None = None,
        proxy_master: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client or RobloxPlacesClient()
        self._settings_store = settings_store or SubplaceSettingsStore()
        self._launcher = launcher or (lambda target: launch_roblox_target(proxy_master, target))
        self._join_coordinator = join_coordinator
        self._model = DictListModel(_PLACE_ROLES, parent=self)
        self._recent_model = DictListModel(_HISTORY_ROLES, parent=self)
        self._favorites_model = DictListModel(_HISTORY_ROLES, parent=self)
        self._server_model = DictListModel(_SERVER_ROLES, parent=self)
        self._task = TaskState(self)
        self._server_task = TaskState(self)
        self._launch_task = TaskState(self)
        self._query = ''
        self._sort_mode = 'rootFirst'
        self._current_place_id = ''
        self._source_rows: list[dict[str, Any]] = []
        self._server_source_rows: list[dict[str, Any]] = []
        self._server_place_id = ''
        self._server_place_name = ''
        self._server_sort_mode = 'playersAscending'
        self._server_fetch_order = 'Asc'
        self._server_cursor = ''
        self._server_page_count = 0
        self._server_error = ''
        self._recent_ids, self._favorites, self._custom_names = self._settings_store.load()
        self._task.succeeded.connect(self._apply_search_result)
        self._task.failed.connect(self._on_search_failed)
        self._server_task.succeeded.connect(self._apply_server_result)
        self._server_task.failed.connect(self._on_server_failed)
        self._server_task.busyChanged.connect(self.serverStateChanged)
        self._launch_task.succeeded.connect(self._on_launch_succeeded)
        self._launch_task.failed.connect(self._on_launch_failed)
        self._refresh_history_models()

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(QObject, constant=True)
    def recentModel(self) -> QObject:  # noqa: N802
        return self._recent_model

    @Property(QObject, constant=True)
    def favoritesModel(self) -> QObject:  # noqa: N802
        return self._favorites_model

    @Property(QObject, constant=True)
    def serverModel(self) -> QObject:  # noqa: N802
        return self._server_model

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Property(QObject, constant=True)
    def serverTask(self) -> QObject:  # noqa: N802
        return self._server_task

    @Property(QObject, constant=True)
    def launchTask(self) -> QObject:  # noqa: N802
        return self._launch_task

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self.queryChanged.emit()
        self._apply_filter()

    @Property(str, notify=sortModeChanged)
    def sortMode(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._sort_mode

    @sortMode.setter  # pyright: ignore[reportRedeclaration]
    def sortMode(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        normalized = (
            value if value in {'rootFirst', 'name', 'idAscending', 'idDescending'} else 'rootFirst'
        )
        if normalized == self._sort_mode:
            return
        self._sort_mode = normalized
        self.sortModeChanged.emit()
        self._apply_filter()

    @Property(str, notify=stateChanged)
    def currentPlaceId(self) -> str:  # noqa: N802
        return self._current_place_id

    @Property(bool, notify=favoriteChanged)
    def currentIsFavorite(self) -> bool:  # noqa: N802
        return bool(self._current_place_id and self._current_place_id in self._favorites)

    @Property(int, notify=stateChanged)
    def resultCount(self) -> int:  # noqa: N802
        return self._model.rowCount()

    @Property(str, notify=serverStateChanged)
    def serverPlaceId(self) -> str:  # noqa: N802
        return self._server_place_id

    @Property(str, notify=serverStateChanged)
    def serverPlaceName(self) -> str:  # noqa: N802
        return self._server_place_name

    @Property(str, notify=serverSortModeChanged)
    def serverSortMode(self) -> str:  # noqa: N802
        return self._server_sort_mode

    @Property(bool, notify=serverStateChanged)
    def serverHasMore(self) -> bool:  # noqa: N802
        return bool(self._server_cursor)

    @Property(int, notify=serverStateChanged)
    def serverCount(self) -> int:  # noqa: N802
        return self._server_model.rowCount()

    @Property(int, notify=serverStateChanged)
    def serverPageCount(self) -> int:  # noqa: N802
        return self._server_page_count

    @Property(str, notify=serverStateChanged)
    def serverError(self) -> str:  # noqa: N802
        return self._server_error

    @Property(str, notify=serverStateChanged)
    def serverStatusText(self) -> str:  # noqa: N802
        if self._server_task.busy:
            return (
                f'Loading more public servers…'
                if self._server_model.rowCount()
                else 'Finding public servers…'
            )
        count = self._server_model.rowCount()
        if self._server_error:
            return self._server_error
        if count == 0:
            return 'No active public servers were reported.'
        if self._server_cursor:
            return f'{count} servers loaded across {self._server_page_count} page(s); more available.'
        return f'{count} public servers loaded across {self._server_page_count} page(s).'

    @Slot(str, result=bool)
    def search(self, value: str) -> bool:
        place_id = extract_place_id(value)
        if not place_id:
            self.errorOccurred.emit('Enter a numeric place ID or a Roblox experience URL.')
            return False
        return self._task.run(
            'Finding every place in this experience…',
            lambda: self._client.discover(place_id),
        )

    @Slot(str)
    def usePlace(self, value: str) -> None:  # noqa: N802
        place_id = extract_place_id(value)
        if place_id:
            self.search(place_id)

    @Slot(str)
    def toggleFavorite(self, value: str) -> None:  # noqa: N802
        place_id = extract_place_id(value)
        if not place_id:
            return
        if place_id in self._favorites:
            self._favorites.remove(place_id)
            message = 'Removed from favorites'
        else:
            self._favorites.insert(0, place_id)
            message = 'Added to favorites'
        self._persist()
        self._refresh_rows_favorite_state()
        self._refresh_history_models()
        self.favoriteChanged.emit()
        self.notificationRequested.emit('Favorites updated', message, 'success')

    @Slot(str, str)
    def renameSavedPlace(self, value: str, name: str) -> None:  # noqa: N802
        place_id = extract_place_id(value)
        normalized_name = name.strip()
        if not place_id or not normalized_name:
            return
        self._custom_names[place_id] = normalized_name
        self._persist()
        self._refresh_history_models()

    @Slot(str)
    def removeRecent(self, value: str) -> None:  # noqa: N802
        place_id = extract_place_id(value)
        if place_id not in self._recent_ids:
            return
        self._recent_ids.remove(place_id)
        self._persist()
        self._refresh_history_models()

    @Slot(str, str, str, result=bool)
    def launch(self, value: str, job_id: str = '', root_place_id: str = '') -> bool:
        place_id = extract_place_id(value)
        if not place_id:
            self.errorOccurred.emit('The selected place does not have a valid place ID.')
            return False
        coordinator = self._join_coordinator
        if coordinator is not None:
            normalized_root = extract_place_id(root_place_id) or root_place_id.strip()
            accepted = self._launch_task.run_cancellable(
                'Preparing the selected place…',
                lambda cancel_event: self._prepare_intercepted_launch(
                    place_id,
                    normalized_root,
                    job_id,
                    cancel_event,
                ),
            )
            if not accepted:
                self.errorOccurred.emit('Another place launch is already being prepared.')
            return accepted

        target = build_place_launch_uri(place_id, job_id)
        try:
            launched = self._launcher(target)
        except Exception as exc:
            self.errorOccurred.emit(f'Roblox could not be launched: {exc}')
            return False
        if not launched:
            if coordinator is not None:
                coordinator.cancel()
            self.errorOccurred.emit(
                'Roblox could not be launched. Check that Roblox Player is installed.'
            )
            return False
        self.notificationRequested.emit('Joining place', f'Opening place {place_id}', 'success')
        return True

    def _prepare_intercepted_launch(
        self,
        place_id: str,
        root_place_id: str,
        job_id: str,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        coordinator = self._join_coordinator
        if coordinator is None or cancel_event.is_set():
            return {'cancelled': True}
        seeded = coordinator.prepare(
            place_id,
            root_place_id,
            job_id,
            get_roblosecurity(),
        )
        if cancel_event.is_set():
            coordinator.cancel()
            return {'cancelled': True}
        if root_place_id and root_place_id != place_id and not seeded:
            log_buffer.log(
                'subplace',
                f'Continuing after root-place pre-seed failed for {root_place_id}',
            )
        try:
            launched = self._launcher(build_place_launch_uri(place_id))
        except Exception as exc:
            coordinator.cancel()
            return {'error': f'Roblox could not be launched: {exc}'}
        if not launched:
            coordinator.cancel()
            return {
                'error': 'Roblox could not be launched. Check that Roblox Player is installed.'
            }
        return {'placeId': place_id}

    @Slot(object)
    def _on_launch_succeeded(self, result: object) -> None:
        if not isinstance(result, Mapping) or result.get('cancelled'):
            return
        error = str(result.get('error') or '')
        if error:
            self.errorOccurred.emit(error)
            return
        place_id = str(result.get('placeId') or '')
        if place_id:
            self.notificationRequested.emit(
                'Joining place',
                f'Opening place {place_id}',
                'success',
            )

    @Slot(str)
    def _on_launch_failed(self, message: str) -> None:
        coordinator = self._join_coordinator
        if coordinator is not None:
            coordinator.cancel()
        self.errorOccurred.emit(f'Roblox could not be launched: {message}')

    @Slot(str, result=bool)
    def openBrowser(self, value: str) -> bool:  # noqa: N802
        place_id = extract_place_id(value)
        if not place_id:
            return False
        return QDesktopServices.openUrl(QUrl(f'https://www.roblox.com/games/{place_id}'))

    @Slot(str, str, result=bool)
    def openServers(self, value: str, place_name: str) -> bool:  # noqa: N802
        place_id = extract_place_id(value)
        if not place_id:
            self.errorOccurred.emit('A valid place ID is required to browse public servers.')
            return False
        if self._server_task.busy:
            self.errorOccurred.emit('Another public server page is still loading.')
            return False
        self._server_place_id = place_id
        self._server_place_name = place_name.strip() or f'Place {place_id}'
        self._server_source_rows = []
        self._server_model.replace_items([])
        self._server_cursor = ''
        self._server_page_count = 0
        self._server_error = ''
        self._server_fetch_order = (
            'Desc' if self._server_sort_mode == 'playersDescending' else 'Asc'
        )
        self.serverStateChanged.emit()
        return self._start_server_request(reset=True, sort_order=self._server_fetch_order)

    @Slot(result=bool)
    def refreshServers(self) -> bool:  # noqa: N802
        if not self._server_place_id:
            return False
        sort_order = (
            'Desc'
            if self._server_sort_mode == 'playersDescending'
            else 'Asc'
            if self._server_sort_mode == 'playersAscending'
            else self._server_fetch_order
        )
        return self._start_server_request(reset=True, sort_order=sort_order)

    @Slot(result=bool)
    def loadMoreServers(self) -> bool:  # noqa: N802
        if not self._server_cursor or self._server_task.busy:
            return False
        return self._start_server_request(reset=False, sort_order=self._server_fetch_order)

    @Slot(str)
    def setServerSortMode(self, value: str) -> None:  # noqa: N802
        normalized = value if value in _SERVER_SORT_MODES else 'playersAscending'
        if normalized == self._server_sort_mode:
            return
        self._server_sort_mode = normalized
        self.serverSortModeChanged.emit()
        self._apply_server_sort()
        desired_order = (
            'Desc'
            if normalized == 'playersDescending'
            else 'Asc'
            if normalized == 'playersAscending'
            else self._server_fetch_order
        )
        if (
            self._server_place_id
            and normalized.startswith('players')
            and desired_order != self._server_fetch_order
            and not self._server_task.busy
        ):
            self._start_server_request(reset=True, sort_order=desired_order)

    @Slot(str, result=bool)
    def joinServer(self, job_id: str) -> bool:  # noqa: N802
        normalized = job_id.strip()
        if not self._server_place_id or not normalized:
            self.errorOccurred.emit('The selected public server does not have a valid Job ID.')
            return False
        root_place_id = self._root_place_id_for(self._server_place_id)
        return self.launch(self._server_place_id, normalized, root_place_id)

    def _root_place_id_for(self, place_id: str) -> str:
        for row in self._source_rows:
            if str(row.get('placeId') or '') == place_id:
                return str(row.get('rootPlaceId') or '')
        return ''

    def _start_server_request(self, *, reset: bool, sort_order: str) -> bool:
        if self._server_task.busy:
            return False
        cursor = '' if reset else self._server_cursor
        self._server_error = ''
        self.serverStateChanged.emit()
        place_id = self._server_place_id
        order = 'Desc' if sort_order == 'Desc' else 'Asc'
        return self._server_task.run(
            'Finding public servers…' if reset else 'Loading the next server page…',
            lambda: {
                'page': self._client.public_servers(
                    place_id,
                    cursor=cursor,
                    sort_order=order,
                    limit=_SERVER_PAGE_LIMIT,
                ),
                'reset': reset,
                'sortOrder': order,
            },
        )

    @Slot()
    def shutdown(self) -> None:
        self._task.shutdown()
        self._server_task.shutdown()
        self._launch_task.shutdown()
        if self._join_coordinator is not None:
            self._join_coordinator.cancel()

    @Slot(object)
    def _apply_server_result(self, result: object) -> None:
        if not isinstance(result, Mapping):
            self._on_server_failed('Roblox returned an invalid public server listing.')
            return
        page = result.get('page')
        if not isinstance(page, Mapping):
            self._on_server_failed('Roblox returned an invalid public server listing.')
            return
        place_id = str(page.get('placeId') or '')
        servers = page.get('servers', [])
        if place_id != self._server_place_id or not isinstance(servers, list):
            self._on_server_failed('Roblox returned a server page for the wrong place.')
            return
        reset = bool(result.get('reset'))
        normalized_rows: list[dict[str, Any]] = []
        for entry in servers:
            if not isinstance(entry, Mapping):
                continue
            job_id = str(entry.get('id') or '').strip()
            if not job_id:
                continue
            playing = _nonnegative_int(entry.get('playing'))
            max_players = _nonnegative_int(entry.get('maxPlayers'))
            ping_value = _optional_number(entry.get('ping'))
            fps_value = _optional_number(entry.get('fps'))
            normalized_rows.append(
                {
                    'jobId': job_id,
                    'playing': playing,
                    'maxPlayers': max_players,
                    'occupancyText': f'{playing}/{max_players}',
                    'availableSlots': max(0, max_players - playing),
                    'ping': round(ping_value) if ping_value is not None else -1,
                    'pingText': f'{round(ping_value)} ms' if ping_value is not None else '—',
                    'fps': fps_value if fps_value is not None else -1.0,
                    'fpsText': f'{fps_value:.0f} FPS' if fps_value is not None else '—',
                    'isFull': bool(max_players and playing >= max_players),
                }
            )

        if reset:
            self._server_source_rows = []
        existing_ids = {str(row.get('jobId') or '') for row in self._server_source_rows}
        for row in normalized_rows:
            job_id = str(row['jobId'])
            if job_id in existing_ids:
                continue
            self._server_source_rows.append(row)
            existing_ids.add(job_id)
        self._server_cursor = str(page.get('nextCursor') or '')
        self._server_page_count = 1 if reset else self._server_page_count + 1
        self._server_fetch_order = str(result.get('sortOrder') or 'Asc')
        self._server_error = ''
        self._apply_server_sort()

    @Slot(str)
    def _on_server_failed(self, message: str) -> None:
        self._server_error = f'Could not load public servers: {message}'
        self.serverStateChanged.emit()

    def _apply_server_sort(self) -> None:
        rows = [dict(row) for row in self._server_source_rows]
        if self._server_sort_mode == 'playersDescending':
            rows.sort(
                key=lambda row: (
                    -int(row.get('playing') or 0),
                    int(row.get('ping') or -1) < 0,
                    int(row.get('ping') or -1),
                    str(row.get('jobId') or ''),
                )
            )
        elif self._server_sort_mode == 'pingAscending':
            rows.sort(
                key=lambda row: (
                    int(row.get('ping') or -1) < 0,
                    int(row.get('ping') or -1),
                    int(row.get('playing') or 0),
                )
            )
        elif self._server_sort_mode == 'pingDescending':
            rows.sort(
                key=lambda row: (
                    int(row.get('ping') or -1) < 0,
                    -int(row.get('ping') or -1),
                    int(row.get('playing') or 0),
                )
            )
        else:
            rows.sort(
                key=lambda row: (
                    int(row.get('playing') or 0),
                    int(row.get('ping') or -1) < 0,
                    int(row.get('ping') or -1),
                    str(row.get('jobId') or ''),
                )
            )
        self._server_model.replace_items(rows)
        self.serverStateChanged.emit()

    @Slot(object)
    def _apply_search_result(self, result: object) -> None:
        if not isinstance(result, Mapping):
            self._on_search_failed('Roblox returned an invalid place listing.')
            return
        places = result.get('places', [])
        if not isinstance(places, list):
            self._on_search_failed('Roblox returned an invalid place listing.')
            return
        self._current_place_id = str(result.get('placeId') or '')
        self._source_rows = []
        for place in places:
            if not isinstance(place, Mapping):
                continue
            row = dict(place)
            row['isFavorite'] = str(row.get('placeId') or '') in self._favorites
            row['searchText'] = f'{row.get("name", "")} {row.get("placeId", "")}'.casefold()
            self._source_rows.append(row)
        self._add_recent(self._current_place_id)
        self._apply_filter()
        self.stateChanged.emit()
        self.favoriteChanged.emit()
        self.notificationRequested.emit(
            'Experience loaded',
            f'Found {len(self._source_rows)} places',
            'success',
        )

    @Slot(str)
    def _on_search_failed(self, message: str) -> None:
        self.errorOccurred.emit(f'Subplace search failed: {message}')

    def _apply_filter(self) -> None:
        rows = [
            dict(row)
            for row in self._source_rows
            if not self._query or self._query in str(row.get('searchText', ''))
        ]
        if self._sort_mode == 'name':
            rows.sort(key=lambda row: str(row.get('name', '')).casefold())
        elif self._sort_mode == 'idAscending':
            rows.sort(key=lambda row: int(str(row.get('placeId') or '0')))
        elif self._sort_mode == 'idDescending':
            rows.sort(key=lambda row: int(str(row.get('placeId') or '0')), reverse=True)
        else:
            rows.sort(
                key=lambda row: (
                    not bool(row.get('isRoot')),
                    str(row.get('name', '')).casefold(),
                )
            )
        self._model.replace_items(rows)
        self.stateChanged.emit()

    def _add_recent(self, place_id: str) -> None:
        if not place_id:
            return
        if place_id in self._recent_ids:
            self._recent_ids.remove(place_id)
        self._recent_ids.insert(0, place_id)
        del self._recent_ids[20:]
        self._persist()
        self._refresh_history_models()

    def _persist(self) -> None:
        try:
            self._settings_store.save(self._recent_ids, self._favorites, self._custom_names)
        except OSError as exc:
            self.errorOccurred.emit(f'Could not save subplace history: {exc}')

    def _history_rows(self, values: list[str]) -> list[dict[str, str]]:
        known_names = {
            str(row.get('placeId')): str(row.get('name'))
            for row in self._source_rows
            if row.get('placeId') and row.get('name')
        }
        return [
            {
                'placeId': place_id,
                'name': self._custom_names.get(place_id) or known_names.get(place_id) or place_id,
            }
            for place_id in values
        ]

    def _refresh_history_models(self) -> None:
        self._recent_model.replace_items(self._history_rows(self._recent_ids))
        self._favorites_model.replace_items(self._history_rows(self._favorites))

    def _refresh_rows_favorite_state(self) -> None:
        for row in self._source_rows:
            row['isFavorite'] = str(row.get('placeId') or '') in self._favorites
        self._apply_filter()
