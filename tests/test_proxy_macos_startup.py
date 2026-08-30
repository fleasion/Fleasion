import asyncio
import stat
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Never, Protocol, Self, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import fleasion.proxy.master as proxy_master
from fleasion.config.manager import ConfigManager
from fleasion.utils import linux_proxy_helper, macos_proxy_helper, platform_macos

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='Linux/macOS proxy startup tests')


class _ObjectCallback[T](Protocol):
    def __call__(self, *args: object, **kwargs: object) -> T: ...


class _ArgsCallback(Protocol):
    def __call__(self, *args: object) -> object: ...


class _KwargsCallback(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class _ArgsKwargsCallback(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


class _OneKwargsCallback(Protocol):
    def __call__(self, arg: object, /, **kwargs: object) -> object: ...


class _LogCallback(Protocol):
    def __call__(self, category: str, message: str) -> None: ...


class _ProxyStartErrorCallback(Protocol):
    def __call__(self, code: str, details: dict[str, object]) -> None: ...


class _EnvOverrideCallback(Protocol):
    def __call__(self, url: str, *, client_key: str) -> bool: ...


class _HostUpdateCallback(Protocol):
    def __call__(self, hosts: set[str]) -> bool: ...


class _HostSinkCallback(Protocol):
    def __call__(self, hosts: Iterable[str]) -> None: ...


type _EndpointMap = dict[str, list[proxy_master.UpstreamEndpoint]]


def _constant_callback[T](value: T) -> _ObjectCallback[T]:
    def callback(*_args: object, **_kwargs: object) -> T:
        return value

    return callback


def _callback0(callback: Callable[[], object]) -> Callable[[], object]:
    return callback


def _callback1(callback: Callable[[object], object]) -> Callable[[object], object]:
    return callback


def _callback2(callback: Callable[[object, object], object]) -> Callable[[object, object], object]:
    return callback


def _callback_args(callback: _ArgsCallback) -> _ArgsCallback:
    return callback


def _callback_kwargs(callback: _KwargsCallback) -> _KwargsCallback:
    return callback


def _callback_args_kwargs(callback: _ArgsKwargsCallback) -> _ArgsKwargsCallback:
    return callback


def _callback1_kwargs(callback: _OneKwargsCallback) -> _OneKwargsCallback:
    return callback


def _collect_log(logs: list[tuple[str, str]]) -> _LogCallback:
    def log(category: str, message: str) -> None:
        logs.append((category, message))

    return log


def _collect_proxy_errors(
    errors: list[tuple[str, dict[str, object]]],
) -> _ProxyStartErrorCallback:
    def on_error(code: str, details: dict[str, object]) -> None:
        errors.append((code, details))

    return on_error


def _collect_env_overrides(calls: list[tuple[str, str]]) -> _EnvOverrideCallback:
    def apply(url: str, *, client_key: str) -> bool:
        calls.append((url, client_key))
        return True

    return apply


def _collect_host_updates(calls: list[set[str]]) -> _HostUpdateCallback:
    def update(hosts: set[str]) -> bool:
        calls.append(set(hosts))
        return True

    return update


def _collect_host_sink(calls: list[set[str]]) -> _HostSinkCallback:
    def update(hosts: Iterable[str]) -> None:
        calls.append(set(hosts))

    return update


def _resolve_empty_endpoints(calls: list[set[str]]) -> Callable[[set[str]], _EndpointMap]:
    def resolve(hosts: set[str]) -> _EndpointMap:
        calls.append(set(hosts))
        return {host: [] for host in hosts}

    return resolve


def _ignore_log(_category: str, _message: str) -> None:
    return None


def test_linux_proxy_constructor_recovers_stale_flatpak_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovered: list[bool] = []

    class CacheManagerStub:
        def __init__(self, _config_manager: object) -> None:
            pass

        def set_scraper(self, _scraper: object) -> None:
            pass

    class CacheScraperStub:
        def __init__(self, _cache_manager: object) -> None:
            pass

        def set_enabled(self, _enabled: object) -> None:
            pass

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'CacheManager', CacheManagerStub)
    monkeypatch.setattr(proxy_master, 'CacheScraper', CacheScraperStub)
    monkeypatch.setattr(
        proxy_master, 'UsernameSpoofer', _callback1(lambda _config_manager: object())
    )
    monkeypatch.setattr(
        proxy_master,
        'CustomFFlagModifier',
        _callback1_kwargs(lambda _config_manager, **_kwargs: object()),
    )
    monkeypatch.setattr(
        proxy_master, '_selected_linux_client_installation', _callback0(lambda: None)
    )
    monkeypatch.setattr(
        'fleasion.utils.platform_linux.recover_stale_linux_client_env_proxy_override',
        _callback0(lambda: recovered.append(True) or True),
    )

    config_manager = ConfigManager.__new__(ConfigManager)
    proxy = proxy_master.ProxyMaster(config_manager)

    assert recovered == [True]
    assert getattr(proxy, '_linux_env_proxy_override_client_key') is None


def test_mode_switch_restart_clears_proxy_readiness_before_worker_runs() -> None:
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, '_env_proxy_ready', threading.Event())
    getattr(proxy, '_env_proxy_ready').set()
    stop_entered = threading.Event()
    allow_stop = threading.Event()
    started = threading.Event()

    def stop() -> None:
        stop_entered.set()
        allow_stop.wait(2.0)

    setattr(proxy, 'stop', stop)
    setattr(proxy, 'start', started.set)

    proxy.restart_for_mode_switch()

    assert stop_entered.wait(1.0)
    assert not getattr(proxy, '_env_proxy_ready').is_set()
    allow_stop.set()
    assert started.wait(1.0)


def test_linux_proxy_stop_clears_exact_owned_flatpak_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, '_env_proxy_ready', threading.Event())
    getattr(proxy, '_env_proxy_ready').set()
    setattr(proxy, '_linux_env_proxy_override_client_key', 'sober')
    setattr(proxy, '_sober_env_proxy_override_active', True)
    setattr(proxy, '_stop_linux_sober_custom_fflag_timer', _callback0(lambda: None))
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, '_running', False)
    setattr(proxy, '_thread', None)

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)

    def clear_override(*, client_key: str) -> bool:
        calls.append(client_key)
        return True

    monkeypatch.setattr(
        'fleasion.utils.platform_linux.clear_linux_client_env_proxy_override',
        clear_override,
    )

    proxy.stop()

    assert calls == ['sober']
    assert getattr(proxy, '_linux_env_proxy_override_client_key') is None
    assert getattr(proxy, '_sober_env_proxy_override_active') is False
    assert not getattr(proxy, '_env_proxy_ready').is_set()


def test_linux_resource_discovery_keeps_saved_resource_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleasion.utils import platform_linux

    discovered = tmp_path / 'discovered'
    saved = tmp_path / 'saved'
    persisted: list[list[Path]] = []
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(
        platform_linux,
        'find_roblox_resource_dirs',
        _callback_kwargs(lambda **_kwargs: [discovered]),
    )
    monkeypatch.setattr(proxy_master, 'load_saved_roblox_dirs', _callback0(lambda: [saved]))

    def save_dirs(paths: Iterable[Path]) -> None:
        persisted.append(list(paths))

    monkeypatch.setattr(
        proxy_master,
        'save_saved_roblox_dirs',
        save_dirs,
    )

    assert getattr(proxy_master, '_find_roblox_dirs')() == [discovered, saved]
    assert persisted == [[discovered, saved]]


def test_privileged_relay_tls_self_test_retries_representative_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = {'assetdelivery.roblox.com', 'gamejoin.roblox.com'}
    calls: list[set[str]] = []
    outcomes: Iterator[tuple[bool, list[str]]] = iter(
        [
            (False, ['assetdelivery.roblox.com: EOF', 'gamejoin.roblox.com: EOF']),
            (False, ['assetdelivery.roblox.com: EOF']),
            (False, ['assetdelivery.roblox.com: EOF']),
        ]
    )
    logs: list[tuple[str, str]] = []

    async def fake_result(
        probe_hosts: set[str], _ca_path: Path, _port: int
    ) -> tuple[bool, list[str]]:
        calls.append(set(probe_hosts))
        return next(outcomes)

    async def no_sleep(_delay: object) -> None:
        return None

    monkeypatch.setattr(proxy_master, '_tls_self_test_result', fake_result)
    monkeypatch.setattr(proxy_master.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    ok, failures = asyncio.run(
        getattr(proxy_master, '_run_privileged_relay_tls_self_test')(
            hosts,
            Path('ca.crt'),
            443,
            attempts=3,
            retry_delay=0.01,
        )
    )

    assert ok is False
    assert calls == [
        hosts,
        {'assetdelivery.roblox.com'},
        {'assetdelivery.roblox.com'},
    ]
    assert any(failure.startswith('relay retry check:') for failure in failures)
    assert sum('retrying' in message for _category, message in logs) == 2


def test_privileged_relay_tls_self_test_runs_full_validation_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = {'assetdelivery.roblox.com', 'gamejoin.roblox.com'}
    calls: list[set[str]] = []
    outcomes: Iterator[tuple[bool, list[str]]] = iter(
        [
            (False, ['assetdelivery.roblox.com: EOF']),
            (True, list[str]()),
            (True, list[str]()),
        ]
    )

    async def fake_result(
        probe_hosts: set[str], _ca_path: Path, _port: int
    ) -> tuple[bool, list[str]]:
        calls.append(set(probe_hosts))
        return next(outcomes)

    async def no_sleep(_delay: object) -> None:
        return None

    monkeypatch.setattr(proxy_master, '_tls_self_test_result', fake_result)
    monkeypatch.setattr(proxy_master.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_ignore_log),
    )

    ok, failures = asyncio.run(
        getattr(proxy_master, '_run_privileged_relay_tls_self_test')(
            hosts,
            Path('ca.crt'),
            443,
            attempts=3,
            retry_delay=0.01,
        )
    )

    assert ok is True
    assert failures == []
    assert calls == [
        hosts,
        {'assetdelivery.roblox.com'},
        hosts,
    ]


def test_proxy_ca_dir_falls_back_when_configured_dir_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / 'proxy_ca'
    fallback = tmp_path / 'proxy_ca_user'
    checked: list[Path] = []
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(proxy_master, 'PROXY_CA_DIR', configured)
    monkeypatch.setattr(proxy_master, '_ACTIVE_PROXY_CA_DIR', configured)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    def directory_is_writable(path: Path) -> bool:
        checked.append(path)
        return path == fallback

    monkeypatch.setattr(
        proxy_master,
        '_directory_is_writable',
        directory_is_writable,
    )

    selected = getattr(proxy_master, '_select_proxy_ca_dir')()

    assert selected == fallback
    assert getattr(proxy_master, '_current_proxy_ca_dir')() == fallback
    assert checked == [configured, fallback]
    assert logs == [
        (
            'Certificate',
            f'Configured CA directory is not writable ({configured}); using {fallback}',
        )
    ]


def _make_self_signed_ca_pem(
    common_name: str = 'Fleasion Proxy CA', organization: str = 'Fleasion'
) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        ]
    )
    now = datetime.now(UTC)
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
    return cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')


def test_macos_proxy_start_blocks_when_ca_patch_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[str, dict[str, object]]] = []
    hosts_calls: list[str] = []
    ca_cert = tmp_path / 'ca.crt'
    ca_key = tmp_path / 'ca.key'
    leaf_cert = tmp_path / 'leaf.crt'
    leaf_key = tmp_path / 'leaf.key'
    default_cert = (tmp_path / 'default.crt', tmp_path / 'default.key')
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text('x', encoding='utf-8')

    monkeypatch.setattr(proxy_master, 'IS_MACOS', True)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(macos_proxy_helper, 'helper_is_ready', _callback0(lambda: True))
    monkeypatch.setattr(proxy_master, 'generate_ca', _callback1(lambda _dir: (ca_cert, ca_key)))
    monkeypatch.setattr(
        proxy_master,
        'generate_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: (leaf_cert, leaf_key)),
    )
    monkeypatch.setattr(
        proxy_master,
        'generate_multi_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: default_cert),
    )
    monkeypatch.setattr(
        proxy_master,
        'get_ca_pem',
        _callback1(lambda _path: '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n'),
    )
    monkeypatch.setattr(
        proxy_master,
        '_install_ca_into_roblox',
        _callback1_kwargs(
            lambda _pem, **_kwargs: (
                False,
                {'failed': [{'resource_dir': '/Applications/Roblox.app/Contents/Resources'}]},
            )
        ),
    )
    monkeypatch.setattr(
        proxy_master,
        '_add_hosts_entries',
        _callback_args_kwargs(lambda *args, **kwargs: hosts_calls.append('add') or True),
    )
    monkeypatch.setattr(
        proxy_master,
        '_remove_hosts_entries',
        _callback_args_kwargs(lambda *args, **kwargs: hosts_calls.append('remove') or True),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(clear_cache_on_launch=False))
    setattr(proxy, '_on_proxy_start_error', _collect_proxy_errors(errors))
    setattr(proxy, '_running', False)
    setattr(proxy, '_loop', None)

    asyncio.run(getattr(proxy, '_run_proxy')())

    assert getattr(proxy, '_running') is False
    assert errors
    assert errors[0][0] == 'macos_ca_patch_failed'
    assert hosts_calls == []


def test_macos_relay_failure_emits_health_diagnostics_before_hosts_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[str, dict[str, object]]] = []
    hosts_calls: list[str] = []
    ca_cert = tmp_path / 'ca.crt'
    ca_key = tmp_path / 'ca.key'
    leaf_cert = tmp_path / 'leaf.crt'
    leaf_key = tmp_path / 'leaf.key'
    default_cert = (tmp_path / 'default.crt', tmp_path / 'default.key')
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text('x', encoding='utf-8')

    class _ProxyStub:
        async def log_upstream_self_test(self, _hosts: object) -> None:
            return None

        def set_module_interceptors(self, _interceptors: object) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    relay_calls: list[None] = []

    async def relay_failure(*_args: object, **_kwargs: object) -> tuple[bool, list[str]]:
        relay_calls.append(None)
        return False, ['assetdelivery.roblox.com: SSLEOFError']

    monkeypatch.setattr(proxy_master, 'IS_MACOS', True)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: False))
    monkeypatch.setattr(macos_proxy_helper, 'helper_is_ready', _callback0(lambda: True))
    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_status',
        _callback0(
            lambda: {
                'ok': True,
                'version': macos_proxy_helper.EXPECTED_HELPER_VERSION,
                'backend_port': proxy_master.MACOS_PROXY_BACKEND_PORT,
            }
        ),
    )
    monkeypatch.setattr(
        macos_proxy_helper,
        'helper_probe_backend',
        _callback0(
            lambda: {
                'ok': True,
                'reachable': False,
                'backend_port': proxy_master.MACOS_PROXY_BACKEND_PORT,
                'elapsed_ms': 1,
                'error_type': 'ConnectionRefusedError',
                'errno': 61,
                'error': '[Errno 61] Connection refused',
            }
        ),
    )
    monkeypatch.setattr(proxy_master, 'generate_ca', _callback1(lambda _dir: (ca_cert, ca_key)))
    monkeypatch.setattr(
        proxy_master,
        'generate_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: (leaf_cert, leaf_key)),
    )
    monkeypatch.setattr(
        proxy_master,
        'generate_multi_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: default_cert),
    )
    monkeypatch.setattr(proxy_master, 'get_ca_pem', _callback1(lambda _path: 'ca'))
    monkeypatch.setattr(
        proxy_master,
        '_install_ca_into_roblox',
        _callback1_kwargs(lambda _pem, **_kwargs: (True, dict[str, object]())),
    )
    monkeypatch.setattr(
        proxy_master,
        '_install_ca_into_macos_login_keychain',
        _callback2(lambda _path, _pem: (True, {'trusted': True, 'changed': False})),
    )
    monkeypatch.setattr(proxy_master, '_other_proxy_owner_alive', _callback0(lambda: False))
    monkeypatch.setattr(proxy_master, '_delete_watchdog_task', _callback0(lambda: None))
    monkeypatch.setattr(
        proxy_master,
        '_remove_hosts_entries',
        _callback_args_kwargs(lambda *_args, **_kwargs: hosts_calls.append('remove') or True),
    )
    monkeypatch.setattr(
        proxy_master,
        '_add_hosts_entries',
        _callback_args_kwargs(lambda *_args, **_kwargs: hosts_calls.append('add') or True),
    )
    monkeypatch.setattr(proxy_master, '_flush_dns', _callback0(lambda: None))
    monkeypatch.setattr(proxy_master, '_resolve_real_endpoints', _callback1(lambda _hosts: {}))
    monkeypatch.setattr(
        proxy_master,
        'detect_windows_proxy',
        _callback0(
            lambda: SimpleNamespace(
                macos_http_enabled=False,
                macos_https_enabled=False,
                macos_http_proxy_server='',
                macos_https_proxy_server='',
                macos_auto_config_url='',
            )
        ),
    )
    monkeypatch.setattr(proxy_master, 'detected_http_proxy', _callback1(lambda _info: None))
    monkeypatch.setattr(
        proxy_master,
        '_run_tls_self_test',
        _callback_args_kwargs(lambda *_args, **_kwargs: asyncio.sleep(0, result=True)),
    )
    monkeypatch.setattr(
        proxy_master,
        '_run_privileged_relay_tls_self_test',
        relay_failure,
    )
    monkeypatch.setattr(
        proxy_master, 'FleasionProxy', _callback_kwargs(lambda **_kwargs: _ProxyStub())
    )
    monkeypatch.setattr(
        proxy_master.ProxyMaster,
        '_startup_intercept_hosts',
        _callback1(lambda _self: set(proxy_master.BASE_INTERCEPT_HOSTS)),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(
        proxy,
        'config_manager',
        SimpleNamespace(
            custom_fflags_enabled=False,
            clear_cache_on_launch=False,
            settings={},
            upstream_transport_mode='auto',
            vpn_compat_max_assetdelivery_connections=16,
            vpn_compat_max_cdn_connections=32,
        ),
    )
    setattr(proxy, 'cache_scraper', SimpleNamespace(set_real_ips=_callback1(lambda _ips: None)))
    setattr(proxy, 'custom_fflag_modifier', None)
    setattr(proxy, '_module_interceptors', [])
    setattr(proxy, '_on_proxy_start_error', _collect_proxy_errors(errors))
    setattr(proxy, '_running', False)
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, '_loop', None)

    asyncio.run(asyncio.wait_for(getattr(proxy, '_run_proxy')(), timeout=2.0))

    assert getattr(proxy, '_running') is False
    assert relay_calls == [None]
    assert hosts_calls == ['remove']
    assert len(errors) == 1
    code, details = errors[0]
    assert code == 'macos_relay_failed'
    assert details['attempts'] == 3
    assert details['tls_failures'] == ['assetdelivery.roblox.com: SSLEOFError']
    backend_probe = cast('dict[str, object]', details['backend_probe'])
    helper_status = cast('dict[str, object]', details['helper_status'])
    assert backend_probe['reachable'] is False
    assert helper_status['version'] == macos_proxy_helper.EXPECTED_HELPER_VERSION


def test_linux_roblox_ca_patch_reseeds_truncated_bundle_even_when_current_ca_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs: list[tuple[str, str]] = []
    roblox_dir = tmp_path / 'asset_overlay'
    healthy_dir = tmp_path / 'exe'
    ssl_dir = roblox_dir / 'ssl'
    healthy_ssl_dir = healthy_dir / 'ssl'
    ssl_dir.mkdir(parents=True)
    healthy_ssl_dir.mkdir(parents=True)
    ca_file = ssl_dir / 'cacert.pem'
    healthy_ca_file = healthy_ssl_dir / 'cacert.pem'
    ca_pem = _make_self_signed_ca_pem()
    other_ca = _make_self_signed_ca_pem(common_name='Other Root', organization='Other')
    ca_file.write_text(f'{other_ca}\n{ca_pem}', encoding='utf-8')
    assert ca_file.stat().st_size < getattr(proxy_master, '_CACERT_MIN_HEALTHY_SIZE_BYTES')

    mozilla_bundle = tmp_path / 'mozilla-cacert.pem'
    mozilla_bundle.write_text(
        '## Bundle of CA Root Certificates\n'
        '-----BEGIN CERTIFICATE-----\nROOT1\n-----END CERTIFICATE-----\n'
        + ('x' * 5000)
        + '\n-----BEGIN CERTIFICATE-----\nROOT2\n-----END CERTIFICATE-----\n',
        encoding='utf-8',
    )
    healthy_ca_file.write_text(
        mozilla_bundle.read_text(encoding='utf-8') + ca_pem, encoding='utf-8'
    )

    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(
        proxy_master,
        '_find_roblox_dirs',
        _callback_kwargs(lambda **_kwargs: [roblox_dir, healthy_dir]),
    )
    monkeypatch.setattr(
        certifi,
        'where',
        _callback0(
            lambda: (_ for _ in ()).throw(AssertionError('should prefer local healthy bundle'))
        ),
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    ok, details = getattr(proxy_master, '_install_ca_into_roblox')(ca_pem)

    patched_text = ca_file.read_text(encoding='utf-8')
    assert ok is True
    assert details['verified'][0]['healthy'] is True
    assert patched_text.startswith('## Bundle of CA Root Certificates')
    _, fleasion_count, current_count = getattr(proxy_master, '_analyze_and_strip_fleasion_cas')(
        patched_text, ca_pem
    )
    assert fleasion_count == 1
    assert current_count == 1
    assert details['patched'][0] == {
        'resource_dir': str(roblox_dir),
        'ca_file': str(ca_file),
        'changed': True,
    }
    assert any(
        'Seeded Roblox cacert.pem from healthy local bundle' in message
        for _category, message in logs
    )


def test_windows_env_global_ca_failure_defers_to_resolved_launch_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    assert getattr(proxy_master, '_env_proxy_global_ca_patch_failure_is_fatal')() is False

    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    assert getattr(proxy_master, '_env_proxy_global_ca_patch_failure_is_fatal')() is True


def test_windows_roblox_ca_patch_reseeds_fleasion_only_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs: list[tuple[str, str]] = []
    roblox_dir = tmp_path / 'Voidstrap' / 'RblxVersions' / 'version-bad'
    healthy_dir = tmp_path / 'Velostrap' / 'Versions' / 'version-good'
    ca_file = roblox_dir / 'ssl' / 'cacert.pem'
    healthy_ca_file = healthy_dir / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir(parents=True)
    healthy_ca_file.parent.mkdir(parents=True)

    ca_pem = _make_self_signed_ca_pem()
    base_root = _make_self_signed_ca_pem(common_name='Roblox Root A', organization='Roblox')
    base_root_2 = _make_self_signed_ca_pem(common_name='Roblox Root B', organization='Roblox')
    ca_file.write_text(ca_pem, encoding='utf-8')
    healthy_ca_file.write_text(base_root + base_root_2 + ('# padding\n' * 600), encoding='utf-8')

    pre_state = getattr(proxy_master, '_describe_cacert_state')(ca_file, ca_pem)
    assert pre_state['healthy'] is False
    assert pre_state['health_reason'] == 'bundle_too_small'
    assert pre_state['total_certs'] == 1
    assert pre_state['current_fleasion_certs'] == 1

    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(
        proxy_master,
        '_find_roblox_dirs',
        _callback_kwargs(lambda **_kwargs: [roblox_dir, healthy_dir]),
    )
    monkeypatch.setattr(
        certifi,
        'where',
        _callback0(
            lambda: (_ for _ in ()).throw(AssertionError('should prefer local healthy bundle'))
        ),
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    ok, details = getattr(proxy_master, '_install_ca_into_roblox')(ca_pem, include_studio=False)

    state = getattr(proxy_master, '_describe_cacert_state')(ca_file, ca_pem)
    assert ok is True
    assert state['healthy'] is True
    assert state['health_reason'] == 'healthy'
    assert state['total_certs'] >= 2
    assert state['current_fleasion_certs'] == 1
    assert details['failed'] == []
    assert any(
        'Seeded Roblox cacert.pem from healthy local bundle' in message
        for _category, message in logs
    )


def test_windows_resolved_launch_target_reseeds_even_if_not_discovered_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy_ca_dir = tmp_path / 'proxy-ca'
    proxy_ca_dir.mkdir()
    (proxy_ca_dir / 'ca.crt').write_text('placeholder', encoding='utf-8')

    target_dir = tmp_path / 'Fishstrap' / 'Versions' / 'version-active'
    healthy_dir = tmp_path / 'Velostrap' / 'Versions' / 'version-good'
    target_ca = target_dir / 'ssl' / 'cacert.pem'
    healthy_ca = healthy_dir / 'ssl' / 'cacert.pem'
    target_ca.parent.mkdir(parents=True)
    healthy_ca.parent.mkdir(parents=True)

    ca_pem = _make_self_signed_ca_pem()
    base_root = _make_self_signed_ca_pem(common_name='Roblox Root A', organization='Roblox')
    base_root_2 = _make_self_signed_ca_pem(common_name='Roblox Root B', organization='Roblox')
    target_ca.write_text(ca_pem, encoding='utf-8')
    healthy_ca.write_text(base_root + base_root_2 + ('# padding\n' * 600), encoding='utf-8')

    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(proxy_master, '_current_proxy_ca_dir', _callback0(lambda: proxy_ca_dir))
    monkeypatch.setattr(proxy_master, 'get_ca_pem', _callback1(lambda _path: ca_pem))
    monkeypatch.setattr(
        proxy_master, '_find_roblox_dirs', _callback_kwargs(lambda **_kwargs: [healthy_dir])
    )
    monkeypatch.setattr(
        certifi,
        'where',
        _callback0(
            lambda: (_ for _ in ()).throw(AssertionError('should prefer local healthy bundle'))
        ),
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_constant_callback(None)))

    changed = proxy_master.check_and_patch_running_roblox_ca(target_dir / 'RobloxPlayerBeta.exe')

    state = getattr(proxy_master, '_describe_cacert_state')(target_ca, ca_pem)
    assert changed is True
    assert state['healthy'] is True
    assert state['current_fleasion_certs'] == 1


def test_direct_cacert_upsert_clears_read_only_before_write(tmp_path: Path) -> None:
    ca_file = tmp_path / 'Roblox' / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir(parents=True)
    ca_file.write_text('MOZILLA ROOTS\n', encoding='utf-8')
    ca_file.chmod(0o444)
    ca_pem = '-----BEGIN CERTIFICATE-----\nCURRENT\n-----END CERTIFICATE-----\n'

    changed, fleasion_count, current_count = getattr(proxy_master, '_upsert_fleasion_ca_in_cacert')(
        ca_file, ca_pem
    )

    assert changed is True
    assert fleasion_count == 0
    assert current_count == 0
    assert ca_file.read_text(encoding='utf-8') == f'MOZILLA ROOTS\n{ca_pem}'
    assert not (ca_file.stat().st_mode & stat.S_IWRITE)


def test_cacert_write_barrier_clear_removes_immutable_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, int]] = []

    class FakePath:
        mode: int = 0o444

        def stat(self) -> SimpleNamespace:
            return SimpleNamespace(st_mode=self.mode, st_flags=0b1111)

        def is_dir(self) -> bool:
            return False

        def chmod(self, mode: int) -> None:
            self.mode = mode

    fake_path = FakePath()
    monkeypatch.setattr(proxy_master.stat, 'UF_IMMUTABLE', 0b0001, raising=False)
    monkeypatch.setattr(proxy_master.stat, 'UF_APPEND', 0b0010, raising=False)
    monkeypatch.setattr(proxy_master.stat, 'SF_IMMUTABLE', 0b0100, raising=False)
    monkeypatch.setattr(proxy_master.stat, 'SF_APPEND', 0b1000, raising=False)

    def record_chflags(path: object, flags: int) -> None:
        calls.append((path, flags))

    monkeypatch.setattr(proxy_master.os, 'chflags', record_chflags, raising=False)

    getattr(proxy_master, '_clear_cacert_write_barriers')(fake_path)

    assert calls == [(fake_path, 0)]
    assert fake_path.mode & stat.S_IWRITE


def test_linux_cacert_seed_clears_read_only_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs: list[tuple[str, str]] = []
    ca_file = tmp_path / 'asset_overlay' / 'ssl' / 'cacert.pem'
    source = tmp_path / 'healthy' / 'ssl' / 'cacert.pem'
    ca_file.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    ca_file.write_text('truncated', encoding='utf-8')
    source.write_text('replacement bundle', encoding='utf-8')
    ca_file.chmod(0o444)

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(
        proxy_master, '_healthy_cacert_source', _callback_args(lambda *_args: source)
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    seeded = getattr(proxy_master, '_seed_linux_cacert_if_needed')(
        ca_file,
        {'exists': True, 'size': 9, 'total_certs': 0, 'error': ''},
        'asset_overlay',
        'ca',
        [tmp_path / 'asset_overlay', tmp_path / 'healthy'],
    )

    assert seeded is True
    assert ca_file.read_text(encoding='utf-8') == 'replacement bundle'
    assert not (ca_file.stat().st_mode & stat.S_IWRITE)
    assert any(
        'Seeded Roblox cacert.pem from healthy local bundle' in message
        for _category, message in logs
    )


def test_linux_proxy_start_emits_read_only_hosts_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[str, dict[str, object]]] = []
    ca_cert = tmp_path / 'ca.crt'
    ca_key = tmp_path / 'ca.key'
    leaf_cert = tmp_path / 'leaf.crt'
    leaf_key = tmp_path / 'leaf.key'
    default_cert = (tmp_path / 'default.crt', tmp_path / 'default.key')
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text('x', encoding='utf-8')

    class _ProxyStub:
        async def log_upstream_self_test(self, _hosts: object) -> None:
            return None

        def set_module_interceptors(self, _interceptors: object) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    helper_error: dict[str, object] = {
        'ok': False,
        'code': 'linux_hosts_read_only',
        'error': "[Errno 30] Read-only file system: '/etc/hosts'",
        'hosts': ['assetdelivery.roblox.com', 'gamejoin.roblox.com'],
    }

    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))
    monkeypatch.setattr(proxy_master, 'generate_ca', _callback1(lambda _dir: (ca_cert, ca_key)))
    monkeypatch.setattr(
        proxy_master,
        'generate_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: (leaf_cert, leaf_key)),
    )
    monkeypatch.setattr(
        proxy_master,
        'generate_multi_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: default_cert),
    )
    monkeypatch.setattr(proxy_master, 'get_ca_pem', _callback1(lambda _path: 'ca'))
    monkeypatch.setattr(
        proxy_master,
        '_install_ca_into_roblox',
        _callback1_kwargs(lambda _pem, **_kwargs: (True, dict[str, object]())),
    )
    monkeypatch.setattr(proxy_master, '_resolve_real_endpoints', _callback1(lambda _hosts: {}))
    monkeypatch.setattr(
        proxy_master,
        '_run_tls_self_test',
        _callback_args_kwargs(lambda *_args, **_kwargs: asyncio.sleep(0, result=True)),
    )
    monkeypatch.setattr(
        proxy_master, 'FleasionProxy', _callback_kwargs(lambda **_kwargs: _ProxyStub())
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_ca_into_linux_trust',
        _callback_args_kwargs(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        linux_proxy_helper, 'linux_system_ca_needs_install', _callback1(lambda _path: False)
    )
    monkeypatch.setattr(
        linux_proxy_helper, 'start_helper', _callback_args_kwargs(lambda *_args, **_kwargs: False)
    )
    monkeypatch.setattr(
        linux_proxy_helper, 'last_start_error_details', _callback0(lambda: helper_error)
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(
        proxy,
        'config_manager',
        SimpleNamespace(
            clear_cache_on_launch=False,
            settings={},
            upstream_transport_mode='direct',
            vpn_compat_max_assetdelivery_connections=0,
            vpn_compat_max_cdn_connections=0,
        ),
    )
    setattr(proxy, 'cache_scraper', SimpleNamespace(set_real_ips=_callback1(lambda _ips: None)))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(proxy, '_module_interceptors', [])
    setattr(proxy, '_on_proxy_start_error', _collect_proxy_errors(errors))
    setattr(proxy, '_running', False)
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, '_loop', None)
    setattr(proxy, '_roblox_player_running', False)

    asyncio.run(getattr(proxy, '_run_proxy')())

    assert getattr(proxy, '_running') is False
    assert errors == [('linux_hosts_read_only', helper_error)]


def test_linux_helper_does_not_intercept_profile_api_when_spoofer_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(proxy, '_roblox_player_running', True)

    assert getattr(proxy, '_desired_intercept_hosts')() == set(proxy_master.BASE_INTERCEPT_HOSTS)


def test_linux_custom_fflags_wait_for_sober_engine_bootstrap_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils import platform_linux

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(
        proxy_master.ProxyMaster,
        '_sober_boottime',
        staticmethod(_callback0(lambda: 130.0)),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(proxy, 'custom_fflag_modifier', SimpleNamespace(is_enabled=_callback0(lambda: True)))
    installation = SimpleNamespace(
        key='sober',
        client=SimpleNamespace(clientsettings_route_delay_seconds=30.0),
    )
    setattr(proxy, '_active_linux_client_installation', installation)
    setattr(proxy, '_active_linux_client_key', 'sober')

    monkeypatch.setattr(
        platform_linux,
        'linux_client_main_process',
        _callback1(lambda _installation: (1001, 100.1)),
    )
    assert getattr(proxy, '_desired_intercept_hosts')() == set(proxy_master.BASE_INTERCEPT_HOSTS)

    monkeypatch.setattr(
        platform_linux,
        'linux_client_main_process',
        _callback1(lambda _installation: (1001, 100.0)),
    )
    assert getattr(proxy, '_desired_intercept_hosts')() == (
        set(proxy_master.BASE_INTERCEPT_HOSTS) | set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS)
    )

    # A quick close/reopen produces a new process identity and starts a fresh
    # bootstrap guard rather than inheriting the old process's elapsed time.
    monkeypatch.setattr(
        platform_linux,
        'linux_client_main_process',
        _callback1(lambda _installation: (1002, 129.9)),
    )
    assert getattr(proxy, '_desired_intercept_hosts')() == set(proxy_master.BASE_INTERCEPT_HOSTS)


def test_linux_env_proxy_exclusions_come_from_selected_descriptor() -> None:
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, '_active_linux_client_key', 'sober')
    setattr(
        proxy,
        '_active_linux_client_installation',
        SimpleNamespace(
            key='sober',
            client=SimpleNamespace(
                proxy_passthrough_hosts=frozenset({'bootstrap.example'}),
                clientsettings_route_delay_seconds=30.0,
            ),
        ),
    )

    assert getattr(proxy, '_linux_env_proxy_excluded_hosts')() == (
        {'bootstrap.example'} | set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS)
    )


def test_linux_sober_clientsettings_stays_tunneled_until_route_is_armed() -> None:
    excluded_updates: list[set[str]] = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(
        proxy,
        '_proxy',
        SimpleNamespace(set_intercept_excluded_hosts=_collect_host_sink(excluded_updates)),
    )
    setattr(proxy, '_env_proxy_intercept_excluded_hosts', {'sober.vinegarhq.org'})

    getattr(proxy, '_set_linux_sober_clientsettings_passthrough')(True)
    assert set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS) <= excluded_updates[-1]
    assert 'sober.vinegarhq.org' in excluded_updates[-1]

    getattr(proxy, '_set_linux_sober_clientsettings_passthrough')(False)
    assert not set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS) & excluded_updates[-1]
    assert 'sober.vinegarhq.org' in excluded_updates[-1]


def test_proxy_startup_self_tests_only_active_intercept_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleasion.utils.linux_clients import SOBER_CLIENT

    self_test_hosts: list[set[str]] = []
    override_calls: list[tuple[str, str]] = []
    logs: list[tuple[str, str]] = []
    ca_cert = tmp_path / 'ca.crt'
    ca_key = tmp_path / 'ca.key'
    leaf_cert = tmp_path / 'leaf.crt'
    leaf_key = tmp_path / 'leaf.key'
    default_cert = (tmp_path / 'default.crt', tmp_path / 'default.key')
    for path in (ca_cert, ca_key, leaf_cert, leaf_key, *default_cert):
        path.write_text('x', encoding='utf-8')

    class _TextureStripper:
        def __init__(self, _config: object) -> None:
            pass

        def set_cache_scraper(self, _scraper: object) -> None:
            pass

        def precheck_replacements(self) -> None:
            pass

    class _ProxyStub:
        port = proxy_master.MACOS_PROXY_BACKEND_PORT

        async def log_upstream_self_test(self, _hosts: object) -> None:
            pass

        def set_module_interceptors(self, _interceptors: object) -> None:
            pass

        def set_intercept_match(self, _match: object) -> None:
            pass

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        def clear_request_log(self) -> None:
            pass

        async def serve_forever(self) -> Never:
            raise asyncio.CancelledError

    async def tls_self_test(hosts: set[str], *_args: object, **_kwargs: object) -> bool:
        self_test_hosts.append(set(hosts))
        return True

    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(
        proxy_master,
        '_selected_linux_client_installation',
        _callback0(lambda: SimpleNamespace(key='sober', display_name='Sober', client=SOBER_CLIENT)),
    )
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: False))
    monkeypatch.setattr(proxy_master, 'generate_ca', _callback1(lambda _dir: (ca_cert, ca_key)))
    monkeypatch.setattr(
        proxy_master,
        'generate_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: (leaf_cert, leaf_key)),
    )
    monkeypatch.setattr(
        proxy_master,
        'generate_multi_host_cert',
        _callback_args_kwargs(lambda *_args, **_kwargs: default_cert),
    )
    monkeypatch.setattr(proxy_master, 'get_ca_pem', _callback1(lambda _path: 'ca'))
    monkeypatch.setattr(
        proxy_master,
        '_install_ca_into_roblox',
        _callback1_kwargs(lambda _pem, **_kwargs: (True, dict[str, object]())),
    )
    monkeypatch.setattr(proxy_master, '_other_proxy_owner_alive', _callback0(lambda: False))
    monkeypatch.setattr(
        proxy_master, '_remove_hosts_entries', _callback_args_kwargs(lambda *_args, **_kwargs: True)
    )
    monkeypatch.setattr(
        proxy_master, '_add_hosts_entries', _callback_args_kwargs(lambda *_args, **_kwargs: True)
    )
    monkeypatch.setattr(
        proxy_master, '_verify_hosts_entries', _callback_args_kwargs(lambda *_args, **_kwargs: True)
    )
    monkeypatch.setattr(proxy_master, '_flush_dns', _callback0(lambda: None))
    monkeypatch.setattr(proxy_master, '_resolve_real_endpoints', _callback1(lambda _hosts: {}))
    monkeypatch.setattr(
        proxy_master,
        'detect_windows_proxy',
        _callback0(
            lambda: SimpleNamespace(
                wininet_enabled=False,
                wininet_proxy_server='',
                wininet_auto_config_url='',
                winhttp_proxy_server='',
                macos_http_enabled=False,
                macos_https_enabled=False,
                macos_http_proxy_server='',
                macos_https_proxy_server='',
                macos_auto_config_url='',
            )
        ),
    )
    monkeypatch.setattr(proxy_master, 'detected_http_proxy', _callback1(lambda _info: None))
    monkeypatch.setattr(proxy_master, 'TextureStripper', _TextureStripper)
    monkeypatch.setattr(
        proxy_master, 'FleasionProxy', _callback_kwargs(lambda **_kwargs: _ProxyStub())
    )
    monkeypatch.setattr(proxy_master, '_run_tls_self_test', tls_self_test)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_start_watchdog', _callback1(lambda _self: None))
    monkeypatch.setattr(
        proxy_master.ProxyMaster,
        '_start_linux_sober_custom_fflag_timer',
        _callback1(lambda _self: None),
    )
    monkeypatch.setattr(
        'fleasion.utils.platform_linux.set_linux_client_env_proxy_override',
        _collect_env_overrides(override_calls),
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(
        proxy,
        'config_manager',
        SimpleNamespace(
            proxy_mode='env',
            custom_fflags_enabled=True,
            clear_cache_on_launch=False,
            settings={},
            upstream_transport_mode='direct',
            vpn_compat_max_assetdelivery_connections=0,
            vpn_compat_max_cdn_connections=0,
            wire_preserving_passthrough=False,
        ),
    )
    setattr(proxy, 'cache_scraper', SimpleNamespace(set_real_ips=_callback1(lambda _ips: None)))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(
        proxy,
        'custom_fflag_modifier',
        SimpleNamespace(
            is_enabled=_callback0(lambda: True),
            prime_windows_flag_cache=_callback0(lambda: False),
        ),
    )
    setattr(proxy, '_module_interceptors', [])
    setattr(proxy, '_on_proxy_start_error', _callback_args(lambda *_args: None))
    setattr(proxy, '_running', False)
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, '_loop', None)
    setattr(proxy, '_env_proxy_intercept_match', '')
    setattr(proxy, '_env_proxy_intercept_all', False)
    setattr(proxy, '_active_intercept_hosts', set())
    setattr(proxy, '_hosts_installed', False)
    setattr(proxy, '_active_env_proxy_mode', False)
    setattr(proxy, '_sober_env_proxy_override_active', False)
    setattr(proxy, '_sober_fflag_timer_stop', None)
    setattr(proxy, '_sober_fflag_timer_thread', None)
    monkeypatch.setattr(
        proxy_master.ProxyMaster,
        '_startup_intercept_hosts',
        _callback1(lambda _self: set(proxy_master.BASE_INTERCEPT_HOSTS)),
    )

    asyncio.run(getattr(proxy, '_run_proxy')())

    assert self_test_hosts == [set(proxy_master.BASE_INTERCEPT_HOSTS)]
    assert override_calls == [('http://127.0.0.1:58443', 'sober')]
    assert getattr(proxy, '_active_env_proxy_mode') is True
    assert getattr(proxy, '_sober_env_proxy_override_active') is True


def test_linux_startup_treats_manual_profile_api_hosts_entry_as_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text(
        '127.0.0.1 localhost\n127.0.0.1 apis.roblox.com\n',
        encoding='utf-8',
    )
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(proxy, '_roblox_player_running', True)

    assert getattr(proxy, '_startup_intercept_hosts')() == (
        set(proxy_master.BASE_INTERCEPT_HOSTS) | set(proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS)
    )
    assert any('existing Linux loopback hosts entries' in message for _category, message in logs)


def test_linux_system_trust_unsupported_does_not_block_profile_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ca = tmp_path / 'ca.crt'
    ca.write_text('ca', encoding='utf-8')
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_ca_into_linux_trust',
        _callback_args_kwargs(
            lambda *_args, **_kwargs: {
                'ok': False,
                'system': {'ok': False, 'error': 'no_supported_system_trust_store'},
                'nss': [],
            }
        ),
    )

    assert (
        getattr(proxy_master, '_ensure_linux_system_trust_for_hosts')(
            set(proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS),
            ca,
        )
        is True
    )
    assert any('unsupported on this distro' in message for _category, message in logs)


def test_linux_helper_intercepts_profile_api_when_spoofer_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: True)))
    setattr(proxy, '_roblox_player_running', True)

    assert getattr(proxy, '_desired_intercept_hosts')() == (
        set(proxy_master.BASE_INTERCEPT_HOSTS) | set(proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS)
    )


def test_linux_helper_refresh_requests_helper_update_without_direct_hosts_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []
    helper_updates: list[set[str]] = []
    endpoint_hosts: list[set[str]] = []

    class ProxyStub:
        def __init__(self) -> None:
            self.endpoints: _EndpointMap = {
                host: [proxy_master.UpstreamEndpoint(host=host, ip='203.0.113.1')]
                for host in proxy_master.BASE_INTERCEPT_HOSTS
            }

        def upstream_endpoints_for_hosts(self, hosts: Iterable[str]) -> _EndpointMap:
            return {host: self.endpoints[host] for host in hosts if host in self.endpoints}

        def set_upstream_endpoints(self, endpoints: _EndpointMap) -> None:
            self.endpoints = endpoints
            endpoint_hosts.append(set(endpoints))

    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))
    monkeypatch.setattr(
        proxy_master,
        '_add_hosts_entries',
        _callback_args_kwargs(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct add should not run')
            )
        ),
    )
    monkeypatch.setattr(
        proxy_master,
        '_remove_hosts_entries',
        _callback_args_kwargs(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct remove should not run')
            )
        ),
    )
    resolved_host_sets: list[set[str]] = []
    monkeypatch.setattr(
        proxy_master,
        '_resolve_real_endpoints',
        _resolve_empty_endpoints(resolved_host_sets),
    )
    monkeypatch.setattr(
        proxy_master,
        '_ensure_linux_system_trust_for_hosts',
        _callback_args_kwargs(lambda *_args, **_kwargs: True),
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    from fleasion.utils import linux_proxy_helper

    monkeypatch.setattr(
        linux_proxy_helper,
        'update_helper_hosts',
        _collect_host_updates(helper_updates),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: True)))
    setattr(proxy, '_roblox_player_running', False)
    setattr(proxy, '_active_intercept_hosts', set(proxy_master.BASE_INTERCEPT_HOSTS))
    setattr(proxy, '_hosts_installed', True)
    setattr(proxy, '_proxy', ProxyStub())
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, 'cache_scraper', SimpleNamespace(set_real_ips=_callback1(lambda _ips: None)))

    proxy.refresh_username_spoofer_interception()

    expected = set(proxy_master.BASE_INTERCEPT_HOSTS) | set(
        proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS
    )
    assert helper_updates == [expected]
    assert endpoint_hosts == [expected]
    assert resolved_host_sets == [set(proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS)]
    assert getattr(proxy, '_active_intercept_hosts') == expected
    assert any('Requested Linux helper intercept update' in message for _category, message in logs)


def test_linux_helper_custom_fflags_adds_only_clientsettings_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_updates: list[set[str]] = []
    resolved_host_sets: list[set[str]] = []

    class ProxyStub:
        def __init__(self) -> None:
            self.endpoints: _EndpointMap = {
                host: [proxy_master.UpstreamEndpoint(host=host, ip='203.0.113.1')]
                for host in proxy_master.BASE_INTERCEPT_HOSTS
            }

        def upstream_endpoints_for_hosts(self, hosts: Iterable[str]) -> _EndpointMap:
            return {host: self.endpoints[host] for host in hosts if host in self.endpoints}

        def set_upstream_endpoints(self, endpoints: _EndpointMap) -> None:
            self.endpoints = endpoints

    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))
    monkeypatch.setattr(
        proxy_master,
        '_resolve_real_endpoints',
        _resolve_empty_endpoints(resolved_host_sets),
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_constant_callback(None)))

    from fleasion.utils import linux_proxy_helper

    monkeypatch.setattr(
        linux_proxy_helper,
        'update_helper_hosts',
        _collect_host_updates(helper_updates),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(
        proxy,
        'custom_fflag_modifier',
        SimpleNamespace(
            is_enabled=_callback0(lambda: True),
            prime_windows_flag_cache=_callback0(lambda: False),
        ),
    )
    setattr(proxy, '_linux_sober_custom_fflag_routes_ready', _callback0(lambda: True))
    setattr(proxy, '_roblox_player_running', False)
    setattr(proxy, '_active_intercept_hosts', set(proxy_master.BASE_INTERCEPT_HOSTS))
    setattr(proxy, '_hosts_installed', True)
    setattr(proxy, '_proxy', ProxyStub())
    setattr(proxy, '_lock', threading.Lock())
    setattr(proxy, 'cache_scraper', SimpleNamespace(set_real_ips=_callback1(lambda _ips: None)))

    proxy.refresh_custom_fflag_interception()

    expected = set(proxy_master.BASE_INTERCEPT_HOSTS) | set(
        proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS
    )
    assert helper_updates == [expected]
    assert resolved_host_sets == [set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS)]
    assert set(getattr(proxy, '_proxy').endpoints) == expected


def test_intercept_configuration_log_distinguishes_tls_coverage_from_active_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'custom_fflag_modifier', SimpleNamespace(is_enabled=_callback0(lambda: False)))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: True)))

    getattr(proxy, '_log_intercept_configuration')(
        'Startup routing selection',
        set(proxy_master.BASE_INTERCEPT_HOSTS) | set(proxy_master.USERNAME_SPOOFER_INTERCEPT_HOSTS),
    )

    assert logs == [
        (
            'InterceptConfig',
            (
                'Startup routing selection: custom_fflags=disabled; '
                'clientsettings_intercepted=no; username_spoofer=enabled; '
                'profile_api_intercepted=yes; '
                'hosts=apis.roblox.com, assetdelivery.roblox.com, '
                'contentdelivery.roblox.com, fts.rbxcdn.com, gamejoin.roblox.com'
            ),
        )
    ]


def test_linux_helper_refresh_skips_profile_api_when_webview_trust_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []
    helper_updates: list[set[str]] = []

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, '_use_linux_privileged_helper', _callback0(lambda: True))
    monkeypatch.setattr(
        proxy_master,
        '_ensure_linux_system_trust_for_hosts',
        _callback_args_kwargs(lambda *_args, **_kwargs: False),
    )
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )

    from fleasion.utils import linux_proxy_helper

    monkeypatch.setattr(
        linux_proxy_helper,
        'update_helper_hosts',
        _collect_host_updates(helper_updates),
    )

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    setattr(proxy, 'username_spoofer', SimpleNamespace(is_enabled=_callback0(lambda: True)))
    setattr(proxy, '_roblox_player_running', False)
    setattr(proxy, '_active_intercept_hosts', set(proxy_master.BASE_INTERCEPT_HOSTS))
    setattr(proxy, '_hosts_installed', True)
    setattr(proxy, '_proxy', SimpleNamespace())
    setattr(proxy, '_lock', threading.Lock())

    proxy.refresh_username_spoofer_interception()

    assert helper_updates == []
    assert getattr(proxy, '_active_intercept_hosts') == set(proxy_master.BASE_INTERCEPT_HOSTS)
    assert any('system trust is not ready' in message for _category, message in logs)


def test_macos_studio_launch_skips_ca_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    (ca_dir / 'ca.crt').write_text(
        '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n',
        encoding='utf-8',
    )
    studio_exe = tmp_path / 'RobloxStudio.app' / 'Contents' / 'MacOS' / 'RobloxStudio'
    studio_exe.parent.mkdir(parents=True)
    studio_exe.write_text('stub', encoding='utf-8')
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(proxy_master, 'IS_MACOS', True)
    monkeypatch.setattr(proxy_master, '_current_proxy_ca_dir', _callback0(lambda: ca_dir))
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )
    monkeypatch.setattr(
        proxy_master,
        '_log_cacert_state',
        _callback_args_kwargs(
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('should not inspect Studio certs')
            )
        ),
    )
    monkeypatch.setattr(
        proxy_master,
        '_upsert_fleasion_ca_in_cacert',
        _callback_args_kwargs(
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('should not patch Studio certs')
            )
        ),
    )

    assert proxy_master.check_and_patch_running_roblox_ca(studio_exe) is False
    assert any('Skipping macOS Roblox Studio CA patch' in message for _category, message in logs)


def test_macos_running_player_ca_repair_uses_direct_write_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    (ca_dir / 'ca.crt').write_text('ca', encoding='utf-8')
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = tmp_path / 'Roblox.app' / 'Contents' / 'MacOS'
    ssl_dir = resources / 'ssl'
    macos.mkdir(parents=True)
    ssl_dir.mkdir(parents=True)
    exe_path = macos / 'RobloxPlayer'
    exe_path.write_text('stub', encoding='utf-8')
    ca_file = ssl_dir / 'cacert.pem'
    ca_file.write_text('MOZILLA ROOTS\n', encoding='utf-8')
    helper_calls: list[tuple[str, list[dict[str, object]]]] = []
    states = [
        {
            'exists': True,
            'healthy': False,
            'fleasion_certs': 0,
            'current_fleasion_certs': 0,
            'sha256': 'before',
        },
        {
            'exists': True,
            'healthy': True,
            'fleasion_certs': 1,
            'current_fleasion_certs': 1,
            'sha256': 'after',
        },
    ]

    def fake_helper_patch(ca_pem: str, installs: list[dict[str, object]]) -> dict[str, object]:
        helper_calls.append((ca_pem, installs))
        return {
            'ok': True,
            'patched': [{'resource_dir': str(resources), 'ca_file': str(ca_file), 'changed': True}],
            'skipped': [],
            'failed': [],
        }

    monkeypatch.setattr(proxy_master, 'IS_MACOS', True)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, '_is_admin', _callback0(lambda: False))
    monkeypatch.setattr(proxy_master, '_current_proxy_ca_dir', _callback0(lambda: ca_dir))
    monkeypatch.setattr(
        proxy_master,
        'get_ca_pem',
        _callback1(lambda _path: '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n'),
    )
    monkeypatch.setattr(
        'fleasion.utils.platform_macos._resource_root_from_executable',
        _callback1(lambda _path: resources),
    )
    monkeypatch.setattr(platform_macos, 'find_bootstrapper_restore_resource_dirs', _callback0(list))
    monkeypatch.setattr(macos_proxy_helper, 'helper_patch_ca', fake_helper_patch)
    monkeypatch.setattr(
        proxy_master,
        '_log_cacert_state',
        _callback_args_kwargs(lambda *_args, **_kwargs: states.pop(0)),
    )
    monkeypatch.setattr(
        proxy_master,
        '_upsert_fleasion_ca_in_cacert',
        _callback_args_kwargs(lambda *_args, **_kwargs: (True, 0, 0)),
    )

    assert proxy_master.check_and_patch_running_roblox_ca(exe_path) is True
    assert helper_calls == []


def test_macos_running_player_ca_repair_requests_full_strip_when_pre_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    (ca_dir / 'ca.crt').write_text('ca', encoding='utf-8')
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = tmp_path / 'Roblox.app' / 'Contents' / 'MacOS'
    ssl_dir = resources / 'ssl'
    macos.mkdir(parents=True)
    ssl_dir.mkdir(parents=True)
    exe_path = macos / 'RobloxPlayer'
    exe_path.write_text('stub', encoding='utf-8')
    (ssl_dir / 'cacert.pem').write_text('MOZILLA ROOTS\n', encoding='utf-8')
    helper_calls: list[tuple[str, list[dict[str, object]]]] = []
    original_read_text = proxy_master.Path.read_text

    def fake_helper_patch(ca_pem: str, installs: list[dict[str, object]]) -> dict[str, object]:
        helper_calls.append((ca_pem, installs))
        return {'ok': True, 'patched': [], 'skipped': [], 'failed': []}

    def fake_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        if self == ssl_dir / 'cacert.pem':
            msg = 'permission denied'
            raise OSError(msg)
        return original_read_text(self, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(proxy_master, 'IS_MACOS', True)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, '_is_admin', _callback0(lambda: False))
    monkeypatch.setattr(proxy_master, '_current_proxy_ca_dir', _callback0(lambda: ca_dir))
    monkeypatch.setattr(
        proxy_master,
        'get_ca_pem',
        _callback1(lambda _path: '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n'),
    )
    monkeypatch.setattr(
        'fleasion.utils.platform_macos._resource_root_from_executable',
        _callback1(lambda _path: resources),
    )
    monkeypatch.setattr(proxy_master.Path, 'read_text', fake_read_text)
    monkeypatch.setattr(macos_proxy_helper, 'helper_patch_ca', fake_helper_patch)

    request_ok, changed, details = getattr(proxy_master, '_patch_roblox_ca_with_macos_helper')(
        '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n',
        resources,
    )

    assert request_ok is True
    assert changed is False
    assert details['ok'] is True
    assert helper_calls == [
        (
            '-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n',
            [{'resource_dir': str(resources), 'remove_pems': [], 'strip_all_fleasion_ca': True}],
        )
    ]


def test_macos_system_keychain_removes_stale_fleasion_ca_before_current_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs: list[tuple[str, str]] = []
    calls: list[list[str]] = []
    ca_cert = tmp_path / 'ca.crt'
    ca_cert.write_text('ca', encoding='utf-8')
    stale_ca = _make_self_signed_ca_pem()
    current_ca = _make_self_signed_ca_pem()
    lookalike_ca = _make_self_signed_ca_pem(organization='Other Org')
    stale_thumbprint = getattr(proxy_master, '_ca_thumbprint_sha1')(stale_ca)
    lookalike_thumbprint = getattr(proxy_master, '_ca_thumbprint_sha1')(lookalike_ca)

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if args[:5] == ['security', 'find-certificate', '-a', '-p', '-c']:
            return SimpleNamespace(
                returncode=0,
                stdout=f'{stale_ca}\n{current_ca}\n{lookalike_ca}\n',
                stderr='',
            )
        if args[:3] == ['security', 'delete-certificate', '-Z']:
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        msg = f'unexpected security call: {args}'
        raise AssertionError(msg)

    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=_collect_log(logs)),
    )
    monkeypatch.setattr(proxy_master.subprocess, 'run', fake_run)

    getattr(proxy_master, '_install_ca_into_macos_system_keychain')(ca_cert, current_ca)

    assert [
        'security',
        'delete-certificate',
        '-Z',
        stale_thumbprint,
        '/Library/Keychains/System.keychain',
    ] in calls
    assert not any(isinstance(call, list) and lookalike_thumbprint in call for call in calls)
    assert not any('add-trusted-cert' in call for call in calls)
    assert any('removed 1 stale Fleasion CA entry' in message for _category, message in logs)


def test_proxy_find_roblox_dirs_ignores_invalid_registry_key_and_keeps_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Key:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    fake_winreg = SimpleNamespace(HKEY_CURRENT_USER=object(), REG_SZ=1)
    software_key = _Key()
    valid_key = _Key()
    valid_dir = Path('C:/ValidRoblox')
    valid_exe = str(valid_dir / proxy_master.ROBLOX_PROCESS)

    def open_key(root: object, name: str) -> _Key:
        if root is fake_winreg.HKEY_CURRENT_USER and name == r'Software':
            return software_key
        if root is software_key and name == 'ValidVendor':
            return valid_key
        if root is software_key and name == 'corrupt\x00key':
            msg = 'embedded null character'
            raise ValueError(msg)
        raise OSError

    def enum_key(key: _Key, index: int) -> str:
        if key is software_key:
            if index == 0:
                return 'corrupt\x00key'
            if index == 1:
                return 'ValidVendor'
        raise OSError

    def query_value_ex(key: _Key, name: str) -> tuple[str, int]:
        if key is valid_key and name == 'PlayerPath':
            return str(valid_dir / proxy_master.ROBLOX_PROCESS), fake_winreg.REG_SZ
        raise OSError

    fake_winreg.OpenKey = open_key
    fake_winreg.EnumKey = enum_key
    fake_winreg.QueryValueEx = query_value_ex

    monkeypatch.setitem(sys.modules, 'winreg', fake_winreg)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, 'IS_LINUX', False)
    monkeypatch.setattr(proxy_master, 'LOCAL_APPDATA', tmp_path)

    def isfile(value: object) -> bool:
        return value == valid_exe

    monkeypatch.setattr(proxy_master.os.path, 'isfile', isfile)
    monkeypatch.setattr(proxy_master, 'load_saved_roblox_dirs', _callback0(list))
    monkeypatch.setattr(proxy_master, 'get_roblox_player_exe_path', _callback0(lambda: None))
    monkeypatch.setattr(proxy_master, 'get_roblox_studio_exe_path', _callback0(lambda: None))
    monkeypatch.setattr(proxy_master.log_buffer, 'log', _callback_args(lambda *_args: None))

    assert getattr(proxy_master, '_find_roblox_dirs')() == [valid_dir]
