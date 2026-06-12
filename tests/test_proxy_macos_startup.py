import asyncio
from pathlib import Path
from types import SimpleNamespace

import Fleasion.proxy.master as proxy_master
from Fleasion.utils import macos_proxy_helper


def test_proxy_ca_dir_falls_back_when_configured_dir_is_not_writable(tmp_path, monkeypatch):
    configured = tmp_path / "proxy_ca"
    fallback = tmp_path / "proxy_ca_user"
    checked = []
    logs = []

    monkeypatch.setattr(proxy_master, "PROXY_CA_DIR", configured)
    monkeypatch.setattr(proxy_master, "_ACTIVE_PROXY_CA_DIR", configured)
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))
    monkeypatch.setattr(
        proxy_master,
        "_directory_is_writable",
        lambda path: checked.append(path) or path == fallback,
    )

    selected = proxy_master._select_proxy_ca_dir()

    assert selected == fallback
    assert proxy_master._current_proxy_ca_dir() == fallback
    assert checked == [configured, fallback]
    assert logs == [
        (
            "Certificate",
            f"Configured CA directory is not writable ({configured}); using {fallback}",
        )
    ]


def test_cacert_state_does_not_log_when_healthy(monkeypatch):
    logs = []
    healthy_state = {
        "path": "/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem",
        "install": "Resources",
        "exists": True,
        "size": 229889,
        "mtime_ns": 1,
        "sha256": "a" * 64,
        "total_certs": 148,
        "fleasion_certs": 1,
        "current_fleasion_certs": 1,
        "healthy": True,
        "error": "",
    }

    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))
    monkeypatch.setattr(proxy_master, "_describe_cacert_state", lambda _path, _pem: healthy_state)

    assert proxy_master._log_cacert_state(Path("/tmp/cacert.pem"), "ca", "healthy check") == healthy_state
    assert logs == []


def test_cacert_state_still_logs_when_unhealthy(monkeypatch):
    logs = []
    unhealthy_state = {
        "path": "/Applications/Roblox.app/Contents/Resources/ssl/cacert.pem",
        "install": "Resources",
        "exists": True,
        "size": 100,
        "mtime_ns": 1,
        "sha256": "b" * 64,
        "total_certs": 1,
        "fleasion_certs": 0,
        "current_fleasion_certs": 0,
        "healthy": False,
        "error": "",
    }

    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))
    monkeypatch.setattr(proxy_master, "_describe_cacert_state", lambda _path, _pem: unhealthy_state)

    proxy_master._log_cacert_state(Path("/tmp/cacert.pem"), "ca", "unhealthy check")

    assert any("unhealthy check" in message for _category, message in logs)
    assert any("not launch-healthy" in message for _category, message in logs)


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
