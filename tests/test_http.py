import importlib.util
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _load_http_module():
    path = Path(__file__).resolve().parents[1] / 'src' / 'Fleasion' / 'utils' / 'http.py'
    spec = importlib.util.spec_from_file_location('fleasion_http_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_https_record_layer_failure_retries_with_tls12(monkeypatch):
    http = _load_http_module()
    http._tls12_context.cache_clear()
    http._certifi_tls12_context.cache_clear()
    calls = []
    response = object()

    def fake_urlopen(req, timeout, context=None):
        calls.append(context)
        if context is None:
            raise urllib.error.URLError(
                ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
            )
        return response

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(http, '_certifi_tls12_context', lambda: None)

    req = urllib.request.Request('https://file.garden/example.obj')

    assert http._open_verified(req, 'https://file.garden/example.obj', 30) is response
    assert calls[0] is None
    assert calls[1].minimum_version is ssl.TLSVersion.TLSv1_2
    assert calls[1].maximum_version is ssl.TLSVersion.TLSv1_2


def test_certificate_failure_without_certifi_reraises_original(monkeypatch):
    http = _load_http_module()
    original = urllib.error.URLError(
        ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED')
    )

    def fake_urlopen(req, timeout, context=None):
        raise original

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(http, '_certifi_context', lambda: None)

    req = urllib.request.Request('https://example.test/file')

    with pytest.raises(urllib.error.URLError) as exc_info:
        http._open_verified(req, 'https://example.test/file', 30)

    assert exc_info.value is original


def test_http_download_to_uses_curl_fallback_after_urllib_failure(monkeypatch, tmp_path):
    http = _load_http_module()
    original = urllib.error.URLError(
        ssl.SSLError('[SSL: RECORD_LAYER_FAILURE] record layer failure')
    )
    dest = tmp_path / 'asset.obj'
    calls = {}

    def fake_urlopen(req, timeout, context=None):
        raise original

    def fake_run(cmd, capture_output, check, text):
        calls['cmd'] = cmd
        calls['capture_output'] = capture_output
        calls['check'] = check
        calls['text'] = text
        Path(cmd[cmd.index('--output') + 1]).write_bytes(b'from curl')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(http.shutil, 'which', lambda name: '/usr/bin/curl')
    monkeypatch.setattr(http.subprocess, 'run', fake_run)

    http.http_download_to(
        'https://file.garden/example.obj',
        dest,
        timeout=30,
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'},
    )

    assert dest.read_bytes() == b'from curl'
    assert calls['cmd'][:8] == [
        '/usr/bin/curl',
        '--fail',
        '--location',
        '--silent',
        '--show-error',
        '--max-time',
        '30',
        '--output',
    ]
    assert '--user-agent' in calls['cmd']
    assert 'Mozilla/5.0' in calls['cmd']
    assert '--header' in calls['cmd']
    assert 'Accept: */*' in calls['cmd']
    assert calls['cmd'][-1] == 'https://file.garden/example.obj'
    assert calls['capture_output'] is True
    assert calls['check'] is False
    assert calls['text'] is True
