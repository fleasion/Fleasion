import asyncio
from pathlib import Path
from types import SimpleNamespace

import Fleasion.proxy.master as proxy_master
from Fleasion.utils import macos_proxy_helper


def test_proxy_ca_dir_falls_back_when_configured_dir_is_not_writable(tmp_path, monkeypatch):
    configured = tmp_path / "proxy_ca"
    fallback = tmp_path / "proxy_ca_user"
    checked = []

    monkeypatch.setattr(proxy_master, "PROXY_CA_DIR", configured)
    monkeypatch.setattr(proxy_master, "_ACTIVE_PROXY_CA_DIR", configured)
    monkeypatch.setattr(
        proxy_master,
        "_directory_is_writable",
        lambda path: checked.append(path) or path == fallback,
    )

    selected = proxy_master._select_proxy_ca_dir()

    assert selected == fallback
    assert proxy_master._current_proxy_ca_dir() == fallback
    assert checked == [configured, fallback]


def test_macos_proxy_start_blocks_when_ca_patch_verification_fails(tmp_path, monkeypatch):
    errors = []
    hosts_calls = []
    ca_cert = tmp_path / "ca.crt"
    ca_key = tmp_path / "ca.key"
    leaf_cert = tmp_path / "leaf.crt"
    leaf_key = tmp_path / "leaf.key"
    default_cert = (tmp_path / "default.crt", tmp_path / "default.key")
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text("x", encoding="utf-8")

    monkeypatch.setattr(proxy_master, "IS_MACOS", True)
    monkeypatch.setattr(proxy_master, "IS_WINDOWS", False)
    monkeypatch.setattr(macos_proxy_helper, "helper_is_ready", lambda: True)
    monkeypatch.setattr(proxy_master, "generate_ca", lambda _dir: (ca_cert, ca_key))
    monkeypatch.setattr(proxy_master, "generate_host_cert", lambda *_args, **_kwargs: (leaf_cert, leaf_key))
    monkeypatch.setattr(proxy_master, "generate_multi_host_cert", lambda *_args, **_kwargs: default_cert)
    monkeypatch.setattr(proxy_master, "get_ca_pem", lambda _path: "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(
        proxy_master,
        "_install_ca_into_roblox",
        lambda _pem: (False, {"failed": [{"resource_dir": "/Applications/Roblox.app/Contents/Resources"}]}),
    )
    monkeypatch.setattr(proxy_master, "_add_hosts_entries", lambda *args, **kwargs: hosts_calls.append("add") or True)
    monkeypatch.setattr(proxy_master, "_remove_hosts_entries", lambda *args, **kwargs: hosts_calls.append("remove") or True)

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.config_manager = SimpleNamespace(clear_cache_on_launch=False)
    proxy._on_proxy_start_error = lambda code, details: errors.append((code, details))
    proxy._running = False
    proxy._loop = None

    asyncio.run(proxy._run_proxy())

    assert proxy._running is False
    assert errors and errors[0][0] == "macos_ca_patch_failed"
    assert hosts_calls == []
