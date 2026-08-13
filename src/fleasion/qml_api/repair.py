"""Structured startup-repair requests exposed to the QML shell."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

from ..utils import open_folder
from ..utils.paths import CONFIG_DIR, LOGS_DIR
from .models import DictListModel
from .tasks import TaskState

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_DIAGNOSTIC_ROLES: Final = ('label', 'value', 'copyable')
_ACTION_ROLES: Final = (
    'actionId',
    'label',
    'style',
    'requiresConfirmation',
    'confirmationTitle',
    'confirmationText',
)

KNOWN_STARTUP_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        'port_bind_failed',
        'hosts_write_exhausted',
        'linux_hosts_read_only',
        'linux_helper_unavailable',
        'macos_helper_unavailable',
        'macos_ca_patch_failed',
        'macos_ca_trust_failed',
        'macos_relay_failed',
        'roblox_ca_patch_failed',
        'tls_self_test_failed',
        'windows_upstream_firewall',
    }
)

_DEFAULT_INTERCEPT_HOSTS: Final = (
    'apis.roblox.com',
    'assetdelivery.roblox.com',
    'contentdelivery.roblox.com',
    'fts.rbxcdn.com',
    'gamejoin.roblox.com',
)


@dataclass(slots=True)
class _RepairRequest:
    code: str
    dialog_kind: str
    title: str
    summary: str
    guidance: str
    details: dict[str, object]
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    actions: list[dict[str, object]] = field(default_factory=list)
    snippet: str = ''
    supplemental_title: str = ''
    supplemental_text: str = ''


@dataclass(slots=True)
class _OperationResult:
    action_id: str
    ok: bool
    message: str
    details: dict[str, object] = field(default_factory=dict)
    retry: bool = False


def _text(value: object, fallback: str = '') -> str:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (list, tuple, set)):
        return ', '.join(_text(item) for item in value if _text(item))
    if isinstance(value, Mapping):
        return '; '.join(f'{key}: {_text(item)}' for key, item in value.items())
    return str(value)


def _diagnostic(label: str, value: object, *, copyable: bool = True) -> dict[str, object]:
    return {'label': label, 'value': _text(value), 'copyable': copyable}


def _action(
    action_id: str,
    label: str,
    *,
    style: str = 'normal',
    confirmation_title: str = '',
    confirmation_text: str = '',
) -> dict[str, object]:
    return {
        'actionId': action_id,
        'label': label,
        'style': style,
        'requiresConfirmation': bool(confirmation_title),
        'confirmationTitle': confirmation_title,
        'confirmationText': confirmation_text,
    }


def _nix_hosts_snippet(details: Mapping[str, object]) -> str:
    raw_hosts = details.get('hosts')
    hosts = (
        [str(host).strip().casefold() for host in raw_hosts]
        if isinstance(raw_hosts, (list, tuple, set))
        else list(_DEFAULT_INTERCEPT_HOSTS)
    )
    normalized = sorted({host for host in hosts if host})
    rows = '\n'.join(f'  127.0.0.1 {host}' for host in normalized)
    return f"networking.extraHosts =\n''\n{rows}\n'';"


def _owners_text(value: object) -> str:
    if not isinstance(value, list):
        return ''
    rows: list[str] = []
    for owner in value:
        if not isinstance(owner, Mapping):
            continue
        name = _text(owner.get('process_name'), 'Unknown process')
        pid = _text(owner.get('pid'))
        address = _text(owner.get('local_address'))
        identity = f'{name} (PID {pid})' if pid else name
        rows.append(f'{identity} — {address}' if address else identity)
    return '\n'.join(rows)


def _failed_paths(details: Mapping[str, object]) -> list[str]:
    paths: list[str] = []
    failed = details.get('failed')
    if isinstance(failed, list):
        for item in failed:
            if isinstance(item, Mapping):
                value = item.get('path') or item.get('file') or item.get('directory')
            else:
                value = item
            if value:
                paths.append(str(value))
    for key in ('path', 'ca_path', 'resource_directory'):
        if value := details.get(key):
            paths.append(str(value))
    return list(dict.fromkeys(paths))


def _base_actions(*, logs: bool = True) -> list[dict[str, object]]:
    actions = [_action('retry', 'Retry proxy', style='primary')]
    if logs:
        actions.append(_action('open_logs', 'Open logs'))
    return actions


def _build_request(code: str, details: Mapping[str, object]) -> _RepairRequest:
    error = _text(details.get('error') or details.get('message'))
    if code == 'port_bind_failed':
        port = _text(details.get('port'), '443')
        owners = _owners_text(details.get('owners'))
        reason = _text(details.get('bind_reason'))
        diagnostics = [_diagnostic('Port', port)]
        if owners:
            diagnostics.append(_diagnostic('Current listener', owners))
        if reason:
            diagnostics.append(_diagnostic('Bind result', reason))
        if error:
            diagnostics.append(_diagnostic('System error', error))
        return _RepairRequest(
            code,
            'port',
            f'Port {port} is unavailable',
            'Fleasion could not claim the local HTTPS endpoint used for Roblox traffic.',
            (
                'Close the listed listener or release the reserved port, then retry. '
                'If access was denied, check endpoint-security and port-exclusion policies.'
            ),
            dict(details),
            diagnostics,
            _base_actions(),
        )

    if code == 'hosts_write_exhausted':
        hosts_path = _text(details.get('hosts_path'), 'the system hosts file')
        directory = _text(details.get('hosts_directory'))
        diagnostics = [_diagnostic('Hosts file', hosts_path)]
        if error:
            diagnostics.append(_diagnostic('Write error', error))
        actions = _base_actions()
        if directory or hosts_path:
            actions.insert(1, _action('open_hosts_folder', 'Open hosts folder'))
        return _RepairRequest(
            code,
            'hosts',
            'The hosts file could not be updated',
            'Every safe hosts-file write strategy was rejected by the operating system.',
            (
                'Review file permissions and antivirus controlled-folder protection. '
                'Fleasion only needs to add its Roblox loopback entries.'
            ),
            dict(details),
            diagnostics,
            actions,
        )

    if code == 'linux_hosts_read_only':
        hosts_path = _text(details.get('hosts_path'), '/etc/hosts')
        diagnostics = [_diagnostic('Hosts file', hosts_path)]
        if error:
            diagnostics.append(_diagnostic('Write error', error))
        return _RepairRequest(
            code,
            'hosts',
            'The Linux hosts file is declarative',
            'This system exposes a read-only hosts file, which is common on NixOS.',
            (
                'Add the generated option to your Nix configuration, rebuild the system, '
                'then retry Fleasion.'
            ),
            dict(details),
            diagnostics,
            _base_actions(logs=False),
            snippet=_nix_hosts_snippet(details),
            supplemental_title='Nix configuration',
            supplemental_text='Copy this exact host mapping into your system configuration.',
        )

    if code == 'linux_helper_unavailable':
        helper_code = _text(details.get('code'))
        diagnostics: list[dict[str, object]] = []
        if helper_code:
            diagnostics.append(_diagnostic('Helper result', helper_code))
        if error:
            diagnostics.append(_diagnostic('Error', error))
        return _RepairRequest(
            code,
            'helper',
            'Linux proxy helper is unavailable',
            'Fleasion could not start its narrowly scoped Polkit helper.',
            (
                'Install or refresh the signed helper, approve the Polkit prompt, and retry. '
                'If pkexec is unavailable, install a Polkit authentication agent first.'
            ),
            dict(details),
            diagnostics,
            [
                _action(
                    'install_linux_helper',
                    'Install helper and retry',
                    style='primary',
                    confirmation_title='Install the Linux proxy helper?',
                    confirmation_text=(
                        'A Polkit administrator prompt will install Fleasion’s root-owned helper '
                        'and a narrowly scoped promptless policy for future proxy and hosts '
                        'operations.'
                    ),
                ),
                _action('retry', 'Retry only'),
                _action('open_logs', 'Open logs'),
            ],
        )

    if code in {'macos_helper_unavailable', 'macos_ca_patch_failed', 'macos_relay_failed'}:
        helper_status = details.get('helper_status')
        backend_probe = details.get('backend_probe')
        diagnostics = []
        if error:
            diagnostics.append(_diagnostic('Error', error))
        if helper_status:
            diagnostics.append(_diagnostic('Helper status', helper_status))
        if backend_probe:
            diagnostics.append(_diagnostic('Backend probe', backend_probe))
        if relay_port := details.get('relay_port'):
            diagnostics.append(_diagnostic('Relay port', relay_port))
        if backend_port := details.get('backend_port'):
            diagnostics.append(_diagnostic('Backend port', backend_port))
        is_relay = code == 'macos_relay_failed'
        is_ca = code == 'macos_ca_patch_failed'
        return _RepairRequest(
            code,
            'helper',
            (
                'macOS relay could not start'
                if is_relay
                else 'Roblox certificate patch failed'
                if is_ca
                else 'macOS proxy helper is unavailable'
            ),
            (
                'The privileged relay could not connect to Fleasion’s local backend.'
                if is_relay
                else 'The helper could not update Roblox with Fleasion’s local certificate.'
                if is_ca
                else 'The LaunchDaemon used for port 443 is missing or unhealthy.'
            ),
            (
                'Reinstall the helper with one macOS administrator approval, then retry. '
                'Fleasion continues running as your normal user.'
            ),
            dict(details),
            diagnostics,
            [
                _action(
                    'install_macos_helper',
                    'Reinstall helper and retry' if code != 'macos_helper_unavailable' else 'Install helper and retry',
                    style='primary',
                    confirmation_title='Install the macOS proxy helper?',
                    confirmation_text=(
                        'macOS will request administrator approval to install or replace the '
                        'Fleasion LaunchDaemon and its restricted relay helper.'
                    ),
                ),
                _action('retry', 'Retry only'),
                _action('open_helper_logs', 'Open helper logs'),
            ],
        )

    if code == 'macos_ca_trust_failed':
        diagnostics = []
        if error:
            diagnostics.append(_diagnostic('Trust error', error))
        if verified := details.get('verified'):
            diagnostics.append(_diagnostic('Verified installs', verified))
        return _RepairRequest(
            code,
            'certificate',
            'Fleasion’s certificate is not trusted',
            'macOS could not verify the local proxy certificate in the login keychain.',
            (
                'Open Keychain Access, find the Fleasion certificate in the login keychain, '
                'and allow it for SSL. Keep the certificate limited to this local proxy.'
            ),
            dict(details),
            diagnostics,
            _base_actions(),
        )

    if code == 'roblox_ca_patch_failed':
        paths = _failed_paths(details)
        diagnostics = []
        if paths:
            diagnostics.append(_diagnostic('Roblox resource paths', '\n'.join(paths)))
        if error:
            diagnostics.append(_diagnostic('Patch error', error))
        failed = details.get('failed')
        if failed and not error:
            diagnostics.append(_diagnostic('Failures', failed))
        actions = _base_actions()
        if paths:
            actions.insert(1, _action('open_roblox_folder', 'Open Roblox folder'))
        return _RepairRequest(
            code,
            'certificate',
            'Roblox rejected the certificate patch',
            'Fleasion found Roblox but could not update one or more certificate resources.',
            (
                'Close Roblox, make sure the installation folder is writable, and retry. '
                'On managed systems, reinstall Roblox for your user or ask an administrator '
                'to grant Modify access to the listed resource folder.'
            ),
            dict(details),
            diagnostics,
            actions,
        )

    if code == 'tls_self_test_failed':
        hosts = details.get('hosts')
        diagnostics = []
        if hosts:
            diagnostics.append(_diagnostic('Failed hosts', hosts))
        if mode := details.get('proxy_mode'):
            diagnostics.append(_diagnostic('Proxy mode', mode))
        if event_loop := details.get('event_loop'):
            diagnostics.append(_diagnostic('Event loop', event_loop))
        if error:
            diagnostics.append(_diagnostic('TLS error', error))
        return _RepairRequest(
            code,
            'tls',
            'TLS verification did not pass',
            'The proxy started, but its end-to-end Roblox HTTPS self-test failed.',
            (
                'Check the certificate guidance above, VPN or antivirus HTTPS inspection, '
                'and the Fleasion logs. Retry after the conflicting TLS layer is disabled.'
            ),
            dict(details),
            diagnostics,
            _base_actions(),
        )

    # The remaining recognized request is Windows upstream-connect repair
    host = _text(details.get('host'))
    diagnostics = []
    if host:
        diagnostics.append(_diagnostic('Upstream host', host))
    if error:
        diagnostics.append(_diagnostic('Connection error', error))
    if port := details.get('listen_port'):
        diagnostics.append(_diagnostic('Local port', port))
    return _RepairRequest(
        code,
        'firewall',
        'Windows may be blocking Fleasion',
        'The local proxy could not reach Roblox after accepting the connection.',
        (
            'Fleasion can verify its two program-specific firewall rules. The repair only '
            'allows this executable on Private and Public networks.'
        ),
        dict(details),
        diagnostics,
        [
            _action('refresh_firewall', 'Check firewall', style='primary'),
            _action('open_firewall_settings', 'Open Firewall settings'),
            _action('retry', 'Retry proxy'),
        ],
        supplemental_title='Windows Firewall status',
        supplemental_text='Not checked yet',
    )


def _is_windows_admin() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        return False


def _launch_firewall_helper() -> bool:
    if sys.platform != 'win32':
        return False
    local_appdata_arg = f'--fleasion-user-localappdata={CONFIG_DIR.parent}'
    helper_args = [local_appdata_arg, '--repair-firewall']
    project_root = Path(__file__).resolve().parents[3]
    if getattr(sys, 'frozen', False):
        executable = sys.executable
        parameters = subprocess.list2cmdline(helper_args)
    elif uv_executable := shutil.which('uv') or shutil.which('uv.exe'):
        executable = uv_executable
        parameters = subprocess.list2cmdline(
            ['--project', str(project_root), 'run', 'fleasion', *helper_args]
        )
    else:
        executable = sys.executable
        parameters = subprocess.list2cmdline([str(project_root / 'launcher.py'), *helper_args])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(  # pyright: ignore[reportAttributeAccessIssue]
            None,
            'runas',
            executable,
            parameters,
            str(project_root),
            0,
        )
    except Exception:
        return False
    return int(result) > 32


@QmlElement
class StartupRepairApi(QObject):
    """Queue known startup failures and run their narrowly scoped repairs."""

    requestChanged = Signal()
    retryRequested = Signal()
    presentationRequested = Signal()
    notificationRequested = Signal(str, str, str)
    errorOccurred = Signal(str)
    _reported = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._request: _RepairRequest | None = None
        self._pending: deque[tuple[str, dict[str, object]]] = deque(maxlen=8)
        self._diagnostics = DictListModel(_DIAGNOSTIC_ROLES, parent=self)
        self._actions = DictListModel(_ACTION_ROLES, parent=self)
        self._task = TaskState(self)
        self._task.succeeded.connect(self._task_succeeded)
        self._task.failed.connect(self._task_failed)
        self._reported.connect(self._apply_report, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def handles_error(code: str) -> bool:
        if code == 'upstream_connect_failed':
            return sys.platform == 'win32'
        return code in KNOWN_STARTUP_ERROR_CODES

    def report_error(self, code: str, details: Mapping[str, object] | None = None) -> bool:
        """Queue a known proxy failure from any runtime thread."""
        if not self.handles_error(code):
            return False
        normalized = 'windows_upstream_firewall' if code == 'upstream_connect_failed' else code
        self._reported.emit(normalized, dict(details or {}))
        return True

    @Slot(str, object)
    def _apply_report(self, code: str, payload: object) -> None:
        details = dict(payload) if isinstance(payload, Mapping) else {}
        if self._active and self._request is not None:
            if self._request.code == code:
                self._show_request(_build_request(code, details))
                return
            self._pending.append((code, details))
            return
        self._show_request(_build_request(code, details))

    def _show_request(self, request: _RepairRequest) -> None:
        self._request = request
        self._active = True
        self._diagnostics.replace_items(request.diagnostics)
        self._actions.replace_items(request.actions)
        self.requestChanged.emit()
        self.presentationRequested.emit()
        if request.dialog_kind == 'firewall':
            QTimer.singleShot(0, self.refreshFirewallStatus)

    @Property(bool, notify=requestChanged)
    def active(self) -> bool:
        return self._active

    @Property(str, notify=requestChanged)
    def code(self) -> str:
        return self._request.code if self._request else ''

    @Property(str, notify=requestChanged)
    def dialogKind(self) -> str:  # noqa: N802
        return self._request.dialog_kind if self._request else ''

    @Property(str, notify=requestChanged)
    def title(self) -> str:
        return self._request.title if self._request else ''

    @Property(str, notify=requestChanged)
    def summary(self) -> str:
        return self._request.summary if self._request else ''

    @Property(str, notify=requestChanged)
    def guidance(self) -> str:
        return self._request.guidance if self._request else ''

    @Property(str, notify=requestChanged)
    def snippet(self) -> str:
        return self._request.snippet if self._request else ''

    @Property(str, notify=requestChanged)
    def supplementalTitle(self) -> str:  # noqa: N802
        return self._request.supplemental_title if self._request else ''

    @Property(str, notify=requestChanged)
    def supplementalText(self) -> str:  # noqa: N802
        return self._request.supplemental_text if self._request else ''

    @Property(dict, notify=requestChanged)
    def requestPayload(self) -> dict[str, object]:  # noqa: N802
        if self._request is None:
            return {}
        return {'code': self._request.code, **self._request.details}

    @Property(QObject, constant=True)
    def diagnostics(self) -> QObject:
        return self._diagnostics

    @Property(QObject, constant=True)
    def actions(self) -> QObject:
        return self._actions

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Slot()
    def dismiss(self) -> None:
        if not self._active:
            return
        self._active = False
        self._request = None
        self._diagnostics.replace_items(())
        self._actions.replace_items(())
        self.requestChanged.emit()
        if self._pending:
            QTimer.singleShot(0, self._show_next)

    @Slot()
    def _show_next(self) -> None:
        if self._active or not self._pending:
            return
        code, details = self._pending.popleft()
        self._show_request(_build_request(code, details))

    @Slot()
    def retry(self) -> None:
        self._pending.clear()
        self.dismiss()
        self.retryRequested.emit()

    @Slot(str)
    def performAction(self, action_id: str) -> None:  # noqa: N802
        if action_id == 'retry':
            self.retry()
        elif action_id == 'open_logs':
            open_folder(LOGS_DIR)
        elif action_id == 'open_hosts_folder':
            self._open_hosts_folder()
        elif action_id == 'open_roblox_folder':
            self._open_roblox_folder()
        elif action_id == 'open_helper_logs':
            self._open_helper_logs()
        elif action_id == 'open_firewall_settings':
            self._open_firewall_settings()
        elif action_id == 'refresh_firewall':
            self.refreshFirewallStatus()
        elif action_id == 'install_linux_helper':
            self._install_linux_helper()
        elif action_id == 'install_macos_helper':
            self._install_macos_helper()
        elif action_id == 'repair_firewall':
            self._repair_firewall()

    def _open_hosts_folder(self) -> None:
        details = self._request.details if self._request else {}
        value = details.get('hosts_directory') or details.get('hosts_path') or '/etc'
        path = Path(str(value)).expanduser()
        open_folder(path if path.is_dir() else path.parent)

    def _open_roblox_folder(self) -> None:
        details = self._request.details if self._request else {}
        paths = _failed_paths(details)
        if not paths:
            self.errorOccurred.emit('No Roblox installation path was reported.')
            return
        path = Path(paths[0]).expanduser()
        open_folder(path if path.is_dir() else path.parent)

    def _open_helper_logs(self) -> None:
        if sys.platform == 'darwin':
            from ..utils.macos_proxy_helper import HELPER_LOG_DIR

            open_folder(HELPER_LOG_DIR)
            return
        open_folder(LOGS_DIR)

    def _open_firewall_settings(self) -> None:
        if sys.platform != 'win32':
            self.errorOccurred.emit('Windows Firewall settings are only available on Windows.')
            return
        try:
            subprocess.Popen(
                [
                    'control.exe',
                    '/name',
                    'Microsoft.WindowsFirewall',
                    '/page',
                    'pageConfigureApps',
                ],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as exc:
            self.errorOccurred.emit(f'Windows Firewall settings could not open: {exc}')

    @Slot()
    def refreshFirewallStatus(self) -> None:  # noqa: N802
        if sys.platform != 'win32':
            return

        def check() -> _OperationResult:
            from ..utils.windows_firewall import get_fleasion_firewall_rule_status

            details = get_fleasion_firewall_rule_status()
            return _OperationResult(
                'refresh_firewall',
                bool(details.get('ok')),
                'Firewall rules are installed' if details.get('ok') else 'Firewall rules are missing',
                details,
            )

        self._task.run('Checking Windows Firewall…', check)

    def _install_linux_helper(self) -> None:
        def install() -> _OperationResult:
            from ..utils.linux_proxy_helper import install_privileged_helper

            details = install_privileged_helper(enable_promptless=True)
            ok = bool(details.get('ok'))
            return _OperationResult(
                'install_linux_helper',
                ok,
                'Linux proxy helper installed' if ok else _text(details.get('error'), 'Helper installation failed'),
                details,
                retry=ok,
            )

        self._task.run('Installing Linux proxy helper…', install)

    def _install_macos_helper(self) -> None:
        def install() -> _OperationResult:
            from ..utils.macos_proxy_helper import install_helper

            ok, message = install_helper()
            return _OperationResult(
                'install_macos_helper',
                ok,
                message or ('macOS proxy helper installed' if ok else 'Helper installation failed'),
                retry=ok,
            )

        self._task.run('Installing macOS proxy helper…', install)

    def _repair_firewall(self) -> None:
        def repair(cancel_event: threading.Event) -> _OperationResult:
            from ..utils.windows_firewall import (
                clear_pending_repair,
                clear_repair_result,
                install_fleasion_firewall_rules,
                read_repair_result,
                write_pending_repair,
            )

            clear_repair_result(CONFIG_DIR)
            clear_pending_repair(CONFIG_DIR)
            if _is_windows_admin():
                details = install_fleasion_firewall_rules()
            else:
                write_pending_repair(CONFIG_DIR)
                if not _launch_firewall_helper():
                    clear_pending_repair(CONFIG_DIR)
                    return _OperationResult(
                        'repair_firewall',
                        False,
                        'Administrator approval was canceled or the repair could not start.',
                    )
                deadline = time.monotonic() + 120.0
                details = None
                while (
                    details is None
                    and time.monotonic() < deadline
                    and not cancel_event.wait(0.25)
                ):
                    details = read_repair_result(CONFIG_DIR)
                if cancel_event.is_set():
                    clear_pending_repair(CONFIG_DIR)
                    clear_repair_result(CONFIG_DIR)
                    return _OperationResult(
                        'repair_firewall',
                        False,
                        'Firewall repair canceled during shutdown.',
                    )
                if details is None:
                    clear_pending_repair(CONFIG_DIR)
                    clear_repair_result(CONFIG_DIR)
                    return _OperationResult(
                        'repair_firewall',
                        False,
                        'Timed out waiting for the elevated firewall repair.',
                    )
            clear_pending_repair(CONFIG_DIR)
            clear_repair_result(CONFIG_DIR)
            ok = bool(details.get('ok'))
            message = (
                'Fleasion’s Windows Firewall rules were updated.'
                if ok
                else _text(details.get('error') or details.get('failed'), 'Firewall repair failed')
            )
            return _OperationResult('repair_firewall', ok, message, details, retry=ok)

        self._task.run_cancellable('Updating Windows Firewall…', repair)

    @Slot(object)
    def _task_succeeded(self, value: object) -> None:
        if not isinstance(value, _OperationResult):
            return
        if value.action_id == 'refresh_firewall':
            self._apply_firewall_status(value)
            return
        if not value.ok:
            self.errorOccurred.emit(value.message)
            return
        self.notificationRequested.emit('Startup repair', value.message, 'success')
        if value.retry:
            self.retry()

    def _apply_firewall_status(self, result: _OperationResult) -> None:
        if self._request is None or self._request.dialog_kind != 'firewall':
            return
        error = _text(result.details.get('error'))
        missing = result.details.get('missing')
        rules = result.details.get('rules')
        if result.ok:
            status = 'Both Fleasion program rules are installed.'
            actions = [
                _action('open_firewall_settings', 'Open Firewall settings'),
                _action('retry', 'Retry proxy', style='primary'),
            ]
        else:
            status = (
                f'Missing: {_text(missing)}'
                if missing
                else f'Could not inspect rules: {error}'
                if error
                else 'One or more Fleasion rules are missing.'
            )
            actions = [
                _action(
                    'repair_firewall',
                    'Repair firewall rules',
                    style='primary',
                    confirmation_title='Update Windows Firewall?',
                    confirmation_text=(
                        'Windows will request administrator approval to add or update only '
                        'Fleasion’s inbound and outbound program rules for Private and Public networks.'
                    ),
                ),
                _action('refresh_firewall', 'Check again'),
                _action('open_firewall_settings', 'Open Firewall settings'),
                _action('retry', 'Retry proxy'),
            ]
        if rules:
            self._request.diagnostics = [
                item for item in self._request.diagnostics if item.get('label') != 'Installed rules'
            ]
            self._request.diagnostics.append(_diagnostic('Installed rules', rules))
        self._diagnostics.replace_items(self._request.diagnostics)
        self._request.supplemental_text = status
        self._request.actions = actions
        self._actions.replace_items(actions)
        self.requestChanged.emit()

    @Slot(str)
    def _task_failed(self, message: str) -> None:
        self.errorOccurred.emit(message)

    @Slot()
    def shutdown(self) -> None:
        self._task.shutdown(wait=True)


__all__ = ['KNOWN_STARTUP_ERROR_CODES', 'StartupRepairApi']
