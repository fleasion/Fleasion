"""System proxy discovery for upstream CONNECT fallback."""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .upstream import HttpProxyConfig


@dataclass
class WindowsProxyInfo:
    wininet_enabled: bool = False
    wininet_proxy_server: Optional[str] = None
    wininet_auto_config_url: Optional[str] = None
    winhttp_proxy_server: Optional[str] = None
    macos_http_enabled: bool = False
    macos_http_proxy_server: Optional[str] = None
    macos_https_enabled: bool = False
    macos_https_proxy_server: Optional[str] = None
    macos_auto_config_url: Optional[str] = None


def _query_reg_value(key, name: str):
    try:
        import winreg

        value, _ = winreg.QueryValueEx(key, name)
        return value
    except Exception:
        return None


def _read_wininet() -> tuple[bool, Optional[str], Optional[str]]:
    if platform.system() != "Windows":
        return False, None, None

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = bool(int(_query_reg_value(key, "ProxyEnable") or 0))
            proxy_server = _query_reg_value(key, "ProxyServer")
            auto_config_url = _query_reg_value(key, "AutoConfigURL")
            return (
                enabled,
                str(proxy_server) if proxy_server else None,
                str(auto_config_url) if auto_config_url else None,
            )
    except Exception:
        return False, None, None


def _read_winhttp_proxy() -> Optional[str]:
    if platform.system() != "Windows":
        return None

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["netsh", "winhttp", "show", "proxy"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creationflags,
        )
    except Exception:
        return None

    text = (result.stdout or "") + "\n" + (result.stderr or "")
    if "Direct access" in text:
        return None

    match = re.search(r"Proxy Server\(s\)\s*:\s*(.+)", text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        return value or None
    return None


def _proxy_target(host: Optional[str], port: object) -> Optional[str]:
    host_text = str(host or "").strip()
    if not host_text:
        return None
    try:
        port_int = int(str(port).strip())
    except (TypeError, ValueError):
        return None
    if port_int <= 0 or port_int > 65535:
        return None
    if ":" in host_text and not host_text.startswith("["):
        host_text = f"[{host_text}]"
    return f"{host_text}:{port_int}"


def _parse_scutil_proxy_output(text: str) -> tuple[bool, Optional[str], bool, Optional[str], Optional[str]]:
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = re.match(r"\s*([A-Za-z0-9]+)\s*:\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2)

    http_enabled = values.get("HTTPEnable") == "1"
    https_enabled = values.get("HTTPSEnable") == "1"
    auto_config_url = values.get("ProxyAutoConfigURLString") if values.get("ProxyAutoConfigEnable") == "1" else None
    http_proxy = _proxy_target(values.get("HTTPProxy"), values.get("HTTPPort")) if http_enabled else None
    https_proxy = _proxy_target(values.get("HTTPSProxy"), values.get("HTTPSPort")) if https_enabled else None
    return http_enabled, http_proxy, https_enabled, https_proxy, auto_config_url


def _read_macos_proxies() -> tuple[bool, Optional[str], bool, Optional[str], Optional[str]]:
    if platform.system() != "Darwin":
        return False, None, False, None, None

    try:
        result = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False, None, False, None, None

    return _parse_scutil_proxy_output((result.stdout or "") + "\n" + (result.stderr or ""))


def detect_windows_proxy() -> WindowsProxyInfo:
    if platform.system() == "Darwin":
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


def _host_port_from_target(target: str) -> Optional[tuple[str, int]]:
    target = target.strip()
    if not target:
        return None

    if "://" in target:
        parsed = urlparse(target)
        host = parsed.hostname
        port = parsed.port
        if host and port:
            return host, int(port)
        return None

    if target.startswith("["):
        host, sep, rest = target[1:].partition("]")
        if sep and rest.startswith(":"):
            try:
                return host, int(rest[1:])
            except ValueError:
                return None
        return None

    if target.count(":") == 1:
        host, port_text = target.rsplit(":", 1)
        try:
            return host.strip(), int(port_text)
        except ValueError:
            return None

    return None


def parse_static_http_proxy(proxy_server: Optional[str]) -> Optional[HttpProxyConfig]:
    """Parse simple WinINET/WinHTTP proxy strings into an HTTP CONNECT proxy."""
    if not proxy_server:
        return None

    parts = [part.strip() for part in re.split(r"[;\s]+", proxy_server) if part.strip()]
    scheme_targets: dict[str, str] = {}
    bare_targets: list[str] = []

    for part in parts:
        if "=" in part:
            scheme, target = part.split("=", 1)
            scheme_targets[scheme.strip().lower()] = target.strip()
        else:
            bare_targets.append(part)

    for key in ("https", "http"):
        parsed = _host_port_from_target(scheme_targets.get(key, ""))
        if parsed:
            return HttpProxyConfig(host=parsed[0], port=parsed[1])

    for target in bare_targets:
        parsed = _host_port_from_target(target)
        if parsed:
            return HttpProxyConfig(host=parsed[0], port=parsed[1])

    return None


def detected_http_proxy(info: WindowsProxyInfo) -> Optional[HttpProxyConfig]:
    if platform.system() == "Darwin":
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
