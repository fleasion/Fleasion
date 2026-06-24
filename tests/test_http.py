import importlib.util
import ssl
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
