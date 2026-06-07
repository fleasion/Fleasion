import json
from pathlib import Path

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


def test_installed_helper_plist_runs_root_owned_helper_copy():
    plist = macos_proxy_helper.plistlib.loads(macos_proxy_helper._build_plist())
    args = plist["ProgramArguments"]

    assert args[0] == "/usr/bin/python3"
    assert args[1] == str(macos_proxy_helper.HELPER_INSTALL_PATH)
    assert "/Users/" not in args[1]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
