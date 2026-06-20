from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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


def test_helper_creates_missing_hosts_parent_directory(tmp_path, monkeypatch):
    hosts_root = tmp_path / "system"
    hosts_root.mkdir()
    hosts_file = hosts_root / "etc" / "hosts"
    token_file = tmp_path / "token"
    token_file.write_text("x" * 48, encoding="utf-8")
    monkeypatch.setattr(daemon, "HOSTS_FILE", str(hosts_file))
    monkeypatch.setattr(daemon, "_token_file", str(token_file))
    monkeypatch.setattr(daemon, "_flush_dns", lambda: None)
    monkeypatch.setattr(daemon, "_active_hosts", set())
    monkeypatch.setattr(daemon, "_last_heartbeat", 0.0)

    daemon._set_hosts({"assetdelivery.roblox.com"})

    assert hosts_file.parent.is_dir()
    assert "127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry" in hosts_file.read_text(encoding="utf-8")


def test_helper_recreates_missing_hosts_file_with_macos_defaults(tmp_path, monkeypatch):
    hosts_file = tmp_path / "hosts"
    token_file = tmp_path / "token"
    token_file.write_text("x" * 48, encoding="utf-8")
    monkeypatch.setattr(daemon, "HOSTS_FILE", str(hosts_file))
    monkeypatch.setattr(daemon, "_token_file", str(token_file))
    monkeypatch.setattr(daemon, "_flush_dns", lambda: None)
    monkeypatch.setattr(daemon, "_active_hosts", set())
    monkeypatch.setattr(daemon, "_last_heartbeat", 0.0)

    daemon._set_hosts({"assetdelivery.roblox.com"})

    content = hosts_file.read_text(encoding="utf-8")
    assert "127.0.0.1\tlocalhost" in content
    assert "255.255.255.255\tbroadcasthost" in content
    assert "::1             localhost" in content
    assert "127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry" in content


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

    assert args[0] == str(macos_proxy_helper.HELPER_INSTALL_PATH)
    assert "/usr/bin/python3" not in args
    assert "/Users/" not in args[0]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True


def test_frozen_helper_source_prefers_bundled_executable(tmp_path, monkeypatch):
    frozen_root = tmp_path / "_MEIPASS"
    frozen_root.mkdir()
    bundled_executable = frozen_root / macos_proxy_helper.HELPER_BUNDLED_EXECUTABLE_NAME
    bundled_source = frozen_root / "macos_proxy_helper_daemon.py"
    bundled_executable.write_bytes(b"helper binary")
    bundled_source.write_text("# source fallback\n", encoding="utf-8")

    monkeypatch.setattr(macos_proxy_helper.sys, "_MEIPASS", str(frozen_root), raising=False)

    assert macos_proxy_helper._source_helper_path() == bundled_executable


def test_helper_installer_stages_helper_before_privileged_install(tmp_path, monkeypatch):
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
    assert "/usr/bin/python3" not in script
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


def _make_self_signed_ca_pem(common_name: str = "Fleasion Proxy CA", organization: str = "Fleasion") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


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


def test_helper_patch_ca_strips_all_fleasion_cas_when_requesting_full_cleanup(tmp_path, monkeypatch):
    _hosts_file, token_file = _reset_daemon_state(tmp_path, monkeypatch)
    resources = _fake_roblox_resources(tmp_path)
    ca_file = resources / "ssl" / "cacert.pem"
    ca_file.parent.mkdir()
    stale_ca = _make_self_signed_ca_pem()
    unrelated_ca = _make_self_signed_ca_pem(organization="Other Org")
    current_ca = _make_self_signed_ca_pem()
    ca_file.write_text(f"MOZILLA ROOT\n{stale_ca}{unrelated_ca}", encoding="utf-8")

    response = daemon._handle_request({
        "token": token_file.read_text(),
        "action": "patch_ca",
        "ca_pem": current_ca,
        "installs": [{
            "resource_dir": str(resources),
            "remove_pems": [],
            "strip_all_fleasion_ca": True,
        }],
    })

    patched_text = ca_file.read_text(encoding="utf-8")
    assert response["ok"] is True
    assert response["patched"][0]["ca_file"] == str(ca_file)
    assert stale_ca not in patched_text
    assert unrelated_ca in patched_text
    assert current_ca in patched_text
    assert patched_text.count("-----BEGIN CERTIFICATE-----") == 2


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
