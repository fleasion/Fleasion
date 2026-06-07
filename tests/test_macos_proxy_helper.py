import os
from pathlib import Path
import subprocess

import pytest

from Fleasion import macos_proxy_helper_daemon as daemon
from Fleasion.utils import macos_proxy_helper


def _reset_daemon_state(tmp_path, monkeypatch):
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n10.0.0.1 custom.example\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("x" * 48, encoding="utf-8")
    monkeypatch.setattr(daemon, "HOSTS_FILE", str(hosts_file))
    monkeypatch.setattr(daemon, "_token_file", str(token_file))
    monkeypatch.setattr(daemon, "_flush_dns", lambda: None)
    monkeypatch.setattr(daemon, "_active_hosts", set())
    monkeypatch.setattr(daemon, "_last_heartbeat", 0.0)
    return hosts_file, token_file


def test_helper_only_manages_allowlisted_fleasion_hosts(tmp_path, monkeypatch):
    hosts_file, _ = _reset_daemon_state(tmp_path, monkeypatch)

    daemon._set_hosts({"assetdelivery.roblox.com", "gamejoin.roblox.com"})
    content = hosts_file.read_text(encoding="utf-8")

    assert "10.0.0.1 custom.example" in content
    assert "127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry" in content
    assert "127.0.0.1 gamejoin.roblox.com # Fleasion proxy entry" in content

    daemon._set_hosts([])
    cleaned = hosts_file.read_text(encoding="utf-8")
    assert "custom.example" in cleaned
    assert "Fleasion proxy entry" not in cleaned

    with pytest.raises(ValueError):
        daemon._set_hosts({"example.com"})


def test_helper_rejects_conflicting_mapping(tmp_path, monkeypatch):
    hosts_file, _ = _reset_daemon_state(tmp_path, monkeypatch)
    hosts_file.write_text("203.0.113.1 assetdelivery.roblox.com\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="hosts conflict"):
        daemon._set_hosts({"assetdelivery.roblox.com"})


def test_helper_control_requires_token(tmp_path, monkeypatch):
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)

    assert daemon._handle_request({"token": "wrong", "action": "status"})["ok"] is False
    response = daemon._handle_request({"token": token_file.read_text(), "action": "status"})
    assert response["ok"] is True
    assert response["version"] == daemon.HELPER_VERSION
    assert "patch_ca" in response["capabilities"]


def test_installed_helper_plist_runs_root_owned_helper_copy():
    plist = macos_proxy_helper.plistlib.loads(macos_proxy_helper._build_plist())
    args = plist["ProgramArguments"]

    assert args[0] == "/usr/bin/python3"
    assert args[1] == str(macos_proxy_helper.HELPER_INSTALL_PATH)
    assert "/Users/" not in args[1]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True


def test_helper_installer_stages_source_before_privileged_install(tmp_path, monkeypatch):
    source = tmp_path / "Documents" / "Fleasion" / "macos_proxy_helper_daemon.py"
    source.parent.mkdir(parents=True)
    source.write_text("# helper\n", encoding="utf-8")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["script"] = args[-1]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(macos_proxy_helper.sys, "platform", "darwin")
    monkeypatch.setattr(macos_proxy_helper, "_source_helper_path", lambda: source)
    monkeypatch.setattr(macos_proxy_helper, "_ensure_token", lambda: "x" * 48)
    monkeypatch.setattr(macos_proxy_helper, "helper_is_ready", lambda: True)
    monkeypatch.setattr(macos_proxy_helper.subprocess, "run", fake_run)

    ok, detail = macos_proxy_helper.install_helper()

    assert ok is True, detail
    script = captured["script"]
    assert str(source) not in script
    assert "launchctl bootout" in script
    assert script.index("launchctl bootout") < script.index("/usr/bin/install")
    assert "/usr/bin/xattr -c" in script
    assert script.index("/usr/bin/xattr -c") > script.index("/usr/bin/install")
    assert "launchctl bootstrap system" in script
    assert "launchctl load -w" in script


def _fake_roblox_resources(tmp_path: Path) -> Path:
    app = tmp_path / "Roblox.app"
    resources = app / "Contents" / "Resources"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (macos / "RobloxPlayer").write_text("#!/bin/sh\n", encoding="utf-8")
    return resources


def test_helper_patch_ca_writes_only_roblox_cacert_path(tmp_path, monkeypatch):
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ca_file = resources / "ssl" / "cacert.pem"
    ca_file.parent.mkdir()
    old_ca = "-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n"
    current_ca = "-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n"
    ca_file.write_text(f"MOZILLA ROOT\n{old_ca}", encoding="utf-8")

    response = daemon._handle_request({
        "token": token_file.read_text(),
        "action": "patch_ca",
        "ca_pem": current_ca,
        "installs": [{"resource_dir": str(resources), "remove_pems": [old_ca]}],
    })

    assert response["ok"] is True
    assert response["patched"][0]["ca_file"] == str(ca_file)
    assert ca_file.read_text(encoding="utf-8") == f"MOZILLA ROOT\n{current_ca}"
    assert oct(ca_file.stat().st_mode & 0o777) == "0o644"


def test_helper_patch_ca_recovers_from_read_only_bundle_permissions(tmp_path, monkeypatch):
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ssl_dir = resources / "ssl"
    ssl_dir.mkdir()
    ca_file = ssl_dir / "cacert.pem"
    old_ca = "-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n"
    current_ca = "-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n"
    ca_file.write_text(f"MOZILLA ROOT\n{old_ca}", encoding="utf-8")

    resources.chmod(0o555)
    ssl_dir.chmod(0o555)
    ca_file.chmod(0o444)

    response = daemon._handle_request({
        "token": token_file.read_text(),
        "action": "patch_ca",
        "ca_pem": current_ca,
        "installs": [{"resource_dir": str(resources), "remove_pems": [old_ca]}],
    })

    assert response["ok"] is True
    assert response["patched"][0]["ca_file"] == str(ca_file)
    assert ca_file.read_text(encoding="utf-8") == f"MOZILLA ROOT\n{current_ca}"
    assert oct(ca_file.stat().st_mode & 0o777) == "0o644"


def test_helper_patch_ca_rejects_arbitrary_resource_paths(tmp_path, monkeypatch):
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    arbitrary = tmp_path / "NotRoblox" / "Contents" / "Resources"
    arbitrary.mkdir(parents=True)
    current_ca = "-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n"

    response = daemon._handle_request({
        "token": token_file.read_text(),
        "action": "patch_ca",
        "ca_pem": current_ca,
        "installs": [{"resource_dir": str(arbitrary), "remove_pems": []}],
    })

    assert response["ok"] is False
    assert response["failed"]
    assert not (arbitrary / "ssl" / "cacert.pem").exists()


def test_helper_patch_ca_rejects_symlinked_cacert(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ssl_dir = resources / "ssl"
    ssl_dir.mkdir()
    outside = tmp_path / "outside.pem"
    outside.write_text("outside", encoding="utf-8")
    try:
        os.symlink(outside, ssl_dir / "cacert.pem")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    current_ca = "-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n"

    response = daemon._handle_request({
        "token": token_file.read_text(),
        "action": "patch_ca",
        "ca_pem": current_ca,
        "installs": [{"resource_dir": str(resources), "remove_pems": []}],
    })

    assert response["ok"] is False
    assert response["failed"]
    assert outside.read_text(encoding="utf-8") == "outside"


def test_helper_readiness_requires_ca_patch_capability(monkeypatch):
    monkeypatch.setattr(
        macos_proxy_helper,
        "helper_status",
        lambda timeout=1.0: {
            "ok": True,
            "version": 2,
            "backend_port": macos_proxy_helper.MACOS_PROXY_BACKEND_PORT,
            "capabilities": ["hosts", "relay", "patch_ca"],
        },
    )
    assert macos_proxy_helper.helper_is_ready() is False

    monkeypatch.setattr(
        macos_proxy_helper,
        "helper_status",
        lambda timeout=1.0: {
            "ok": True,
            "version": 3,
            "backend_port": macos_proxy_helper.MACOS_PROXY_BACKEND_PORT,
            "capabilities": ["hosts", "relay", "patch_ca"],
        },
    )
    assert macos_proxy_helper.helper_is_ready() is True
