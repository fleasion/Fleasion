"""Account, rejoin, username, and multi-instance workflows for QML."""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from typing import Any, Final

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

from ..utils.roblox_auth import discover_browser_roblosecurity, get_roblosecurity, set_roblosecurity
from .account_store import AccountStore, StoredAccount
from .animation_conversion import AnimationConversionApi
from .models import DictListModel
from .multi_instance import MultiInstanceController
from .reserved_rejoin import ReservedRejoinInterceptor
from .roblox_launch import AccountLauncher, RobloxAccountClient
from .subplace_blacklist import GameJoinInterceptorChain, SubplaceBlacklistApi
from .subplace_join import SubplaceJoinCoordinator
from .tasks import TaskState

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_ACCOUNT_ROLES: Final = ('username', 'userId', 'status', 'statusText')


@QmlElement
class UtilitiesApi(QObject):
    """Own the non-visual utility workflows and their persisted state."""

    accountsChanged = Signal()
    selectedAccountChanged = Signal()
    multiInstanceChanged = Signal()
    usernameStateChanged = Signal()
    rejoinStateChanged = Signal()
    _reservedStateQueued = Signal(str, str, float)
    notificationRequested = Signal(str, str, str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        config_manager: Any,
        proxy_master: Any | None = None,
        account_store: AccountStore | None = None,
        account_client: RobloxAccountClient | None = None,
        account_launcher: AccountLauncher | None = None,
        multi_instance: MultiInstanceController | None = None,
        subplace_join: SubplaceJoinCoordinator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy = proxy_master
        self._store = account_store or AccountStore()
        self._account_client = account_client or RobloxAccountClient()
        self._account_launcher = account_launcher or AccountLauncher(self._account_client)
        self._multi_instance = multi_instance or MultiInstanceController()
        self._subplace_join = subplace_join or SubplaceJoinCoordinator()
        self._accounts = self._store.load()
        self._account_status: dict[str, str] = {}
        self._model = DictListModel(_ACCOUNT_ROLES, parent=self)
        self._account_task = TaskState(self)
        self._launch_task = TaskState(self)
        self._validation_task = TaskState(self)
        self._selected_username = ''
        self._username_state = self._load_username_state()
        self._reserved_place_id = ''
        self._reserved_access_code = ''
        self._reserved_expires_at = 0.0
        self._rejoin = ReservedRejoinInterceptor(self._queue_reserved_state)
        self._subplace_blacklist = SubplaceBlacklistApi(  # pyright: ignore[reportCallIssue]
            config_manager,
            parent=self,
        )
        self._animation_conversion = AnimationConversionApi(  # pyright: ignore[reportCallIssue]
            parent=self
        )
        self._gamejoin_interceptor = GameJoinInterceptorChain(
            self._rejoin,
            self._subplace_join,
            self._subplace_blacklist,
        )
        self._reservedStateQueued.connect(
            self._apply_reserved_state,
            Qt.ConnectionType.QueuedConnection,
        )
        self._subplace_blacklist.notificationRequested.connect(self.notificationRequested)
        self._animation_conversion.notificationRequested.connect(self.notificationRequested)
        self._animation_conversion.errorOccurred.connect(self.errorOccurred)
        self._account_task.succeeded.connect(self._apply_account_operation)
        self._account_task.failed.connect(self._on_account_operation_failed)
        self._launch_task.succeeded.connect(self._on_launch_finished)
        self._launch_task.failed.connect(self._on_launch_failed)
        self._validation_task.succeeded.connect(self._apply_validation_results)
        self._validation_task.failed.connect(self._on_validation_failed)
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self.rejoinStateChanged)
        self._countdown.start()
        self._push_username_state()
        self._refresh_model()
        if self.multiInstanceEnabled:
            self._multi_instance.start()
        self._validation_task.run('Checking stored accounts…', self._validate_stored_accounts)

    @Property(QObject, constant=True)
    def accountsModel(self) -> QObject:  # noqa: N802
        return self._model

    @Property(QObject, constant=True)
    def accountTask(self) -> QObject:  # noqa: N802
        return self._account_task

    @Property(QObject, constant=True)
    def launchTask(self) -> QObject:  # noqa: N802
        return self._launch_task

    @Property(QObject, constant=True)
    def animationConverter(self) -> QObject:  # noqa: N802
        return self._animation_conversion

    @Property(QObject, constant=True)
    def subplaceBlacklist(self) -> QObject:  # noqa: N802
        return self._subplace_blacklist

    @Property(str, notify=selectedAccountChanged)
    def selectedUsername(self) -> str:  # noqa: N802
        return self._selected_username

    @Property(bool, constant=True)
    def supportsMultiInstance(self) -> bool:  # noqa: N802
        return self._multi_instance.supported

    @Property(bool, notify=multiInstanceChanged)
    def multiInstanceEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._config.multi_instance_launching) and self._multi_instance.supported

    @multiInstanceEnabled.setter  # pyright: ignore[reportRedeclaration]
    def multiInstanceEnabled(self, enabled: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        normalized = bool(enabled and self._multi_instance.supported)
        if bool(self._config.multi_instance_launching) == normalized:
            return
        self._config.multi_instance_launching = normalized
        if normalized:
            self._multi_instance.start()
        else:
            self._multi_instance.stop()
        self.multiInstanceChanged.emit()

    @Property(str, notify=rejoinStateChanged)
    def reservedPlaceId(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._reserved_place_id

    @reservedPlaceId.setter  # pyright: ignore[reportRedeclaration]
    def reservedPlaceId(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._reserved_place_id:
            return
        self._rejoin.set_credentials(value, self._reserved_access_code)

    @Property(str, notify=rejoinStateChanged)
    def reservedAccessCode(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._reserved_access_code

    @reservedAccessCode.setter  # pyright: ignore[reportRedeclaration]
    def reservedAccessCode(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._reserved_access_code:
            return
        self._rejoin.set_credentials(self._reserved_place_id, value)

    @Property(int, notify=rejoinStateChanged)
    def rejoinSecondsRemaining(self) -> int:  # noqa: N802
        return max(0, int(self._reserved_expires_at - time.time()))

    @Property(bool, notify=rejoinStateChanged)
    def rejoinAvailable(self) -> bool:  # noqa: N802
        return bool(
            self._reserved_place_id
            and self._reserved_access_code
            and self._reserved_expires_at > time.time()
        )

    @Property(bool, notify=usernameStateChanged)
    def saveUsernameSettings(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['save_settings'])

    @saveUsernameSettings.setter  # pyright: ignore[reportRedeclaration]
    def saveUsernameSettings(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('save_settings', value)

    @Property(str, notify=usernameStateChanged)
    def othersName(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return str(self._username_state['others_name'])

    @othersName.setter  # pyright: ignore[reportRedeclaration]
    def othersName(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('others_name', value)

    @Property(bool, notify=usernameStateChanged)
    def othersApplyInGame(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['others_apply_ingame'])

    @othersApplyInGame.setter  # pyright: ignore[reportRedeclaration]
    def othersApplyInGame(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('others_apply_ingame', value)

    @Property(bool, notify=usernameStateChanged)
    def othersVerified(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['others_verified'])

    @othersVerified.setter  # pyright: ignore[reportRedeclaration]
    def othersVerified(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('others_verified', value)

    @Property(str, notify=usernameStateChanged)
    def selfName(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return str(self._username_state['self_name'])

    @selfName.setter  # pyright: ignore[reportRedeclaration]
    def selfName(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('self_name', value)

    @Property(bool, notify=usernameStateChanged)
    def selfApplyInGame(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['self_apply_ingame'])

    @selfApplyInGame.setter  # pyright: ignore[reportRedeclaration]
    def selfApplyInGame(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('self_apply_ingame', value)

    @Property(bool, notify=usernameStateChanged)
    def selfVerified(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['self_verified'])

    @selfVerified.setter  # pyright: ignore[reportRedeclaration]
    def selfVerified(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('self_verified', value)

    @Property(bool, notify=usernameStateChanged)
    def selfGameCreator(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._username_state['self_game_creator'])

    @selfGameCreator.setter  # pyright: ignore[reportRedeclaration]
    def selfGameCreator(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_username_value('self_game_creator', value)

    @Slot(str, result=bool)
    def addAccount(self, cookie: str) -> bool:  # noqa: N802
        cleaned = cookie.strip()
        if not cleaned:
            self.errorOccurred.emit('Paste a .ROBLOSECURITY cookie first.')
            return False
        return self._account_task.run(
            'Validating account with Roblox…',
            lambda: {'operation': 'add', **self._account_client.validate(cleaned)},
        )

    @Slot(result=bool)
    def importBrowserAccount(self) -> bool:  # noqa: N802
        return self._account_task.run(
            'Looking for your Roblox browser sign-in…',
            self._discover_browser_account,
        )

    @Slot(int)
    def selectAccount(self, row: int) -> None:  # noqa: N802
        if not 0 <= row < len(self._accounts):
            return
        username = self._accounts[row].username
        if username == self._selected_username:
            return
        self._selected_username = username
        self._push_username_current_user(self._accounts[row])
        self.selectedAccountChanged.emit()

    @Slot(int)
    def removeAccount(self, row: int) -> None:  # noqa: N802
        if not 0 <= row < len(self._accounts):
            return
        username = self._accounts[row].username
        del self._accounts[row]
        self._store.save(self._accounts)
        self._account_status.pop(username, None)
        if username == self._selected_username:
            self._selected_username = ''
            self.selectedAccountChanged.emit()
        self._refresh_model()
        self.notificationRequested.emit('Account removed', username, 'success')

    @Slot(int, str, str, result=bool)
    def launchAccount(self, row: int, place_id: str = '', job_id: str = '') -> bool:  # noqa: N802
        if not 0 <= row < len(self._accounts):
            return False
        account = self._accounts[row]
        cookie = self._store.cookie(account)
        if not cookie:
            self._account_status[account.username] = 'expired'
            self._refresh_model()
            self.errorOccurred.emit(
                'The stored cookie could not be decrypted. Re-add this account.'
            )
            return False
        normalized_place = place_id.strip()
        if normalized_place and not normalized_place.isdigit():
            self.errorOccurred.emit('The launch place ID must be numeric.')
            return False
        self._selected_username = account.username
        self._push_username_current_user(account)
        self.selectedAccountChanged.emit()
        return self._launch_task.run(
            f'Launching Roblox as {account.username}…',
            lambda: {
                'username': account.username,
                'launched': self._account_launcher.launch(cookie, normalized_place, job_id.strip()),
            },
        )

    @Slot(int, result=bool)
    def switchToAccount(self, row: int) -> bool:  # noqa: N802
        if not 0 <= row < len(self._accounts):
            return False
        account = self._accounts[row]
        cookie = self._store.cookie(account)
        if not cookie:
            self.errorOccurred.emit(
                'The stored cookie could not be decrypted. Re-add this account.'
            )
            return False
        if not set_roblosecurity(cookie):
            self.errorOccurred.emit('Roblox cookie storage could not be updated on this platform.')
            return False
        self._selected_username = account.username
        self._push_username_current_user(account)
        self.selectedAccountChanged.emit()
        self.notificationRequested.emit('Account switched', account.username, 'success')
        return True

    @Slot(result=bool)
    def rejoinReservedServer(self) -> bool:  # noqa: N802
        if not self._rejoin.arm():
            self.errorOccurred.emit(
                'Enter a valid reserved place and access code before it expires.'
            )
            return False
        from ..utils.windows import launch_as_standard_user

        if not launch_as_standard_user(f'roblox://placeId={self._reserved_place_id}'):
            self.errorOccurred.emit('Roblox could not be opened for the reserved-server rejoin.')
            return False
        self.notificationRequested.emit(
            'Reserved rejoin armed',
            'The next Roblox join request will be redirected to the captured server.',
            'success',
        )
        return True

    def interceptor(self) -> GameJoinInterceptorChain:
        return self._gamejoin_interceptor

    @Slot()
    def shutdown(self) -> None:
        self._countdown.stop()
        self._multi_instance.stop()
        self._account_task.shutdown()
        self._launch_task.shutdown()
        self._validation_task.shutdown()
        self._animation_conversion.shutdown()
        self._subplace_blacklist.shutdown()
        self._subplace_join.cancel()

    def _discover_browser_account(self) -> dict[str, str]:
        cookie, source = discover_browser_roblosecurity(
            include_keychain=sys.platform == 'darwin',
            explicit_import=True,
        )
        if not cookie:
            raise RuntimeError('No signed-in Roblox account was found in supported browsers')
        return {'operation': 'browser', 'source': source, **self._account_client.validate(cookie)}

    def _validate_stored_accounts(self) -> dict[str, object]:
        results: dict[str, str] = {}
        active_cookie = get_roblosecurity()
        selected_username = ''
        for account in self._accounts:
            cookie = self._store.cookie(account)
            if not cookie:
                results[account.username] = 'expired'
                continue
            if active_cookie and cookie == active_cookie:
                selected_username = account.username
            try:
                self._account_client.validate(cookie)
            except ValueError:
                results[account.username] = 'expired'
            except Exception:
                results[account.username] = 'stored'
            else:
                results[account.username] = 'valid'
        return {'statuses': results, 'selectedUsername': selected_username}

    def _push_username_current_user(self, account: StoredAccount) -> None:
        spoofer = getattr(self._proxy, 'username_spoofer', None)
        if spoofer is not None:
            spoofer.set_current_user(account.user_id or None, account.username)

    @Slot(object)
    def _apply_account_operation(self, result: object) -> None:
        if not isinstance(result, Mapping):
            self._on_account_operation_failed('Roblox returned invalid account data')
            return
        username = str(result.get('username') or '').strip()
        cookie = str(result.get('cookie') or '').strip()
        if not username or not cookie:
            self._on_account_operation_failed('Roblox returned invalid account data')
            return
        account = self._store.create(username, cookie, str(result.get('userId') or ''))
        self._accounts = [item for item in self._accounts if item.username != username]
        self._accounts.append(account)
        self._store.save(self._accounts)
        self._account_status[username] = 'valid'
        self._selected_username = username
        self._push_username_current_user(account)
        self._refresh_model()
        self.selectedAccountChanged.emit()
        source = str(result.get('source') or '')
        message = f'{username} imported from {source}' if source else f'{username} added securely'
        self.notificationRequested.emit('Account ready', message, 'success')

    @Slot(str)
    def _on_account_operation_failed(self, message: str) -> None:
        self.errorOccurred.emit(f'Account validation failed: {message}')

    @Slot(object)
    def _on_launch_finished(self, result: object) -> None:
        if not isinstance(result, Mapping) or not bool(result.get('launched')):
            self.errorOccurred.emit('Roblox could not be opened for the selected account.')
            return
        self.notificationRequested.emit(
            'Roblox launched',
            f'Using {result.get("username", "the selected account")}',
            'success',
        )

    @Slot(str)
    def _on_launch_failed(self, message: str) -> None:
        self.errorOccurred.emit(f'Account launch failed: {message}')

    @Slot(object)
    def _apply_validation_results(self, result: object) -> None:
        if not isinstance(result, Mapping):
            return
        statuses = result.get('statuses', {})
        if not isinstance(statuses, Mapping):
            return
        self._account_status.update(
            {
                str(username): str(status)
                for username, status in statuses.items()
                if str(status) in {'valid', 'expired', 'stored'}
            }
        )
        selected_username = str(result.get('selectedUsername') or '')
        selected_account = next(
            (account for account in self._accounts if account.username == selected_username),
            None,
        )
        if selected_account is not None:
            self._selected_username = selected_account.username
            self._push_username_current_user(selected_account)
            self.selectedAccountChanged.emit()
        self._refresh_model()

    @Slot(str)
    def _on_validation_failed(self, _message: str) -> None:
        return

    def _load_username_state(self) -> dict[str, str | bool]:
        defaults: dict[str, str | bool] = {
            'save_settings': False,
            'others_name': '',
            'others_apply_ingame': False,
            'others_verified': False,
            'self_name': '',
            'self_apply_ingame': False,
            'self_verified': False,
            'self_game_creator': False,
        }
        saved = self._config.username_spoofer
        if isinstance(saved, Mapping) and bool(saved.get('save_settings')):
            defaults.update({key: saved.get(key, value) for key, value in defaults.items()})
        return defaults

    def _set_username_value(self, key: str, value: str | bool) -> None:
        if self._username_state.get(key) == value:
            return
        self._username_state[key] = value
        self._push_username_state()
        if self._username_state['save_settings']:
            self._config.username_spoofer = dict(self._username_state)
        elif key == 'save_settings':
            cleared = dict(self._username_state)
            cleared['save_settings'] = False
            self._config.username_spoofer = cleared
        self.usernameStateChanged.emit()

    def _push_username_state(self) -> None:
        spoofer = getattr(self._proxy, 'username_spoofer', None)
        if spoofer is not None:
            spoofer.set_runtime_state(dict(self._username_state))
        refresh = getattr(self._proxy, 'refresh_username_spoofer_interception', None)
        if callable(refresh):
            refresh()

    def _refresh_model(self) -> None:
        rows: list[dict[str, str]] = []
        for account in self._accounts:
            status = self._account_status.get(account.username, 'stored')
            rows.append(
                {
                    'username': account.username,
                    'userId': account.user_id,
                    'status': status,
                    'statusText': {
                        'valid': 'Validated',
                        'expired': 'Needs attention',
                    }.get(status, 'Stored securely'),
                }
            )
        self._model.replace_items(rows)
        self.accountsChanged.emit()

    def _queue_reserved_state(self, place_id: str, access_code: str, expires_at: float) -> None:
        self._reservedStateQueued.emit(place_id, access_code, expires_at)

    @Slot(str, str, float)
    def _apply_reserved_state(self, place_id: str, access_code: str, expires_at: float) -> None:
        self._reserved_place_id = place_id
        self._reserved_access_code = access_code
        self._reserved_expires_at = expires_at
        self.rejoinStateChanged.emit()
