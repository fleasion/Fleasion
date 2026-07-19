import json
from types import SimpleNamespace

from fleasion.proxy.addons.custom_fflags import (
    DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG,
    CustomFFlagModifier,
    normalize_custom_fflags,
)
from fleasion.proxy import master as proxy_master
from fleasion.proxy.server import (
    BASE_INTERCEPT_HOSTS,
    CUSTOM_FFLAGS_INTERCEPT_HOSTS,
    _build_modified_response,
    _compress_dcz,
    _decompress_body,
    _decompress_dcz,
    _dcz_dictionary_sha256,
    _without_internal_client_settings_headers,
    _without_conditional_client_settings_headers,
)


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


def test_modifier_does_not_replace_an_unknown_compressed_flag_cache(tmp_path):
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={})
    cache_path = tmp_path / 'flag_cache.dat'
    original = b'\x00\x00\x00\x00\x01compressed'
    cache_path.write_bytes(original)

    assert not CustomFFlagModifier(config, flag_cache_path=cache_path).prime_windows_flag_cache()
    assert cache_path.read_bytes() == original


def test_modifier_requests_one_fresh_response_for_each_flag_set():
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    modifier = CustomFFlagModifier(config)

    assert modifier.requires_fresh_response()
    assert not modifier.requires_fresh_response()

    config.custom_fflags['FFlagExample'] = 'False'
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
