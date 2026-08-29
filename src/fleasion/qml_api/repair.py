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

from ..localization import tr
from ..translations.qml_sources import QML_SOURCE_IDS
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
        'hosts_entries_would_exceed_limit',
        'hosts_file_too_large',
        'hosts_file_repair_failed',
        'linux_hosts_read_only',
        'linux_helper_unavailable',
        'macos_helper_unavailable',
        'macos_ca_patch_failed',
        'macos_ca_trust_failed',
        'macos_relay_failed',
        'roblox_ca_patch_failed',
        'roblox_permission_denied',
        'tls_self_test_failed',
        'windows_upstream_firewall',
    }
)

_REPAIR_UI_IDS: Final[dict[str, str]] = {
    'Retry proxy': 'qml.dynamic.repair.action.retry_proxy',
    'Open logs': QML_SOURCE_IDS['Open logs'],
    'Port': 'qml.dynamic.repair.diagnostic.port',
    'Current listener': 'qml.dynamic.repair.diagnostic.current_listener',
    'Bind result': 'qml.dynamic.repair.diagnostic.bind_result',
    'System error': 'qml.dynamic.repair.diagnostic.system_error',
    'Hosts file': 'qml.dynamic.repair.diagnostic.hosts_file',
    'Write error': 'qml.dynamic.repair.diagnostic.write_error',
    'Projected size': 'qml.dynamic.repair.diagnostic.projected_size',
    'Safety limit': 'qml.dynamic.repair.diagnostic.safety_limit',
    'Safety check': 'qml.dynamic.repair.diagnostic.safety_check',
    'Current size': 'qml.dynamic.repair.diagnostic.current_size',
    'Repair status': 'qml.dynamic.repair.diagnostic.repair_status',
    'Helper result': 'qml.dynamic.repair.diagnostic.helper_result',
    'Error': QML_SOURCE_IDS['Error'],
    'Helper status': 'qml.dynamic.repair.diagnostic.helper_status',
    'Backend probe': 'qml.dynamic.repair.diagnostic.backend_probe',
    'Relay port': 'qml.dynamic.repair.diagnostic.relay_port',
    'Backend port': 'qml.dynamic.repair.diagnostic.backend_port',
    'Trust error': 'qml.dynamic.repair.diagnostic.trust_error',
    'Verified installs': 'qml.dynamic.repair.diagnostic.verified_installs',
    'Roblox resource paths': 'qml.dynamic.repair.diagnostic.roblox_resource_paths',
    'Patch error': 'qml.dynamic.repair.diagnostic.patch_error',
    'Failures': 'qml.dynamic.repair.diagnostic.failures',
    'Failed hosts': 'qml.dynamic.repair.diagnostic.failed_hosts',
    'Proxy mode': QML_SOURCE_IDS['Proxy mode'],
    'Event loop': 'qml.dynamic.repair.diagnostic.event_loop',
    'TLS error': 'qml.dynamic.repair.diagnostic.tls_error',
    'Upstream host': 'qml.dynamic.repair.diagnostic.upstream_host',
    'Connection error': 'qml.dynamic.repair.diagnostic.connection_error',
    'Local port': 'qml.dynamic.repair.diagnostic.local_port',
    'Installed rules': 'qml.dynamic.repair.diagnostic.installed_rules',
    'The hosts file could not be updated': 'qml.dynamic.repair.hosts_write.title',
    'Every safe hosts-file write strategy was rejected by the operating system.': 'qml.dynamic.repair.hosts_write.summary',
    'Review file permissions and antivirus controlled-folder protection. Fleasion only needs to add its Roblox loopback entries.': 'qml.dynamic.repair.hosts_write.guidance',
    'Open hosts folder': 'qml.dynamic.repair.action.open_hosts_folder',
    'The hosts file is near Fleasion’s safety limit': 'qml.dynamic.repair.hosts_limit.title',
    'Fleasion did not modify the hosts file because adding its mappings would make it too large.': 'qml.dynamic.repair.hosts_limit.summary',
    'Review the hosts file and remove entries you no longer need, then retry. Fleasion will not truncate unrelated host mappings automatically.': 'qml.dynamic.repair.hosts_limit.guidance',
    'Attempt safe repair': 'qml.dynamic.repair.action.attempt_safe_repair',
    'Repair the hosts file?': 'qml.dynamic.repair.confirm.repair_hosts_title',
    'Fleasion will create a temporary backup, stream the file safely, remove Fleasion-owned mappings and redundant blank lines, then verify the repaired file before retrying the proxy.': 'qml.dynamic.repair.confirm.repair_hosts_text',
    'The hosts file is abnormally large': 'qml.dynamic.repair.hosts_large.title',
    'Fleasion stopped before reading or extending an oversized system hosts file.': 'qml.dynamic.repair.hosts_large.summary',
    'Use the safe repair to preserve unrelated host mappings while removing Fleasion-owned entries and excess blank lines, or inspect the file manually.': 'qml.dynamic.repair.hosts_large.guidance',
    'The Linux hosts file is declarative': 'qml.dynamic.repair.linux_hosts.title',
    'This system exposes a read-only hosts file, which is common on NixOS.': 'qml.dynamic.repair.linux_hosts.summary',
    'Add the generated option to your Nix configuration, rebuild the system, then retry Fleasion.': 'qml.dynamic.repair.linux_hosts.guidance',
    'Nix configuration': 'qml.dynamic.repair.linux_hosts.supplemental_title',
    'Copy this exact host mapping into your system configuration.': 'qml.dynamic.repair.linux_hosts.supplemental_text',
    'Linux proxy helper is unavailable': 'qml.dynamic.repair.linux_helper.title',
    'Fleasion could not start its narrowly scoped Polkit helper.': 'qml.dynamic.repair.linux_helper.summary',
    'Install or refresh the signed helper, approve the Polkit prompt, and retry. If pkexec is unavailable, install a Polkit authentication agent first.': 'qml.dynamic.repair.linux_helper.guidance',
    'Install helper and retry': 'qml.dynamic.repair.action.install_helper_retry',
    'Install the Linux proxy helper?': 'qml.dynamic.repair.confirm.install_linux_helper_title',
    'A Polkit administrator prompt will install Fleasion’s root-owned helper and a narrowly scoped promptless policy for future proxy and hosts operations.': 'qml.dynamic.repair.confirm.install_linux_helper_text',
    'Retry only': 'qml.dynamic.repair.action.retry_only',
    'Reinstall the helper with one macOS administrator approval, then retry. Fleasion continues running as your normal user.': 'qml.dynamic.repair.macos_helper.guidance',
    'macOS relay could not start': 'qml.dynamic.repair.macos_relay.title',
    'The privileged relay could not connect to Fleasion’s local backend.': 'qml.dynamic.repair.macos_relay.summary',
    'Roblox certificate patch failed': 'qml.dynamic.repair.macos_ca_patch.title',
    'The helper could not update Roblox with Fleasion’s local certificate.': 'qml.dynamic.repair.macos_ca_patch.summary',
    'macOS proxy helper is unavailable': 'qml.dynamic.repair.macos_helper.title',
    'The LaunchDaemon used for port 443 is missing or unhealthy.': 'qml.dynamic.repair.macos_helper.summary',
    'Reinstall helper and retry': 'qml.dynamic.repair.action.reinstall_helper_retry',
    'Install the macOS proxy helper?': 'qml.dynamic.repair.confirm.install_macos_helper_title',
    'macOS will request administrator approval to install or replace the Fleasion LaunchDaemon and its restricted relay helper.': 'qml.dynamic.repair.confirm.install_macos_helper_text',
    'Open helper logs': 'qml.dynamic.repair.action.open_helper_logs',
    'Fleasion’s certificate is not trusted': 'qml.dynamic.repair.ca_trust.title',
    'macOS could not verify the local proxy certificate in the login keychain.': 'qml.dynamic.repair.ca_trust.summary',
    'Open Keychain Access, find the Fleasion certificate in the login keychain, and allow it for SSL. Keep the certificate limited to this local proxy.': 'qml.dynamic.repair.ca_trust.guidance',
    'Roblox rejected the certificate patch': 'qml.dynamic.repair.roblox_ca.title',
    'Fleasion found Roblox but could not update one or more certificate resources.': 'qml.dynamic.repair.roblox_ca.summary',
    'Close Roblox, make sure the installation folder is writable, and retry. On managed systems, reinstall Roblox for your user or ask an administrator to grant Modify access to the listed resource folder.': 'qml.dynamic.repair.roblox_ca.guidance',
    'Open Roblox folder': 'qml.dynamic.repair.action.open_roblox_folder',
    'TLS verification did not pass': 'qml.dynamic.repair.tls.title',
    'The proxy started, but its end-to-end Roblox HTTPS self-test failed.': 'qml.dynamic.repair.tls.summary',
    'Check the certificate guidance above, VPN or antivirus HTTPS inspection, and the Fleasion logs. Retry after the conflicting TLS layer is disabled.': 'qml.dynamic.repair.tls.guidance',
    'Windows may be blocking Fleasion': 'qml.dynamic.repair.firewall.title',
    'The local proxy could not reach Roblox after accepting the connection.': 'qml.dynamic.repair.firewall.summary',
    'Fleasion can verify its two program-specific firewall rules. The repair only allows this executable on Private and Public networks.': 'qml.dynamic.repair.firewall.guidance',
    'Check firewall': 'qml.dynamic.repair.action.check_firewall',
    'Open Firewall settings': 'qml.dynamic.repair.action.open_firewall_settings',
    'Windows Firewall status': 'qml.dynamic.repair.firewall.supplemental_title',
    'Not checked yet': 'qml.dynamic.repair.firewall.not_checked',
    'Firewall rules are installed': 'qml.dynamic.repair.firewall.rules_installed',
    'Firewall rules are missing': 'qml.dynamic.repair.firewall.rules_missing',
    'Linux proxy helper installed': 'qml.dynamic.repair.linux_helper.installed',
    'Helper installation failed': 'qml.dynamic.repair.helper.installation_failed',
    'macOS proxy helper installed': 'qml.dynamic.repair.macos_helper.installed',
    'The privileged hosts-file repair did not complete. No unsafe overwrite was accepted.': 'qml.dynamic.repair.hosts.repair_incomplete',
    'The hosts file is still above Fleasion’s safety limit after repair.': 'qml.dynamic.repair.hosts.still_oversized',
    'Fleasion-owned hosts entries are still present after repair.': 'qml.dynamic.repair.hosts.entries_remain',
    'The hosts file was repaired and verified.': 'qml.dynamic.repair.hosts.repaired_verified',
    'Administrator approval was canceled or the repair could not start.': 'qml.dynamic.repair.firewall.approval_cancelled',
    'Firewall repair canceled during shutdown.': 'qml.dynamic.repair.firewall.cancelled_shutdown',
    'Timed out waiting for the elevated firewall repair.': 'qml.dynamic.repair.firewall.timeout',
    'Fleasion’s Windows Firewall rules were updated.': 'qml.dynamic.repair.firewall.updated',
    'Firewall repair failed': 'qml.dynamic.repair.firewall.repair_failed',
    'Both Fleasion program rules are installed.': 'qml.dynamic.repair.firewall.both_rules_installed',
    'One or more Fleasion rules are missing.': 'qml.dynamic.repair.firewall.one_or_more_missing',
    'Repair firewall rules': 'qml.dynamic.repair.action.repair_firewall_rules',
    'Update Windows Firewall?': 'qml.dynamic.repair.confirm.update_firewall_title',
    'Windows will request administrator approval to add or update only Fleasion’s inbound and outbound program rules for Private and Public networks.': 'qml.dynamic.repair.confirm.update_firewall_text',
    'Check again': 'qml.dynamic.repair.action.check_again',
}


def _repair_text(value: str) -> str:
    identifier = _REPAIR_UI_IDS.get(value)
    return tr(identifier) if identifier is not None else value


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

    def __post_init__(self) -> None:
        self.title = _repair_text(self.title)
        self.summary = _repair_text(self.summary)
        self.guidance = _repair_text(self.guidance)
        self.supplemental_title = _repair_text(self.supplemental_title)
        self.supplemental_text = _repair_text(self.supplemental_text)


@dataclass(slots=True)
class _OperationResult:
    action_id: str
    ok: bool
    message: str
    details: dict[str, object] = field(default_factory=dict)
    retry: bool = False

    def __post_init__(self) -> None:
        self.message = _repair_text(self.message)


def _text(value: object, fallback: str = '') -> str:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return tr('qml.dynamic.repair.yes') if value else tr('qml.dynamic.repair.no')
    if isinstance(value, (list, tuple, set)):
        return ', '.join(_text(item) for item in value if _text(item))
    if isinstance(value, Mapping):
        return '; '.join(f'{key}: {_text(item)}' for key, item in value.items())
    return str(value)


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError, OverflowError:
        return default


def _diagnostic(label: str, value: object, *, copyable: bool = True) -> dict[str, object]:
    return {'label': _repair_text(label), 'value': _text(value), 'copyable': copyable}


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
        'label': _repair_text(label),
        'style': style,
        'requiresConfirmation': bool(confirmation_title),
        'confirmationTitle': _repair_text(confirmation_title),
        'confirmationText': _repair_text(confirmation_text),
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
        name = _text(owner.get('process_name'), tr('qml.dynamic.repair.unknown_process'))
        pid = _text(owner.get('pid'))
        address = _text(owner.get('local_address'))
        identity = f'{name} (PID {pid})' if pid else name
        rows.append(f'{identity} — {address}' if address else identity)
    return '\n'.join(rows)


def _failed_paths(details: Mapping[str, object]) -> list[str]:
    paths: list[str] = []
    raw_paths = details.get('paths')
    if isinstance(raw_paths, (list, tuple, set)):
        paths.extend(str(value) for value in raw_paths if value)
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


def _windows_ca_permission_denied_dirs(details: Mapping[str, object]) -> list[Path]:
    """Return Windows Roblox install directories whose CA patch failed due to ACLs."""
    if sys.platform != 'win32':
        return []

    denied: set[Path] = set()
    failed = details.get('failed') or []
    for item in failed if isinstance(failed, list) else []:
        if not isinstance(item, Mapping):
            continue
        error = str(item.get('error') or '').lower()
        if not any(
            marker in error
            for marker in ('permission denied', 'access is denied', 'winerror 5', 'errno 13')
        ):
            continue
        resource_dir = item.get('resource_dir')
        if resource_dir:
            denied.add(Path(str(resource_dir)))
            continue
        ca_file = item.get('ca_file')
        if ca_file:
            path = Path(str(ca_file))
            denied.add(path.parent.parent if path.parent.name.lower() == 'ssl' else path.parent)

    return sorted(denied, key=lambda path: str(path).lower())


def _permission_repair_action(paths: list[Path], *, env_proxy: bool) -> dict[str, object]:
    listed_paths = '\n'.join(tr('app.common.bullet_path', path=path) for path in paths)
    failure_text = tr(
        'app.roblox_permissions.env_proxy_failure'
        if env_proxy
        else 'app.roblox_permissions.default_failure'
    )
    return _action(
        'repair_roblox_permissions',
        tr('app.roblox_permissions.grant'),
        style='primary',
        confirmation_title=tr('app.roblox_permissions.title'),
        confirmation_text=tr(
            'app.roblox_permissions.prompt',
            failure_text=failure_text,
            listed_paths=listed_paths,
        ),
    )


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
            tr('qml.dynamic.repair.port_unavailable', port=port),
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
        hosts_path = _text(details.get('hosts_path'), tr('qml.dynamic.repair.system_hosts_file'))
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

    if code == 'hosts_entries_would_exceed_limit':
        hosts_path = _text(details.get('hosts_path'), tr('qml.dynamic.repair.system_hosts_file'))
        directory = _text(details.get('hosts_directory'))
        candidate_size = _int_value(details.get('hosts_size_bytes'))
        limit = _int_value(details.get('hosts_size_limit_bytes'), 512 * 1024)
        diagnostics = [
            _diagnostic('Hosts file', hosts_path),
            _diagnostic('Projected size', f'{candidate_size / 1024:.1f} KiB'),
            _diagnostic('Safety limit', f'{limit / 1024:.1f} KiB'),
        ]
        if error:
            diagnostics.append(_diagnostic('Safety check', error))
        actions = _base_actions()
        if directory or hosts_path:
            actions.insert(0, _action('open_hosts_folder', 'Open hosts folder'))
        return _RepairRequest(
            code,
            'hosts',
            'The hosts file is near Fleasion’s safety limit',
            'Fleasion did not modify the hosts file because adding its mappings would make it too large.',
            (
                'Review the hosts file and remove entries you no longer need, then retry. '
                'Fleasion will not truncate unrelated host mappings automatically.'
            ),
            dict(details),
            diagnostics,
            actions,
        )

    if code in {'hosts_file_too_large', 'hosts_file_repair_failed'}:
        hosts_path = _text(details.get('hosts_path'), tr('qml.dynamic.repair.system_hosts_file'))
        directory = _text(details.get('hosts_directory'))
        size = _int_value(
            details.get('hosts_size_bytes') or details.get('repair_output_size_bytes')
        )
        limit = _int_value(details.get('hosts_size_limit_bytes'), 512 * 1024)
        diagnostics = [_diagnostic('Hosts file', hosts_path)]
        if size:
            diagnostics.append(_diagnostic('Current size', f'{size / (1024 * 1024):.2f} MiB'))
        diagnostics.append(_diagnostic('Safety limit', f'{limit / 1024:.1f} KiB'))
        if error:
            diagnostics.append(_diagnostic('Repair status', error))
        actions = [
            _action(
                'repair_hosts',
                'Attempt safe repair',
                style='primary',
                confirmation_title='Repair the hosts file?',
                confirmation_text=(
                    'Fleasion will create a temporary backup, stream the file safely, remove '
                    'Fleasion-owned mappings and redundant blank lines, then verify the repaired '
                    'file before retrying the proxy.'
                ),
            )
        ]
        if directory or hosts_path:
            actions.append(_action('open_hosts_folder', 'Open hosts folder'))
        actions.append(_action('open_logs', 'Open logs'))
        return _RepairRequest(
            code,
            'hosts',
            'The hosts file is abnormally large',
            'Fleasion stopped before reading or extending an oversized system hosts file.',
            (
                'Use the safe repair to preserve unrelated host mappings while removing '
                'Fleasion-owned entries and excess blank lines, or inspect the file manually.'
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
                    'Reinstall helper and retry'
                    if code != 'macos_helper_unavailable'
                    else 'Install helper and retry',
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
        denied_dirs = _windows_ca_permission_denied_dirs(details)
        if denied_dirs:
            actions.insert(0, _permission_repair_action(denied_dirs, env_proxy=True))
        if paths:
            actions.insert(
                1 if not denied_dirs else 2, _action('open_roblox_folder', 'Open Roblox folder')
            )
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
            {**dict(details), 'permission_repair_paths': [str(path) for path in denied_dirs]},
            diagnostics,
            actions,
        )

    if code == 'roblox_permission_denied':
        paths = [Path(value) for value in _failed_paths(details)]
        diagnostics = []
        if paths:
            diagnostics.append(
                _diagnostic('Roblox resource paths', '\n'.join(str(path) for path in paths))
            )
        actions = [
            _permission_repair_action(paths, env_proxy=False),
            _action('open_logs', 'Open logs'),
        ]
        return _RepairRequest(
            code,
            'certificate',
            tr('app.roblox_permissions.title'),
            tr('app.roblox_permissions.default_failure'),
            tr(
                'app.roblox_permissions.prompt',
                failure_text=tr('app.roblox_permissions.default_failure'),
                listed_paths='\n'.join(tr('app.common.bullet_path', path=path) for path in paths),
            ),
            {**dict(details), 'permission_repair_paths': [str(path) for path in paths]},
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
    permissionRepairCompleted = Signal()
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
        elif action_id == 'repair_hosts':
            self._repair_hosts()
        elif action_id == 'repair_roblox_permissions':
            self._repair_roblox_permissions()

    def _open_hosts_folder(self) -> None:
        details = self._request.details if self._request else {}
        value = details.get('hosts_directory') or details.get('hosts_path') or '/etc'
        path = Path(str(value)).expanduser()
        open_folder(path if path.is_dir() else path.parent)

    def _open_roblox_folder(self) -> None:
        details = self._request.details if self._request else {}
        paths = _failed_paths(details)
        if not paths:
            self.errorOccurred.emit(tr('qml.dynamic.repair.no_roblox_path'))
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
            self.errorOccurred.emit(tr('qml.dynamic.repair.firewall_windows_only'))
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
            self.errorOccurred.emit(
                tr('qml.dynamic.repair.firewall_settings_open_failed', error=exc)
            )

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
                _repair_text(
                    'Firewall rules are installed'
                    if details.get('ok')
                    else 'Firewall rules are missing'
                ),
                details,
            )

        self._task.run(tr('qml.dynamic.repair.checking_firewall'), check)

    def _install_linux_helper(self) -> None:
        def install() -> _OperationResult:
            from ..utils.linux_proxy_helper import install_privileged_helper

            details = install_privileged_helper(enable_promptless=True)
            ok = bool(details.get('ok'))
            return _OperationResult(
                'install_linux_helper',
                ok,
                (
                    _repair_text('Linux proxy helper installed')
                    if ok
                    else _repair_text(_text(details.get('error'), 'Helper installation failed'))
                ),
                details,
                retry=ok,
            )

        self._task.run(tr('qml.dynamic.repair.installing_linux_helper'), install)

    def _install_macos_helper(self) -> None:
        def install() -> _OperationResult:
            from ..utils.macos_proxy_helper import install_helper

            ok, message = install_helper()
            return _OperationResult(
                'install_macos_helper',
                ok,
                message
                or _repair_text(
                    'macOS proxy helper installed' if ok else 'Helper installation failed'
                ),
                retry=ok,
            )

        self._task.run(tr('qml.dynamic.repair.installing_macos_helper'), install)

    def _repair_hosts(self) -> None:
        def repair() -> _OperationResult:
            from ..app import _run_privileged_hosts_cleanup
            from ..proxy.master import (
                INTERCEPT_HOSTS,
                has_stale_hosts_entries,
                hosts_file_is_oversized,
            )

            if not _run_privileged_hosts_cleanup(None):
                return _OperationResult(
                    'repair_hosts',
                    False,
                    _repair_text(
                        'The privileged hosts-file repair did not complete. No unsafe overwrite was accepted.'
                    ),
                )
            oversized_details: dict[str, object] = {}
            if hosts_file_is_oversized(oversized_details):
                return _OperationResult(
                    'repair_hosts',
                    False,
                    _repair_text(
                        'The hosts file is still above Fleasion’s safety limit after repair.'
                    ),
                    oversized_details,
                )
            if has_stale_hosts_entries(set(INTERCEPT_HOSTS)):
                return _OperationResult(
                    'repair_hosts',
                    False,
                    _repair_text('Fleasion-owned hosts entries are still present after repair.'),
                )
            return _OperationResult(
                'repair_hosts',
                True,
                _repair_text('The hosts file was repaired and verified.'),
                retry=True,
            )

        self._task.run(tr('qml.dynamic.repair.repairing_hosts'), repair)

    def _repair_roblox_permissions(self) -> None:
        request = self._request
        raw_paths = request.details.get('permission_repair_paths') if request else None
        paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
        if sys.platform != 'win32' or not paths:
            self.errorOccurred.emit(
                tr('app.roblox_permissions.failure_text', detail=tr('app.common.unknown_path'))
            )
            return

        def repair(cancel_event: threading.Event) -> _OperationResult:
            from ..app import _relaunch_as_admin
            from ..utils.windows_permissions import (
                clear_pending_repair,
                clear_repair_result,
                read_repair_result,
                write_pending_repair,
            )

            clear_repair_result(CONFIG_DIR)
            clear_pending_repair(CONFIG_DIR)
            if not write_pending_repair(paths, CONFIG_DIR):
                return _OperationResult(
                    'repair_roblox_permissions',
                    False,
                    tr('app.roblox_permissions.failure_text', detail=tr('app.common.unknown_path')),
                )
            if not _relaunch_as_admin(extra_args='--repair-roblox-permissions'):
                clear_pending_repair(CONFIG_DIR)
                return _OperationResult(
                    'repair_roblox_permissions',
                    False,
                    _repair_text(
                        'Administrator approval was canceled or the repair could not start.'
                    ),
                )

            deadline = time.monotonic() + 120.0
            result = None
            while result is None and time.monotonic() < deadline and not cancel_event.wait(0.25):
                result = read_repair_result(CONFIG_DIR)

            clear_pending_repair(CONFIG_DIR)
            clear_repair_result(CONFIG_DIR)
            if cancel_event.is_set():
                return _OperationResult(
                    'repair_roblox_permissions',
                    False,
                    tr('app.roblox_permissions.timeout_text'),
                )
            if result is None:
                return _OperationResult(
                    'repair_roblox_permissions',
                    False,
                    tr('app.roblox_permissions.timeout_text'),
                )
            if result.get('ok'):
                return _OperationResult(
                    'repair_roblox_permissions',
                    True,
                    tr('app.roblox_permissions.grant'),
                    dict(result),
                )
            detail = (
                result.get('error')
                or result.get('failed')
                or tr('app.roblox_permissions.acl_update_failed')
            )
            return _OperationResult(
                'repair_roblox_permissions',
                False,
                tr('app.roblox_permissions.failure_text', detail=detail),
                dict(result),
            )

        self._task.run_cancellable(tr('app.grant_access_for_this_windows_user'), repair)

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
                        _repair_text(
                            'Administrator approval was canceled or the repair could not start.'
                        ),
                    )
                deadline = time.monotonic() + 120.0
                details = None
                while (
                    details is None and time.monotonic() < deadline and not cancel_event.wait(0.25)
                ):
                    details = read_repair_result(CONFIG_DIR)
                if cancel_event.is_set():
                    clear_pending_repair(CONFIG_DIR)
                    clear_repair_result(CONFIG_DIR)
                    return _OperationResult(
                        'repair_firewall',
                        False,
                        _repair_text('Firewall repair canceled during shutdown.'),
                    )
                if details is None:
                    clear_pending_repair(CONFIG_DIR)
                    clear_repair_result(CONFIG_DIR)
                    return _OperationResult(
                        'repair_firewall',
                        False,
                        _repair_text('Timed out waiting for the elevated firewall repair.'),
                    )
            clear_pending_repair(CONFIG_DIR)
            clear_repair_result(CONFIG_DIR)
            ok = bool(details.get('ok'))
            message = (
                _repair_text('Fleasion’s Windows Firewall rules were updated.')
                if ok
                else _repair_text(
                    _text(details.get('error') or details.get('failed'), 'Firewall repair failed')
                )
            )
            return _OperationResult('repair_firewall', ok, message, details, retry=ok)

        self._task.run_cancellable(tr('qml.dynamic.repair.updating_firewall'), repair)

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
        self.notificationRequested.emit(
            tr('qml.dynamic.repair.startup_repair_title'), value.message, 'success'
        )
        if value.action_id == 'repair_roblox_permissions':
            request_code = self._request.code if self._request is not None else ''
            if request_code == 'roblox_ca_patch_failed':
                self.retry()
            else:
                self.dismiss()
                self.permissionRepairCompleted.emit()
            return
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
                tr('qml.dynamic.repair.firewall.missing', value=_text(missing))
                if missing
                else tr('qml.dynamic.repair.firewall.inspect_failed', error=error)
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
