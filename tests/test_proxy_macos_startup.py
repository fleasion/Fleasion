import asyncio
from datetime import datetime, timedelta, timezone
import certifi
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import Fleasion.proxy.master as proxy_master
from Fleasion.utils import linux_proxy_helper, macos_proxy_helper


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


def test_linux_roblox_ca_patch_reseeds_truncated_bundle_even_when_current_ca_exists(tmp_path, monkeypatch):
    logs = []
    roblox_dir = tmp_path / "asset_overlay"
    healthy_dir = tmp_path / "exe"
    ssl_dir = roblox_dir / "ssl"
    healthy_ssl_dir = healthy_dir / "ssl"
    ssl_dir.mkdir(parents=True)
    healthy_ssl_dir.mkdir(parents=True)
    ca_file = ssl_dir / "cacert.pem"
    healthy_ca_file = healthy_ssl_dir / "cacert.pem"
    ca_pem = _make_self_signed_ca_pem()
    other_ca = _make_self_signed_ca_pem(common_name="Other Root", organization="Other")
    ca_file.write_text(f"{other_ca}\n{ca_pem}", encoding="utf-8")
    assert ca_file.stat().st_size < proxy_master._CACERT_MIN_HEALTHY_SIZE_BYTES

    mozilla_bundle = tmp_path / "mozilla-cacert.pem"
    mozilla_bundle.write_text(
        "## Bundle of CA Root Certificates\n"
        "-----BEGIN CERTIFICATE-----\nROOT1\n-----END CERTIFICATE-----\n"
        + ("x" * 5000)
        + "\n-----BEGIN CERTIFICATE-----\nROOT2\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    healthy_ca_file.write_text(mozilla_bundle.read_text(encoding="utf-8") + ca_pem, encoding="utf-8")

    monkeypatch.setattr(proxy_master, "IS_MACOS", False)
    monkeypatch.setattr(proxy_master, "IS_LINUX", True)
    monkeypatch.setattr(proxy_master, "_find_roblox_dirs", lambda: [roblox_dir, healthy_dir])
    monkeypatch.setattr(certifi, "where", lambda: (_ for _ in ()).throw(AssertionError("should prefer local healthy bundle")))
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))

    ok, details = proxy_master._install_ca_into_roblox(ca_pem)

    patched_text = ca_file.read_text(encoding="utf-8")
    assert ok is True
    assert details["verified"][0]["healthy"] is True
    assert patched_text.startswith("## Bundle of CA Root Certificates")
    _, fleasion_count, current_count = proxy_master._analyze_and_strip_fleasion_cas(patched_text, ca_pem)
    assert fleasion_count == 1
    assert current_count == 1
    assert details["patched"][0] == {"resource_dir": str(roblox_dir), "ca_file": str(ca_file), "changed": True}
    assert any("Seeded Roblox cacert.pem from healthy local bundle" in message for _category, message in logs)


def test_direct_cacert_upsert_clears_read_only_before_write(tmp_path):
    ca_file = tmp_path / "Roblox" / "ssl" / "cacert.pem"
    ca_file.parent.mkdir(parents=True)
    ca_file.write_text("MOZILLA ROOTS\n", encoding="utf-8")
    ca_file.chmod(0o444)
    ca_pem = "-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n"

    changed, fleasion_count, current_count = proxy_master._upsert_fleasion_ca_in_cacert(ca_file, ca_pem)

    assert changed is True
    assert fleasion_count == 0
    assert current_count == 0
    assert ca_file.read_text(encoding="utf-8") == f"MOZILLA ROOTS\n{ca_pem}"
    assert not (ca_file.stat().st_mode & stat.S_IWRITE)


def test_cacert_write_barrier_clear_removes_immutable_flags(monkeypatch):
    calls = []

    class FakePath:
        mode = 0o444

        def stat(self):
            return SimpleNamespace(st_mode=self.mode, st_flags=0b1111)

        def is_dir(self):
            return False

        def chmod(self, mode):
            self.mode = mode

    fake_path = FakePath()
    monkeypatch.setattr(proxy_master.stat, "UF_IMMUTABLE", 0b0001, raising=False)
    monkeypatch.setattr(proxy_master.stat, "UF_APPEND", 0b0010, raising=False)
    monkeypatch.setattr(proxy_master.stat, "SF_IMMUTABLE", 0b0100, raising=False)
    monkeypatch.setattr(proxy_master.stat, "SF_APPEND", 0b1000, raising=False)
    monkeypatch.setattr(proxy_master.os, "chflags", lambda path, flags: calls.append((path, flags)), raising=False)

    proxy_master._clear_cacert_write_barriers(fake_path)

    assert calls == [(fake_path, 0)]
    assert fake_path.mode & stat.S_IWRITE


def test_linux_cacert_seed_clears_read_only_before_copy(tmp_path, monkeypatch):
    logs = []
    ca_file = tmp_path / "asset_overlay" / "ssl" / "cacert.pem"
    source = tmp_path / "healthy" / "ssl" / "cacert.pem"
    ca_file.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    ca_file.write_text("truncated", encoding="utf-8")
    source.write_text("replacement bundle", encoding="utf-8")
    ca_file.chmod(0o444)

    monkeypatch.setattr(proxy_master, "IS_LINUX", True)
    monkeypatch.setattr(proxy_master, "_healthy_linux_cacert_source", lambda *_args: source)
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))

    seeded = proxy_master._seed_linux_cacert_if_needed(
        ca_file,
        {"exists": True, "size": 9, "total_certs": 0, "error": ""},
        "asset_overlay",
        "ca",
        [tmp_path / "asset_overlay", tmp_path / "healthy"],
    )

    assert seeded is True
    assert ca_file.read_text(encoding="utf-8") == "replacement bundle"
    assert not (ca_file.stat().st_mode & stat.S_IWRITE)
    assert any("Seeded Roblox cacert.pem from healthy local bundle" in message for _category, message in logs)


def test_linux_proxy_start_emits_error_when_helper_denied(tmp_path, monkeypatch):
    errors = []
    ca_cert = tmp_path / "ca.crt"
    ca_key = tmp_path / "ca.key"
    leaf_cert = tmp_path / "leaf.crt"
    leaf_key = tmp_path / "leaf.key"
    default_cert = (tmp_path / "default.crt", tmp_path / "default.key")
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text("x", encoding="utf-8")

    class _ProxyStub:
        async def log_upstream_self_test(self, _hosts):
            return None

        def set_module_interceptors(self, _interceptors):
            return None

        async def start(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setattr(proxy_master, "IS_MACOS", False)
    monkeypatch.setattr(proxy_master, "IS_WINDOWS", False)
    monkeypatch.setattr(proxy_master, "IS_LINUX", True)
    monkeypatch.setattr(proxy_master, "_use_linux_privileged_helper", lambda: True)
    monkeypatch.setattr(proxy_master, "generate_ca", lambda _dir: (ca_cert, ca_key))
    monkeypatch.setattr(proxy_master, "generate_host_cert", lambda *_args, **_kwargs: (leaf_cert, leaf_key))
    monkeypatch.setattr(proxy_master, "generate_multi_host_cert", lambda *_args, **_kwargs: default_cert)
    monkeypatch.setattr(proxy_master, "get_ca_pem", lambda _path: "ca")
    monkeypatch.setattr(proxy_master, "_install_ca_into_roblox", lambda _pem: (True, {}))
    monkeypatch.setattr(proxy_master, "_resolve_real_endpoints", lambda _hosts: {})
    monkeypatch.setattr(proxy_master, "_run_tls_self_test", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(proxy_master, "FleasionProxy", lambda **_kwargs: _ProxyStub())
    monkeypatch.setattr(linux_proxy_helper, "install_ca_into_linux_trust", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(linux_proxy_helper, "linux_system_ca_needs_install", lambda _path: False)
    monkeypatch.setattr(linux_proxy_helper, "start_helper", lambda *_args, **_kwargs: False)

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.config_manager = SimpleNamespace(
        clear_cache_on_launch=False,
        settings={},
        upstream_transport_mode="direct",
        vpn_compat_max_assetdelivery_connections=0,
        vpn_compat_max_cdn_connections=0,
    )
    proxy.cache_scraper = SimpleNamespace(set_real_ips=lambda _ips: None)
    proxy.username_spoofer = SimpleNamespace(is_enabled=lambda: False)
    proxy._module_interceptors = []
    proxy._on_proxy_start_error = lambda code, details: errors.append((code, details))
    proxy._running = False
    proxy._lock = threading.Lock()
    proxy._loop = None
    proxy._roblox_player_running = False

    asyncio.run(proxy._run_proxy())

    assert proxy._running is False
    assert errors == [("linux_helper_unavailable", {})]


def test_macos_roblox_dir_discovery_excludes_studio_saved_dirs(tmp_path, monkeypatch):
    player = tmp_path / "Roblox.app" / "Contents" / "Resources"
    studio = tmp_path / "RobloxStudio.app" / "Contents" / "Resources"
    player.mkdir(parents=True)
    studio.mkdir(parents=True)
    discovery_calls = []
    persisted = []

    def fake_find_roblox_resource_dirs(include_studio: bool):
        discovery_calls.append(include_studio)
        return [player] + ([studio] if include_studio else [])

    monkeypatch.setattr(proxy_master, "IS_MACOS", True)
    monkeypatch.setattr(proxy_master, "IS_LINUX", False)
    monkeypatch.setattr("Fleasion.utils.platform_macos.find_roblox_resource_dirs", fake_find_roblox_resource_dirs)
    monkeypatch.setattr(proxy_master, "load_saved_roblox_dirs", lambda: [studio])
    monkeypatch.setattr(proxy_master, "save_saved_roblox_dirs", lambda dirs: persisted.extend(dirs))

    assert proxy_master._find_roblox_dirs() == [player]
    assert discovery_calls == [False]
    assert persisted == [player]


def test_macos_studio_launch_skips_ca_patch(tmp_path, monkeypatch):
    ca_dir = tmp_path / "proxy_ca"
    ca_dir.mkdir()
    (ca_dir / "ca.crt").write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    studio_exe = tmp_path / "RobloxStudio.app" / "Contents" / "MacOS" / "RobloxStudio"
    studio_exe.parent.mkdir(parents=True)
    studio_exe.write_text("stub", encoding="utf-8")
    logs = []

    monkeypatch.setattr(proxy_master, "IS_MACOS", True)
    monkeypatch.setattr(proxy_master, "_current_proxy_ca_dir", lambda: ca_dir)
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))
    monkeypatch.setattr(proxy_master, "_log_cacert_state", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not inspect Studio certs")))
    monkeypatch.setattr(proxy_master, "_upsert_fleasion_ca_in_cacert", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not patch Studio certs")))

    assert proxy_master.check_and_patch_running_roblox_ca(studio_exe) is False
    assert any("Skipping macOS Roblox Studio CA patch" in message for _category, message in logs)


def test_macos_running_player_ca_repair_uses_privileged_helper(tmp_path, monkeypatch):
    ca_dir = tmp_path / "proxy_ca"
    ca_dir.mkdir()
    (ca_dir / "ca.crt").write_text("ca", encoding="utf-8")
    resources = tmp_path / "Roblox.app" / "Contents" / "Resources"
    macos = tmp_path / "Roblox.app" / "Contents" / "MacOS"
    ssl_dir = resources / "ssl"
    macos.mkdir(parents=True)
    ssl_dir.mkdir(parents=True)
    exe_path = macos / "RobloxPlayer"
    exe_path.write_text("stub", encoding="utf-8")
    ca_file = ssl_dir / "cacert.pem"
    ca_file.write_text("MOZILLA ROOTS\n", encoding="utf-8")
    helper_calls = []
    states = [
        {
            "exists": True,
            "healthy": False,
            "fleasion_certs": 0,
            "current_fleasion_certs": 0,
            "sha256": "before",
        },
        {
            "exists": True,
            "healthy": True,
            "fleasion_certs": 1,
            "current_fleasion_certs": 1,
            "sha256": "after",
        },
    ]

    def fake_helper_patch(ca_pem, installs):
        helper_calls.append((ca_pem, installs))
        return {
            "ok": True,
            "patched": [{"resource_dir": str(resources), "ca_file": str(ca_file), "changed": True}],
            "skipped": [],
            "failed": [],
        }

    monkeypatch.setattr(proxy_master, "IS_MACOS", True)
    monkeypatch.setattr(proxy_master, "IS_LINUX", False)
    monkeypatch.setattr(proxy_master, "_is_admin", lambda: False)
    monkeypatch.setattr(proxy_master, "_current_proxy_ca_dir", lambda: ca_dir)
    monkeypatch.setattr(proxy_master, "get_ca_pem", lambda _path: "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr("Fleasion.utils.platform_macos._resource_root_from_executable", lambda _path: resources)
    monkeypatch.setattr(macos_proxy_helper, "helper_patch_ca", fake_helper_patch)
    monkeypatch.setattr(proxy_master, "_log_cacert_state", lambda *_args, **_kwargs: states.pop(0))
    monkeypatch.setattr(proxy_master, "_upsert_fleasion_ca_in_cacert", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use helper")))

    assert proxy_master.check_and_patch_running_roblox_ca(exe_path) is True
    assert helper_calls == [
        (
            "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n",
            [{"resource_dir": str(resources), "remove_pems": [], "strip_all_fleasion_ca": False}],
        )
    ]


def test_macos_running_player_ca_repair_requests_full_strip_when_pre_read_fails(tmp_path, monkeypatch):
    ca_dir = tmp_path / "proxy_ca"
    ca_dir.mkdir()
    (ca_dir / "ca.crt").write_text("ca", encoding="utf-8")
    resources = tmp_path / "Roblox.app" / "Contents" / "Resources"
    macos = tmp_path / "Roblox.app" / "Contents" / "MacOS"
    ssl_dir = resources / "ssl"
    macos.mkdir(parents=True)
    ssl_dir.mkdir(parents=True)
    exe_path = macos / "RobloxPlayer"
    exe_path.write_text("stub", encoding="utf-8")
    (ssl_dir / "cacert.pem").write_text("MOZILLA ROOTS\n", encoding="utf-8")
    helper_calls = []
    original_read_text = proxy_master.Path.read_text

    def fake_helper_patch(ca_pem, installs):
        helper_calls.append((ca_pem, installs))
        return {"ok": True, "patched": [], "skipped": [], "failed": []}

    def fake_read_text(self, *args, **kwargs):
        if self == ssl_dir / "cacert.pem":
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(proxy_master, "IS_MACOS", True)
    monkeypatch.setattr(proxy_master, "IS_LINUX", False)
    monkeypatch.setattr(proxy_master, "_is_admin", lambda: False)
    monkeypatch.setattr(proxy_master, "_current_proxy_ca_dir", lambda: ca_dir)
    monkeypatch.setattr(proxy_master, "get_ca_pem", lambda _path: "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr("Fleasion.utils.platform_macos._resource_root_from_executable", lambda _path: resources)
    monkeypatch.setattr(proxy_master.Path, "read_text", fake_read_text)
    monkeypatch.setattr(macos_proxy_helper, "helper_patch_ca", fake_helper_patch)

    request_ok, changed, details = proxy_master._patch_roblox_ca_with_macos_helper(
        "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n",
        resources,
    )

    assert request_ok is True
    assert changed is False
    assert details["ok"] is True
    assert helper_calls == [
        (
            "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n",
            [{"resource_dir": str(resources), "remove_pems": [], "strip_all_fleasion_ca": True}],
        )
    ]


def test_macos_system_keychain_removes_stale_fleasion_ca_before_current_check(tmp_path, monkeypatch):
    logs = []
    calls = []
    ca_cert = tmp_path / "ca.crt"
    ca_cert.write_text("ca", encoding="utf-8")
    stale_ca = _make_self_signed_ca_pem()
    current_ca = _make_self_signed_ca_pem()
    lookalike_ca = _make_self_signed_ca_pem(organization="Other Org")
    stale_thumbprint = proxy_master._ca_thumbprint_sha1(stale_ca)
    lookalike_thumbprint = proxy_master._ca_thumbprint_sha1(lookalike_ca)

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args[:5] == ["security", "find-certificate", "-a", "-p", "-c"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"{stale_ca}\n{current_ca}\n{lookalike_ca}\n",
                stderr="",
            )
        if args[:3] == ["security", "delete-certificate", "-Z"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected security call: {args}")

    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))
    monkeypatch.setattr(proxy_master.subprocess, "run", fake_run)

    proxy_master._install_ca_into_macos_system_keychain(ca_cert, current_ca)

    assert ["security", "delete-certificate", "-Z", stale_thumbprint, "/Library/Keychains/System.keychain"] in calls
    assert not any(isinstance(call, list) and lookalike_thumbprint in call for call in calls)
    assert not any("add-trusted-cert" in call for call in calls)
    assert any("removed 1 stale Fleasion CA entry" in message for _category, message in logs)
