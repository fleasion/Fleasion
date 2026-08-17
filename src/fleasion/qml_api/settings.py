"""Typed application settings exposed to the QML interface."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Final

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement

from ..config.manager import ConfigManager
from .tasks import TaskState

if TYPE_CHECKING:
    from collections.abc import Callable

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_BOOLEAN_SETTINGS: Final = (
    'always_on_top',
    'open_dashboard_on_launch',
    'auto_delete_cache_on_exit',
    'clear_cache_on_launch',
    'proxy_features_enabled',
    'lock_roblox_files_read_only',
    'close_env_proxy_roblox_on_exit',
    'run_on_boot',
    'desktop_integration',
    'close_to_tray',
    'close_viewer_on_replace',
    'show_replacer_notifications',
    'wire_preserving_passthrough',
)
_EXPORT_NAMING_OPTIONS: Final = ('name', 'id', 'hash')


@QmlElement
class SettingsApi(QObject):
    """Expose explicit settings keys and request side effects from the runtime."""

    changed = Signal(str)
    valuesChanged = Signal()
    themeChanged = Signal()
    appearanceChanged = Signal()
    proxyModeChanged = Signal()
    proxyFeaturesChanged = Signal()
    alwaysOnTopChanged = Signal()
    restartRequired = Signal(str)
    proxyRestartRequested = Signal()
    errorOccurred = Signal(str)
    authStatusChanged = Signal()

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager or ConfigManager()
        self._auth_task = TaskState(self)
        self._auth_status = ''
        self._pending_auth_cookie = ''
        self._auth_task.succeeded.connect(self._apply_manual_auth_result)
        self._auth_task.failed.connect(self._on_manual_auth_failed)

    @Property(str, notify=themeChanged)
    def theme(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._config.theme

    @theme.setter  # pyright: ignore[reportRedeclaration]
    def theme(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        if value not in {'System', 'Light', 'Dark'} or value == self._config.theme:
            return
        self._config.theme = value
        self.themeChanged.emit()
        self.valuesChanged.emit()
        self.changed.emit('theme')

    @Property(str, notify=appearanceChanged)
    def accentColor(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.accent_color

    @accentColor.setter  # pyright: ignore[reportRedeclaration]
    def accentColor(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        normalized = value.strip().lower()
        if normalized == self._config.accent_color:
            return
        self._config.accent_color = normalized
        if normalized != self._config.accent_color:
            self.errorOccurred.emit('Choose a six-digit hexadecimal accent color.')
            return
        self.appearanceChanged.emit()
        self.changed.emit('accent_color')

    @Property(bool, notify=appearanceChanged)
    def highContrast(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.high_contrast

    @highContrast.setter  # pyright: ignore[reportRedeclaration]
    def highContrast(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._config.high_contrast:
            return
        self._config.high_contrast = value
        self.appearanceChanged.emit()
        self.changed.emit('high_contrast')

    @Property(bool, notify=appearanceChanged)
    def reducedMotion(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.reduced_motion

    @reducedMotion.setter  # pyright: ignore[reportRedeclaration]
    def reducedMotion(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._config.reduced_motion:
            return
        self._config.reduced_motion = value
        self.appearanceChanged.emit()
        self.changed.emit('reduced_motion')

    @Property(str, notify=proxyModeChanged)
    def proxyMode(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.proxy_mode

    @proxyMode.setter  # pyright: ignore[reportRedeclaration]
    def proxyMode(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value not in {'env', 'hosts'} or value == self._config.proxy_mode:
            return
        self._config.proxy_mode = value
        self.proxyModeChanged.emit()
        self.valuesChanged.emit()
        self.changed.emit('proxy_mode')
        self.restartRequired.emit('The proxy mode changed.')

    @Property(bool, notify=proxyFeaturesChanged)
    def proxyFeaturesEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.proxy_features_enabled

    @proxyFeaturesEnabled.setter  # pyright: ignore[reportRedeclaration]
    def proxyFeaturesEnabled(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._config.proxy_features_enabled:
            return
        self._config.proxy_features_enabled = value
        self.proxyFeaturesChanged.emit()
        self.valuesChanged.emit()
        self.changed.emit('proxy_features_enabled')

    @Property(bool, notify=alwaysOnTopChanged)
    def alwaysOnTop(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.always_on_top

    @alwaysOnTop.setter  # pyright: ignore[reportRedeclaration]
    def alwaysOnTop(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value == self._config.always_on_top:
            return
        self._config.always_on_top = value
        self.alwaysOnTopChanged.emit()
        self.valuesChanged.emit()
        self.changed.emit('always_on_top')

    @Property(bool, notify=valuesChanged)
    def exportNameEnabled(self) -> bool:  # noqa: N802
        return 'name' in self._config.export_naming

    @Property(bool, notify=valuesChanged)
    def closeViewerOnReplace(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.close_viewer_on_replace

    @closeViewerOnReplace.setter  # pyright: ignore[reportRedeclaration]
    def closeViewerOnReplace(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('close_viewer_on_replace', value)

    @Property(bool, notify=valuesChanged)
    def exportIdEnabled(self) -> bool:  # noqa: N802
        return 'id' in self._config.export_naming

    @Property(bool, notify=valuesChanged)
    def exportHashEnabled(self) -> bool:  # noqa: N802
        return 'hash' in self._config.export_naming

    @Property(str, constant=True)
    def platformName(self) -> str:  # noqa: N802
        if sys.platform == 'win32':
            return 'Windows'
        if sys.platform == 'darwin':
            return 'macOS'
        return 'Linux'

    @Property(bool, constant=True)
    def supportsMultiInstance(self) -> bool:  # noqa: N802
        return sys.platform == 'win32'

    @Property(bool, constant=True)
    def supportsBrowserAuthSource(self) -> bool:  # noqa: N802
        return sys.platform == 'darwin'

    @Property(QObject, constant=True)
    def authTask(self) -> QObject:  # noqa: N802
        return self._auth_task

    @Property(str, notify=authStatusChanged)
    def authStatus(self) -> str:  # noqa: N802
        return self._auth_status

    @Property(str, notify=valuesChanged)
    def macosAuthSource(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.macos_auth_source

    @macosAuthSource.setter  # pyright: ignore[reportRedeclaration]
    def macosAuthSource(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if sys.platform != 'darwin' or value == self._config.macos_auth_source:
            return
        self._config.macos_auth_source = value
        from ..utils.roblox_auth import notify_auth_source_changed

        notify_auth_source_changed()
        self.valuesChanged.emit()
        self.changed.emit('macos_auth_source')

    @Property(str, notify=valuesChanged)
    def upstreamTransportMode(self) -> str:  # noqa: N802
        return self._config.upstream_transport_mode

    @Property(str, notify=valuesChanged)
    def httpProxyHost(self) -> str:  # noqa: N802
        return self._config.upstream_http_connect_host

    @Property(int, notify=valuesChanged)
    def httpProxyPort(self) -> int:  # noqa: N802
        return self._config.upstream_http_connect_port

    @Property(str, notify=valuesChanged)
    def httpProxyUsername(self) -> str:  # noqa: N802
        return self._config.upstream_http_connect_username

    @Property(bool, notify=valuesChanged)
    def httpProxyPasswordStored(self) -> bool:  # noqa: N802
        return bool(self._config.upstream_http_connect_password)

    @Property(str, notify=valuesChanged)
    def socksProxyHost(self) -> str:  # noqa: N802
        return self._config.upstream_socks5_host

    @Property(int, notify=valuesChanged)
    def socksProxyPort(self) -> int:  # noqa: N802
        return self._config.upstream_socks5_port

    @Property(str, notify=valuesChanged)
    def socksProxyUsername(self) -> str:  # noqa: N802
        return self._config.upstream_socks5_username

    @Property(bool, notify=valuesChanged)
    def socksProxyPasswordStored(self) -> bool:  # noqa: N802
        return bool(self._config.upstream_socks5_password)

    @Property(int, notify=valuesChanged)
    def assetConnectionLimit(self) -> int:  # noqa: N802
        return self._config.vpn_compat_max_assetdelivery_connections

    @Property(int, notify=valuesChanged)
    def cdnConnectionLimit(self) -> int:  # noqa: N802
        return self._config.vpn_compat_max_cdn_connections

    @Property(bool, notify=valuesChanged)
    def openDashboardOnLaunch(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.open_dashboard_on_launch

    @openDashboardOnLaunch.setter  # pyright: ignore[reportRedeclaration]
    def openDashboardOnLaunch(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('open_dashboard_on_launch', value)

    @Property(bool, notify=valuesChanged)
    def autoDeleteCacheOnExit(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.auto_delete_cache_on_exit

    @autoDeleteCacheOnExit.setter  # pyright: ignore[reportRedeclaration]
    def autoDeleteCacheOnExit(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('auto_delete_cache_on_exit', value)

    @Property(bool, notify=valuesChanged)
    def clearCacheOnLaunch(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.clear_cache_on_launch

    @clearCacheOnLaunch.setter  # pyright: ignore[reportRedeclaration]
    def clearCacheOnLaunch(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('clear_cache_on_launch', value)

    @Property(bool, notify=valuesChanged)
    def closeToTray(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.close_to_tray

    @closeToTray.setter  # pyright: ignore[reportRedeclaration]
    def closeToTray(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('close_to_tray', value)

    @Property(bool, notify=valuesChanged)
    def runOnBoot(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.run_on_boot

    @runOnBoot.setter  # pyright: ignore[reportRedeclaration]
    def runOnBoot(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('run_on_boot', value)

    @Property(bool, notify=valuesChanged)
    def desktopIntegration(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.desktop_integration

    @desktopIntegration.setter  # pyright: ignore[reportRedeclaration]
    def desktopIntegration(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('desktop_integration', value)

    @Property(bool, notify=valuesChanged)
    def closeEnvProxyRobloxOnExit(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.close_env_proxy_roblox_on_exit

    @closeEnvProxyRobloxOnExit.setter  # pyright: ignore[reportRedeclaration]
    def closeEnvProxyRobloxOnExit(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('close_env_proxy_roblox_on_exit', value)

    @Property(bool, notify=valuesChanged)
    def readOnlyGuard(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._config.lock_roblox_files_read_only

    @readOnlyGuard.setter  # pyright: ignore[reportRedeclaration]
    def readOnlyGuard(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_boolean('lock_roblox_files_read_only', value)

    @Slot(str, result=object)
    def value(self, key: str) -> Any:
        if key not in self._config.settings:
            return None
        return self._config.settings[key]

    @Slot(str, bool)
    def setBool(self, key: str, value: bool) -> None:  # noqa: N802
        if key not in _BOOLEAN_SETTINGS:
            self.errorOccurred.emit(f'Unsupported setting: {key}')
            return
        current = bool(self._config.settings.get(key, False))
        if current == value:
            return
        setattr(self._config, key, value)
        self.valuesChanged.emit()
        self.changed.emit(key)
        if key == 'wire_preserving_passthrough':
            self.proxyRestartRequested.emit()

    @Slot(str, bool, result=bool)
    def setExportNamingEnabled(self, option: str, enabled: bool) -> bool:  # noqa: N802
        if option not in _EXPORT_NAMING_OPTIONS:
            self.errorOccurred.emit(f'Unsupported export naming option: {option}')
            return False
        selected = set(self._config.export_naming)
        if enabled:
            selected.add(option)
        else:
            selected.discard(option)
        normalized = [value for value in _EXPORT_NAMING_OPTIONS if value in selected]
        if normalized == self._config.export_naming:
            return True
        self._config.export_naming = normalized
        self.valuesChanged.emit()
        self.changed.emit('export_naming')
        return True

    @Slot(str, str)
    def setText(self, key: str, value: str) -> None:  # noqa: N802
        setters: dict[str, Callable[[str], None]] = {
            'upstream_transport_mode': lambda text: setattr(
                self._config, 'upstream_transport_mode', text
            ),
            'macos_auth_source': lambda text: setattr(self._config, 'macos_auth_source', text),
        }
        setter = setters.get(key)
        if setter is None:
            self.errorOccurred.emit(f'Unsupported setting: {key}')
            return
        setter(value)
        self.valuesChanged.emit()
        self.changed.emit(key)

    @Slot(str, str, int, str, str, str, int, str, str, int, int, result=bool)
    def configureUpstream(  # noqa: N802
        self,
        mode: str,
        http_host: str,
        http_port: int,
        http_username: str,
        http_password: str,
        socks_host: str,
        socks_port: int,
        socks_username: str,
        socks_password: str,
        asset_limit: int,
        cdn_limit: int,
    ) -> bool:
        if mode not in {'auto', 'direct_ip', 'system_proxy', 'http_connect', 'socks5'}:
            self.errorOccurred.emit('Choose a supported upstream transport.')
            return False
        if mode == 'http_connect' and (not http_host.strip() or not 1 <= http_port <= 65535):
            self.errorOccurred.emit('HTTP CONNECT requires a host and port.')
            return False
        if mode == 'socks5' and (not socks_host.strip() or not 1 <= socks_port <= 65535):
            self.errorOccurred.emit('SOCKS5 requires a host and port.')
            return False
        self._config.upstream_transport_mode = mode
        self._config.upstream_http_connect_host = http_host
        self._config.upstream_http_connect_port = http_port
        self._config.upstream_http_connect_username = http_username
        if http_password:
            self._config.upstream_http_connect_password = http_password
        self._config.upstream_socks5_host = socks_host
        self._config.upstream_socks5_port = socks_port
        self._config.upstream_socks5_username = socks_username
        if socks_password:
            self._config.upstream_socks5_password = socks_password
        self._config.vpn_compat_max_assetdelivery_connections = asset_limit
        self._config.vpn_compat_max_cdn_connections = cdn_limit
        self.valuesChanged.emit()
        self.changed.emit('upstream_transport')
        self.proxyRestartRequested.emit()
        return True

    @Slot(str)
    def clearUpstreamPassword(self, kind: str) -> None:  # noqa: N802
        if kind == 'http':
            self._config.upstream_http_connect_password = ''
        elif kind == 'socks5':
            self._config.upstream_socks5_password = ''
        else:
            return
        self.valuesChanged.emit()
        self.changed.emit('upstream_transport')
        self.proxyRestartRequested.emit()

    @Slot(str, result=bool)
    def importManualToken(self, cookie: str) -> bool:  # noqa: N802
        token = cookie.strip()
        if sys.platform != 'darwin' or not token or self._auth_task.busy:
            return False
        from ..utils.roblox_auth import validate_roblosecurity_for_import

        self._pending_auth_cookie = token
        self._set_auth_status('Validating Roblox login…')
        return self._auth_task.run(
            'Validating Roblox login…',
            lambda: validate_roblosecurity_for_import(token),
        )

    @Slot(object)
    def _apply_manual_auth_result(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 2:
            self._on_manual_auth_failed('The login validation returned an invalid response.')
            return
        valid, detail = result
        if not valid:
            self._pending_auth_cookie = ''
            message = str(detail or 'The Roblox login could not be validated.')
            self._set_auth_status(message)
            self.errorOccurred.emit(message)
            return
        from ..utils.roblox_auth import notify_auth_source_changed, store_manual_roblosecurity

        if not store_manual_roblosecurity(self._pending_auth_cookie):
            self._on_manual_auth_failed('The encrypted Roblox login could not be stored.')
            return
        self._pending_auth_cookie = ''
        self._config.macos_auth_source = 'manual'
        notify_auth_source_changed()
        self._set_auth_status('Roblox login stored encrypted.')
        self.valuesChanged.emit()
        self.changed.emit('macos_auth_source')

    @Slot(str)
    def _on_manual_auth_failed(self, message: str) -> None:
        self._pending_auth_cookie = ''
        self._set_auth_status(message)
        self.errorOccurred.emit(message)

    def _set_auth_status(self, message: str) -> None:
        if message == self._auth_status:
            return
        self._auth_status = message
        self.authStatusChanged.emit()

    @Slot()
    def refresh(self) -> None:
        self.themeChanged.emit()
        self.appearanceChanged.emit()
        self.proxyModeChanged.emit()
        self.proxyFeaturesChanged.emit()
        self.alwaysOnTopChanged.emit()
        self.valuesChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        self._auth_task.shutdown()

    def _set_boolean(self, key: str, value: bool) -> None:
        if bool(self._config.settings.get(key, False)) == value:
            return
        setattr(self._config, key, value)
        self.valuesChanged.emit()
        self.changed.emit(key)
