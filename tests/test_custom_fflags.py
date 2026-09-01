import asyncio
import json
import threading
from types import SimpleNamespace

from fleasion.proxy.addons import custom_fflags as custom_fflags_module
from fleasion.proxy.addons.custom_fflags import (
    DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG,
    CustomFFlagModifier,
    normalize_custom_fflags,
)
from fleasion.proxy import master as proxy_master
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.proxy.server import (
    BASE_INTERCEPT_HOSTS,
    CUSTOM_FFLAGS_INTERCEPT_HOSTS,
    FleasionProxy,
    RawHeaders,
    _build_modified_response,
    _compress_dcz,
    _decompress_body,
    _decompress_dcz,
    _dcz_dictionary_sha256,
    _without_internal_client_settings_headers,
    _without_conditional_client_settings_headers,
)


class _BufferWriter:
    def __init__(self, *, fail_drain: bool = False):
        self.buffer = bytearray()
        self.closed = False
        self.fail_drain = fail_drain

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        if self.fail_drain:
            raise ConnectionResetError('test client disconnected before drain')

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


def _run_client_settings_session(
    modifier: CustomFFlagModifier,
    upstream_response: bytes,
    *,
    conditional: bool = False,
    on_connect=None,
    auto_replace_rules: list[dict] | None = None,
    intercept_response_action: str | None = None,
    client_drain_failure: bool = False,
) -> tuple[bytes, bytes]:
    async def _run() -> tuple[bytes, bytes]:
        host = 'clientsettings.roblox.com'
        path = '/v2/settings/application/PCDesktopClient'
        headers = {
            b'host': host.encode('ascii'),
            b'connection': b'close',
        }
        if conditional:
            headers[b'if-none-match'] = b'"cached"'

        request_lines = [
            f'GET {path} HTTP/1.1'.encode('ascii'),
            f'Host: {host}'.encode('ascii'),
            b'Connection: close',
        ]
        if conditional:
            request_lines.append(b'If-None-Match: "cached"')
        request_block = b'\r\n'.join(request_lines) + b'\r\n\r\n'
        first_request = RawHeaders(
            first_line=request_lines[0],
            headers=headers,
            raw_header_block=request_block,
        )

        client_reader = asyncio.StreamReader()
        client_reader.feed_eof()
        client_writer = _BufferWriter(fail_drain=client_drain_failure)
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_data(upstream_response)
        upstream_reader.feed_eof()
        upstream_writer = _BufferWriter()

        proxy = FleasionProxy.__new__(FleasionProxy)
        proxy.texture_stripper = SimpleNamespace(
            config_manager=SimpleNamespace(get_all_replacements=lambda: [])
        )
        proxy.cache_scraper = SimpleNamespace(enabled=False)
        proxy.custom_fflag_modifier = modifier
        proxy._auto_replace_rules = list(auto_replace_rules or [])
        proxy._module_interceptors = []
        proxy._wire_preserving_passthrough = False
        proxy._intercept_all_hosts = intercept_response_action is not None
        proxy._fallback_diagnostics_seen = set()
        proxy._client_settings_dictionary_cache = {}
        proxy._executor = None

        if intercept_response_action is not None:
            entry = {
                'id': 1,
                'status': None,
                'size': 0,
                'ms': None,
                'request_raw': None,
                'response_raw': None,
                'pending_stage': None,
                'was_intercepted': False,
            }
            proxy._intercept_match_text = 'clientsettings'
            proxy._pending = {}
            proxy._pending_lock = threading.Lock()
            proxy._record_request = lambda *_args, **_kwargs: entry

        async def _connect_upstream(_host):
            if on_connect is not None:
                on_connect()
            return UpstreamConnectResult(
                reader=upstream_reader,
                writer=upstream_writer,
                method='direct_ip',
                endpoint='192.0.2.1',
            )

        proxy._connect_upstream = _connect_upstream

        async def _resolve_intercepts() -> None:
            while (1, 'request') not in proxy.get_pending_intercepts():
                await asyncio.sleep(0)
            assert proxy.submit_pending(1, 'request', 'forward')
            while (1, 'response') not in proxy.get_pending_intercepts():
                await asyncio.sleep(0)
            assert proxy.submit_pending(1, 'response', intercept_response_action)

        if intercept_response_action is None:
            await proxy._http_session(first_request, client_reader, client_writer, host)
        else:
            await asyncio.gather(
                proxy._http_session(first_request, client_reader, client_writer, host),
                _resolve_intercepts(),
            )
        return bytes(client_writer.buffer), bytes(upstream_writer.buffer)

    return asyncio.run(_run())


def test_normalize_custom_fflags_matches_roblox_string_values():
    assert normalize_custom_fflags(
        {
            'DFIntTaskSchedulerTargetFps': 20,
            'FFlagExample': True,
            'DFFlagOther': False,
            'FStringValue': 'unchanged',
            'bad': ['nested'],
            '': 'empty name',
        }
    ) == {
        'DFIntTaskSchedulerTargetFps': '20',
        'FFlagExample': 'True',
        'DFFlagOther': 'False',
        'FStringValue': 'unchanged',
    }


def test_runtime_flags_skip_individually_disabled_custom_fflags():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagEnabled': 'True', 'FFlagDisabled': 'False'},
        custom_fflag_disabled=['FFlagDisabled'],
    )

    flags = CustomFFlagModifier(config).runtime_flags()

    assert flags['FFlagEnabled'] == 'True'
    assert 'FFlagDisabled' not in flags


def test_modifier_merges_all_platform_application_settings():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'DFIntTaskSchedulerTargetFps': '20'},
    )
    modifier = CustomFFlagModifier(config)
    original = json.dumps(
        {'applicationSettings': {'DFIntTaskSchedulerTargetFps': '60', 'Existing': 'True'}}
    ).encode()

    modified = json.loads(
        modifier.modify_response(
            '/v2/settings-compressed/application/PCDesktopClient.zst', original
        )
    )

    assert modified['applicationSettings']['DFIntTaskSchedulerTargetFps'] == '20'
    assert modified['applicationSettings'][DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    assert modified['applicationSettings']['Existing'] == 'True'
    android = json.loads(
        modifier.modify_response('/v2/settings/application/GoogleAndroidApp', original)
    )
    assert android['applicationSettings']['DFIntTaskSchedulerTargetFps'] == '20'
    assert modifier.modify_response('/v2/client-version/WindowsPlayer', original) == original


def test_modifier_always_enforces_fast_dynamic_reload_without_saving_it():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '120'},
    )
    modifier = CustomFFlagModifier(config)
    original = b'{"applicationSettings":{"Existing":"True"}}'

    modified = json.loads(
        modifier.modify_response('/v2/settings/application/PCDesktopClient', original)
    )

    assert modified['applicationSettings'][DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    assert config.custom_fflags[DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '120'


def test_modifier_primes_the_uncompressed_windows_flag_cache(tmp_path):
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'DFIntTaskSchedulerTargetFps': '37'},
    )
    cache_path = tmp_path / 'flag_cache.dat'
    cache_path.write_bytes(
        b'\x00\x00\x00\x00\x00'
        + json.dumps({'applicationSettings': {'Existing': 'True'}}).encode()
    )
    modifier = CustomFFlagModifier(config, flag_cache_path=cache_path)

    assert modifier.prime_windows_flag_cache()

    assert cache_path.read_bytes()[:5] == b'\x00\x00\x00\x00\x00'
    payload = json.loads(cache_path.read_bytes()[5:])
    assert payload['applicationSettings']['Existing'] == 'True'
    assert payload['applicationSettings']['DFIntTaskSchedulerTargetFps'] == '37'
    assert payload['applicationSettings'][DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    assert DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG not in config.custom_fflags


def test_modifier_removes_disabled_override_from_windows_flag_cache(tmp_path):
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagFleasionGateMarker': 'True'},
        custom_fflag_disabled=[],
    )
    cache_path = tmp_path / 'flag_cache.dat'
    cache_path.write_bytes(
        b'\x00\x00\x00\x00\x00'
        + json.dumps({'applicationSettings': {'Existing': 'True'}}).encode()
    )
    modifier = CustomFFlagModifier(config, flag_cache_path=cache_path)

    assert modifier.prime_windows_flag_cache()
    config.custom_fflag_disabled = ['FFlagFleasionGateMarker']
    assert modifier.prime_windows_flag_cache()

    payload = json.loads(cache_path.read_bytes()[5:])['applicationSettings']
    assert 'FFlagFleasionGateMarker' not in payload
    assert payload[DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'


def test_modifier_removes_all_saved_overrides_when_windows_feature_is_disabled(tmp_path):
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagFleasionGateMarker': 'True'},
        custom_fflag_disabled=[],
    )
    cache_path = tmp_path / 'flag_cache.dat'
    cache_path.write_bytes(
        b'\x00\x00\x00\x00\x00'
        + json.dumps({'applicationSettings': {'Existing': 'True'}}).encode()
    )
    modifier = CustomFFlagModifier(config, flag_cache_path=cache_path)

    assert modifier.prime_windows_flag_cache()
    config.custom_fflags_enabled = False
    assert modifier.prime_windows_flag_cache()

    payload = json.loads(cache_path.read_bytes()[5:])['applicationSettings']
    assert payload == {'Existing': 'True'}


def test_modifier_primes_macos_player_client_settings(tmp_path):
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'DFFlagDebugDrawBroadPhaseAABBs': 'True', 'FFlagExample': 'False'},
    )
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    settings = resources / 'ClientSettings' / 'ClientAppSettings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{"Existing": "True"}', encoding='utf-8')
    modifier = CustomFFlagModifier(config, macos_resource_dirs=[resources])

    assert modifier.prime_startup_flag_cache()

    payload = json.loads(settings.read_text(encoding='utf-8'))
    assert payload == {
        'Existing': 'True',
        'DFFlagDebugDrawBroadPhaseAABBs': 'True',
        'FFlagExample': 'False',
        DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
    }


def test_modifier_removes_previous_macos_seed_when_flags_change_or_disable(tmp_path):
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'FFlagExample': 'True'},
    )
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    settings = resources / 'ClientSettings' / 'ClientAppSettings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{"Existing": "True"}', encoding='utf-8')
    modifier = CustomFFlagModifier(config, macos_resource_dirs=[resources])

    assert modifier.prime_macos_client_settings()
    config.custom_fflags = {'FFlagExample': 'False'}
    assert modifier.prime_macos_client_settings()
    assert json.loads(settings.read_text(encoding='utf-8'))['FFlagExample'] == 'False'

    config.custom_fflags_enabled = False
    assert modifier.prime_macos_client_settings()
    assert json.loads(settings.read_text(encoding='utf-8')) == {'Existing': 'True'}


def test_modifier_does_not_replace_an_unknown_compressed_flag_cache(tmp_path):
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={})
    cache_path = tmp_path / 'flag_cache.dat'
    original = b'\x00\x00\x00\x00\x01compressed'
    cache_path.write_bytes(original)

    assert not CustomFFlagModifier(config, flag_cache_path=cache_path).prime_windows_flag_cache()
    assert cache_path.read_bytes() == original


def test_modifier_requests_fresh_responses_until_each_flag_set_is_delivered():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    assert modifier.requires_fresh_response()
    assert modifier.requires_fresh_response()

    modifier.note_response_success()
    assert not modifier.requires_fresh_response()

    config.custom_fflags['FFlagExample'] = 'False'
    assert modifier.requires_fresh_response()


def test_modifier_requests_a_fresh_response_again_after_player_relaunch():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    assert modifier.requires_fresh_response()
    modifier.note_response_success()
    assert not modifier.requires_fresh_response()

    modifier.prepare_for_player_launch()

    assert modifier.requires_fresh_response()


def test_late_response_from_previous_player_cannot_satisfy_new_launch():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    old_generation = modifier.delivery_generation()

    assert modifier.note_response_success(generation=old_generation)
    assert not modifier.requires_fresh_response()

    modifier.prepare_for_player_launch()
    assert modifier.requires_fresh_response()

    assert not modifier.note_response_success(generation=old_generation)
    assert modifier.requires_fresh_response()


def test_delivery_generation_check_and_commit_are_atomic(monkeypatch):
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    generation = modifier.delivery_generation()
    signature = modifier._flag_signature(modifier.runtime_flags())
    entered_commit = threading.Event()
    release_commit = threading.Event()
    prepare_done = threading.Event()
    results = []
    real_monotonic = custom_fflags_module.time.monotonic

    def blocking_monotonic():
        entered_commit.set()
        assert release_commit.wait(1.0)
        return real_monotonic()

    monkeypatch.setattr(custom_fflags_module.time, 'monotonic', blocking_monotonic)

    note_thread = threading.Thread(
        target=lambda: results.append(
            modifier.note_response_success(signature, generation=generation)
        )
    )
    note_thread.start()
    assert entered_commit.wait(1.0)

    def prepare_launch():
        modifier.prepare_for_player_launch()
        prepare_done.set()

    prepare_thread = threading.Thread(target=prepare_launch)
    prepare_thread.start()
    assert not prepare_done.wait(0.05)

    release_commit.set()
    note_thread.join(1.0)
    prepare_thread.join(1.0)

    assert not note_thread.is_alive()
    assert not prepare_thread.is_alive()
    assert results == [True]
    assert prepare_done.is_set()
    assert modifier.requires_fresh_response()


def test_modifier_reloads_saved_flags_without_restarting_the_proxy(tmp_path):
    settings_path = tmp_path / 'settings.json'
    settings_path.write_text(
        json.dumps(
            {
                'custom_fflags_enabled': True,
                'custom_fflags': {'DFFlagDebugDrawBroadPhaseAABBs': 'True'},
            }
        ),
        encoding='utf-8',
    )
    config = SimpleNamespace(custom_fflags_enabled=False, custom_fflags={})
    modifier = CustomFFlagModifier(config, settings_path=settings_path)

    assert modifier.is_enabled()
    assert modifier.runtime_flags()['DFFlagDebugDrawBroadPhaseAABBs'] == 'True'
    assert modifier.requires_fresh_response()
    modifier.note_response_success()
    assert not modifier.requires_fresh_response()

    settings_path.write_text(
        json.dumps(
            {
                'custom_fflags_enabled': True,
                'custom_fflags': {'DFFlagDebugDrawBroadPhaseAABBs': 'False'},
            }
        ),
        encoding='utf-8',
    )

    assert modifier.runtime_flags()['DFFlagDebugDrawBroadPhaseAABBs'] == 'False'
    assert modifier.requires_fresh_response()


def test_proxy_applies_custom_fflags_when_enabled_while_request_is_in_flight():
    config = SimpleNamespace(custom_fflags_enabled=False, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    client_response, _upstream_request = _run_client_settings_session(
        modifier,
        response,
        on_connect=lambda: setattr(config, 'custom_fflags_enabled', True),
    )

    delivered = json.loads(client_response.split(b'\r\n\r\n', 1)[1])['applicationSettings']
    assert delivered['FFlagExample'] == 'True'
    assert delivered[DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    assert not modifier.requires_fresh_response()


def test_proxy_logs_empty_2xx_client_settings_responses_as_delivery_failures():
    for status_line, status_code in ((b'200 OK', 200), (b'204 No Content', 204)):
        config = SimpleNamespace(
            custom_fflags_enabled=True,
            custom_fflags={'FFlagExample': 'True'},
        )
        modifier = CustomFFlagModifier(config)
        failures = []
        modifier.log_response_failure = lambda key, message: failures.append((key, message))

        _run_client_settings_session(
            modifier,
            b'HTTP/1.1 '
            + status_line
            + b'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n',
        )

        assert failures == [
            (
                'empty-success',
                f'ClientSettings upstream returned HTTP {status_code} with an empty body; '
                'response left unchanged',
            )
        ]
        assert modifier.requires_fresh_response()


def test_proxy_counts_already_correct_client_settings_response_as_success():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = json.dumps(
        {
            'applicationSettings': {
                'FFlagExample': 'True',
                DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
            }
        },
        separators=(',', ':'),
    ).encode('utf-8')
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    client_response, _upstream_request = _run_client_settings_session(modifier, response)

    assert client_response.endswith(body)
    assert not modifier.requires_fresh_response()


def test_failed_fresh_response_keeps_next_conditional_request_armed():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    _client_response, first_request = _run_client_settings_session(
        modifier,
        b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n',
        conditional=True,
    )
    assert b'if-none-match' not in first_request.lower()
    assert modifier.requires_fresh_response()

    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )
    _client_response, second_request = _run_client_settings_session(
        modifier,
        response,
        conditional=True,
    )

    assert b'if-none-match' not in second_request.lower()
    assert not modifier.requires_fresh_response()


def test_dropped_intercepted_client_settings_response_keeps_freshness_armed():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    client_response, _upstream_request = _run_client_settings_session(
        modifier,
        response,
        intercept_response_action='drop',
    )

    assert client_response == b''
    assert modifier.requires_fresh_response()


def test_forwarded_intercepted_client_settings_response_acknowledges_delivery():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    client_response, _upstream_request = _run_client_settings_session(
        modifier,
        response,
        intercept_response_action='forward',
    )

    delivered = json.loads(client_response.split(b'\r\n\r\n', 1)[1])['applicationSettings']
    assert delivered['FFlagExample'] == 'True'
    assert not modifier.requires_fresh_response()


def test_auto_replace_that_changes_injected_flag_keeps_freshness_armed():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )
    rules = [
        {
            'enabled': True,
            'direction': 'response',
            'type': 'plain',
            'host_filter': 'clientsettings',
            'match': '"FFlagExample":"True"',
            'replacement': '"FFlagExample":"False"',
        }
    ]

    client_response, _upstream_request = _run_client_settings_session(
        modifier,
        response,
        auto_replace_rules=rules,
    )

    delivered = json.loads(client_response.split(b'\r\n\r\n', 1)[1])['applicationSettings']
    assert delivered['FFlagExample'] == 'False'
    assert modifier.requires_fresh_response()


def test_client_disconnect_before_drain_keeps_freshness_armed():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    _run_client_settings_session(
        modifier,
        response,
        client_drain_failure=True,
    )

    assert modifier.requires_fresh_response()


def test_request_started_before_launch_generation_bump_cannot_satisfy_new_player():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    body = b'{"applicationSettings":{"Existing":"True"}}'
    response = (
        b'HTTP/1.1 200 OK\r\n'
        + f'Content-Length: {len(body)}\r\n'.encode('ascii')
        + b'Connection: close\r\n\r\n'
        + body
    )

    client_response, _upstream_request = _run_client_settings_session(
        modifier,
        response,
        on_connect=modifier.prepare_for_player_launch,
    )

    delivered = json.loads(client_response.split(b'\r\n\r\n', 1)[1])['applicationSettings']
    assert delivered['FFlagExample'] == 'True'
    assert modifier.requires_fresh_response()


def test_fresh_client_settings_request_strips_only_conditional_headers():
    original = {
        b'accept-encoding': b'dcz',
        b'if-none-match': b'\"old-etag\"',
        b'if-modified-since': b'last week',
    }

    assert _without_conditional_client_settings_headers(original) == {
        b'accept-encoding': b'dcz',
    }
    assert b'if-none-match' in original


def test_browser_bypass_header_is_never_sent_upstream():
    original = {
        b'accept-encoding': b'dcz',
        b'x-fleasion-bypass-custom-fflags': b'1',
    }

    assert _without_internal_client_settings_headers(original) == {b'accept-encoding': b'dcz'}
    assert b'x-fleasion-bypass-custom-fflags' in original


def test_modifier_passes_the_windows_bootstrapper_through_unchanged():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={'DFIntTaskSchedulerTargetFps': '20'},
    )
    modifier = CustomFFlagModifier(config)
    original = b'{"applicationSettings":{"Existing":"True"}}'

    assert not modifier.handles_path('/v2/settings/application/PCClientBootstrapper')
    assert (
        modifier.modify_response('/v2/settings/application/PCClientBootstrapper', original)
        is original
    )


def test_modifier_is_true_passthrough_when_disabled():
    config = SimpleNamespace(
        custom_fflags_enabled=False,
        custom_fflags={'DFIntTaskSchedulerTargetFps': '20'},
    )
    modifier = CustomFFlagModifier(config)
    original = b'{"applicationSettings":{"Existing":"True"}}'

    assert (
        modifier.modify_response('/v2/settings/application/PCDesktopClient', original)
        is original
    )


def test_modified_response_removes_body_integrity_headers():
    response = _build_modified_response(
        b'HTTP/1.1 200 OK',
        {
            b'content-type': b'application/json',
            b'content-encoding': b'zstd',
            b'x-signature-ed25519': b'original-signature',
            b'etag': b'original-etag',
            b'content-md5': b'original-md5',
        },
        b'{}',
    )
    head = response.split(b'\r\n\r\n', 1)[0].lower()

    assert b'content-encoding' not in head
    assert b'x-signature-ed25519' not in head
    assert b'etag' not in head
    assert b'content-md5' not in head
    assert b'content-length: 2' in head


def test_modified_dcz_response_retains_only_the_required_content_encoding():
    response = _build_modified_response(
        b'HTTP/1.1 200 OK',
        {
            b'content-type': b'application/json',
            b'content-encoding': b'dcz',
            b'x-signature-ed25519': b'original-signature',
            b'etag': b'original-etag',
        },
        b'compressed-with-a-shared-dictionary',
        content_encoding=b'dcz',
    )
    head = response.split(b'\r\n\r\n', 1)[0].lower()

    assert head.count(b'content-encoding: dcz') == 1
    assert b'x-signature-ed25519' not in head
    assert b'etag' not in head


def test_current_zstd_response_shape_can_be_decompressed_and_modified():
    import zstandard

    plain = b'{"applicationSettings":{"Existing":"True"}}'
    compressed = zstandard.ZstdCompressor().compress(plain)
    decoded = _decompress_body(compressed, {b'content-encoding': b'zstd'})

    assert decoded == plain


def test_dcz_round_trip_uses_the_client_dictionary_and_extracts_its_hash():
    dictionary = b'custom fast flag dictionary ' * 100
    plain = b'{"applicationSettings":{"FFlagDebugSkyGray":"True"}}'

    compressed = _compress_dcz(plain, dictionary)

    assert compressed is not None
    assert _decompress_dcz(compressed, dictionary) == plain
    assert _dcz_dictionary_sha256(
        '/v2/settings-compressed/application/GoogleAndroidApp/'
        '69341cc9f35ea6437489227f58455ee226e77c469204ec273eb3e4a05e2f947b.dcz?x=1'
    ) == '69341cc9f35ea6437489227f58455ee226e77c469204ec273eb3e4a05e2f947b'
    assert _dcz_dictionary_sha256('/v2/client-version/WindowsPlayer') is None


def test_windows_custom_fflags_intercept_clientsettings_before_player_starts(monkeypatch):
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)

    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.config_manager = SimpleNamespace(settings={})
    proxy.username_spoofer = SimpleNamespace(is_enabled=lambda: False)
    proxy.custom_fflag_modifier = SimpleNamespace(is_enabled=lambda: True)
    proxy._roblox_player_running = False

    assert proxy._desired_intercept_hosts() == (
        set(BASE_INTERCEPT_HOSTS) | set(CUSTOM_FFLAGS_INTERCEPT_HOSTS)
    )


def test_master_primes_custom_flag_cache_only_while_player_is_closed(monkeypatch):
    calls = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.custom_fflag_modifier = SimpleNamespace(
        is_enabled=lambda: True,
        prime_windows_flag_cache=lambda: calls.append('primed') or True,
    )

    monkeypatch.setattr(proxy_master, 'is_roblox_running', lambda: False)
    assert proxy.prime_custom_fflag_cache()
    assert calls == ['primed']

    monkeypatch.setattr(proxy_master, 'is_roblox_running', lambda: True)
    assert not proxy.prime_custom_fflag_cache()
    assert calls == ['primed']


def test_master_launch_preparation_seeds_startup_flags_before_relaunch(monkeypatch):
    calls = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.custom_fflag_modifier = SimpleNamespace(
        is_enabled=lambda: True,
        prepare_for_player_launch=lambda: calls.append('armed'),
        prime_startup_flag_cache=lambda: calls.append('seeded') or True,
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=lambda *_args: None))

    proxy.prepare_custom_fflags_for_player_launch()

    assert calls == ['armed', 'seeded']


def test_master_launch_preparation_cleans_startup_cache_when_custom_flags_are_off(monkeypatch):
    calls = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.custom_fflag_modifier = SimpleNamespace(
        is_enabled=lambda: False,
        prepare_for_player_launch=lambda: calls.append('armed'),
        prime_startup_flag_cache=lambda: calls.append('seeded') or True,
    )

    proxy.prepare_custom_fflags_for_player_launch()

    assert calls == ['seeded']


def test_master_rearms_delivery_after_outgoing_player_stops():
    calls = []
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy.custom_fflag_modifier = SimpleNamespace(
        is_enabled=lambda: True,
        prepare_for_player_launch=lambda: calls.append('rearmed'),
    )

    proxy.rearm_custom_fflag_delivery_for_player_launch()

    assert calls == ['rearmed']


def test_successful_client_settings_injection_does_not_log_per_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(custom_fflags_module.log_buffer, 'log', lambda *args: calls.append(args))
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)
    original = b'{"applicationSettings":{"Existing":"True"}}'

    modified = modifier.modify_response('/v2/settings/application/PCDesktopClient', original)

    assert modified != original
    assert calls == []


def test_repeated_client_settings_failures_are_rate_limited_and_report_stall(monkeypatch):
    calls = []
    now = [100.0]
    monkeypatch.setattr(custom_fflags_module.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(custom_fflags_module.log_buffer, 'log', lambda *args: calls.append(args))
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    bad = b'not-json'
    modifier.modify_response('/v2/settings/application/PCDesktopClient', bad)
    now[0] = 101.0
    modifier.modify_response('/v2/settings/application/PCDesktopClient', bad)
    now[0] = 131.0
    modifier.modify_response('/v2/settings/application/PCDesktopClient', bad)

    assert len(calls) == 2
    assert 'Could not decode ClientSettings response' in calls[0][1]
    assert 'no successfully delivered ClientSettings response for 31s' in calls[1][1]


def test_successful_client_settings_response_resets_failure_stall_timer(monkeypatch):
    calls = []
    now = [200.0]
    monkeypatch.setattr(custom_fflags_module.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(custom_fflags_module.log_buffer, 'log', lambda *args: calls.append(args))
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    modifier.log_response_failure('decode', 'first failure')
    now[0] = 220.0
    modifier.note_response_success()
    now[0] = 225.0
    modifier.log_response_failure('decode', 'second failure')

    assert len(calls) == 2
    assert calls[1][1] == 'second failure'


def test_runtime_flags_skip_members_of_disabled_fastflag_folders():
    config = SimpleNamespace(
        custom_fflags_enabled=True,
        custom_fflags={
            'FFlagFolderMember': 'True',
            'FFlagStillEnabled': 'False',
        },
        custom_fflag_disabled=[],
        custom_fflag_folders={'Visual': ['FFlagFolderMember']},
        custom_fflag_disabled_folders=['Visual'],
    )

    flags = CustomFFlagModifier(config).runtime_flags()

    assert 'FFlagFolderMember' not in flags
    assert flags['FFlagStillEnabled'] == 'False'
