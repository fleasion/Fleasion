"""System proxy discovery for upstream CONNECT fallback."""

from __future__ import annotations

import importlib
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlparse

from .upstream import HttpProxyConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager


class _WinregModule(Protocol):
    HKEY_CURRENT_USER: object
    OpenKey: Callable[[object, str], object]
    QueryValueEx: Callable[[object, str], tuple[object, int]]


def _winreg_module() -> _WinregModule:
    return cast('_WinregModule', importlib.import_module('winreg'))


def _run_text_command(args: list[str], *, timeout: float, creationflags: int = 0) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0]) or args[0]
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [executable, *args[1:]],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=creationflags,
        check=False,
        shell=False,
    )


@dataclass
class WindowsProxyInfo:
    wininet_enabled: bool = False
    wininet_proxy_server: str | None = None
    wininet_auto_config_url: str | None = None
    winhttp_proxy_server: str | None = None
    macos_http_enabled: bool = False
    macos_http_proxy_server: str | None = None
    macos_https_enabled: bool = False
    macos_https_proxy_server: str | None = None
    macos_auto_config_url: str | None = None


def _read_wininet_registry() -> tuple[bool, str | None, str | None]:
    winreg = _winreg_module()

    def query(key: object, name: str) -> object | None:
        try:
            value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return value

    try:
        with cast(
            'AbstractContextManager[object]',
            winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
            ),
        ) as key:
            proxy_enable = cast('int | str', query(key, 'ProxyEnable') or 0)
            enabled = bool(int(proxy_enable))
            proxy_server = query(key, 'ProxyServer')
            auto_config_url = query(key, 'AutoConfigURL')
    except (ImportError, OSError, TypeError, ValueError):
        return False, None, None
    return (
        enabled,
        str(proxy_server) if proxy_server else None,
        str(auto_config_url) if auto_config_url else None,
    )


def _read_wininet() -> tuple[bool, str | None, str | None]:
    if platform.system() != 'Windows':
        return False, None, None
    return _read_wininet_registry()


def _read_winhttp_proxy() -> str | None:
    if platform.system() != 'Windows':
        return None

    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        result = _run_text_command(
            ['netsh', 'winhttp', 'show', 'proxy'],
            timeout=5,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    text = (result.stdout or '') + '\n' + (result.stderr or '')
    if 'Direct access' in text:
        return None

    match = re.search(r'Proxy Server\(s\)\s*:\s*(.+)', text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        return value or None
    return None


def _proxy_target(host: str | None, port: object) -> str | None:
    host_text = str(host or '').strip()
    if not host_text:
        return None
    try:
        port_int = int(str(port).strip())
    except TypeError, ValueError:
        return None
    if port_int <= 0 or port_int > 65535:
        return None
    if ':' in host_text and not host_text.startswith('['):
        host_text = f'[{host_text}]'
    return f'{host_text}:{port_int}'


def _parse_scutil_proxy_output(
    text: str,
) -> tuple[bool, str | None, bool, str | None, str | None]:
    values: dict[str, str] = {}
    for line in (text or '').splitlines():
        match = re.match(r'\s*([A-Za-z0-9]+)\s*:\s*(.*?)\s*$', line)
        if match:
            values[match.group(1)] = match.group(2)

    http_enabled = values.get('HTTPEnable') == '1'
    https_enabled = values.get('HTTPSEnable') == '1'
    auto_config_url = (
        values.get('ProxyAutoConfigURLString')
        if values.get('ProxyAutoConfigEnable') == '1'
        else None
    )
    http_proxy = (
        _proxy_target(values.get('HTTPProxy'), values.get('HTTPPort')) if http_enabled else None
    )
    https_proxy = (
        _proxy_target(values.get('HTTPSProxy'), values.get('HTTPSPort')) if https_enabled else None
    )
    return http_enabled, http_proxy, https_enabled, https_proxy, auto_config_url


def _read_macos_proxies() -> tuple[bool, str | None, bool, str | None, str | None]:
    if platform.system() != 'Darwin':
        return False, None, False, None, None

    try:
        result = _run_text_command(['scutil', '--proxy'], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False, None, False, None, None

    return _parse_scutil_proxy_output((result.stdout or '') + '\n' + (result.stderr or ''))


def detect_windows_proxy() -> WindowsProxyInfo:
    if platform.system() == 'Darwin':
        http_enabled, http_proxy, https_enabled, https_proxy, auto_url = _read_macos_proxies()
        return WindowsProxyInfo(
            macos_http_enabled=http_enabled,
            macos_http_proxy_server=http_proxy,
            macos_https_enabled=https_enabled,
            macos_https_proxy_server=https_proxy,
            macos_auto_config_url=auto_url,
        )

    enabled, proxy_server, auto_config_url = _read_wininet()
    return WindowsProxyInfo(
        wininet_enabled=enabled,
        wininet_proxy_server=proxy_server,
        wininet_auto_config_url=auto_config_url,
        winhttp_proxy_server=_read_winhttp_proxy(),
    )


def _host_port_from_target(target: str) -> tuple[str, int] | None:
    target = target.strip()
    if not target:
        return None

    if '://' in target:
        parsed = urlparse(target)
        if parsed.hostname is None or parsed.port is None:
            return None
        return parsed.hostname, int(parsed.port)

    host: str | None = None
    port_text: str | None = None
    if target.startswith('['):
        bracket_host, sep, rest = target[1:].partition(']')
        if sep and rest.startswith(':'):
            host = bracket_host
            port_text = rest[1:]
    elif target.count(':') == 1:
        raw_host, port_text = target.rsplit(':', 1)
        host = raw_host.strip()

    if host is None or port_text is None:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    return host, port


def parse_static_http_proxy(proxy_server: str | None) -> HttpProxyConfig | None:
    """Parse simple WinINET/WinHTTP proxy strings into an HTTP CONNECT proxy."""
    if not proxy_server:
        return None

    parts = [part.strip() for part in re.split(r'[;\s]+', proxy_server) if part.strip()]
    scheme_targets: dict[str, str] = {}
    bare_targets: list[str] = []

    for part in parts:
        if '=' in part:
            scheme, target = part.split('=', 1)
            scheme_targets[scheme.strip().lower()] = target.strip()
        else:
            bare_targets.append(part)

    for key in ('https', 'http'):
        parsed = _host_port_from_target(scheme_targets.get(key, ''))
        if parsed:
            return HttpProxyConfig(host=parsed[0], port=parsed[1])

    for target in bare_targets:
        parsed = _host_port_from_target(target)
        if parsed:
            return HttpProxyConfig(host=parsed[0], port=parsed[1])

    return None


def detected_http_proxy(info: WindowsProxyInfo) -> HttpProxyConfig | None:
    if platform.system() == 'Darwin':
        if info.macos_https_enabled:
            proxy = parse_static_http_proxy(info.macos_https_proxy_server)
            if proxy is not None:
                return proxy
        if info.macos_http_enabled:
            return parse_static_http_proxy(info.macos_http_proxy_server)
        return None

    if info.wininet_enabled:
        proxy = parse_static_http_proxy(info.wininet_proxy_server)
        if proxy is not None:
            return proxy
    return parse_static_http_proxy(info.winhttp_proxy_server)
