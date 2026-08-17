from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from fleasion.proxy.traffic_archive import (
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_FILE_BYTES,
    MAX_ARCHIVED_MESSAGE_BYTES,
    ProxyTrafficArchive,
    sanitize_http_message,
)


class ConfigStub:
    def __init__(self, enabled: bool) -> None:
        self.proxy_traffic_preserve = enabled


def _entry(request_id: int, *, secret: str = 'ticket-secret') -> dict[str, Any]:
    return {
        'id': request_id,
        'time': 1_700_000_000 + request_id,
        'host': 'apis.roblox.com',
        'port': 443,
        'method': 'POST',
        'path': f'/v1/join?placeId={request_id}&accessCode={secret}',
        'intercepted': True,
        'status': 200,
        'size': 512,
        'ms': 18,
        'was_intercepted': True,
        'dropped_request': False,
        'dropped_response': False,
        'pending_stage': 'request',
        'request_raw': (
            f'POST /v1/join?accessCode={secret} HTTP/1.1\r\n'
            f'Authorization: Bearer bearer-secret\r\n'
            f'Cookie: .ROBLOSECURITY=cookie-secret\r\n'
            f'Content-Type: application/json\r\n\r\n'
            f'{{"authenticationTicket":"{secret}","placeId":{request_id}}}'
        ).encode(),
        'response_raw': (
            b'HTTP/1.1 200 OK\r\nSet-Cookie: session=server-secret\r\n\r\n'
            b'{"csrfToken":"response-secret","ready":true}'
        ),
    }


def _decoded_messages(path: Path) -> list[bytes]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    result: list[bytes] = []
    for entry in payload['entries']:
        for key in ('request_raw', 'response_raw'):
            if entry[key]:
                result.append(base64.b64decode(entry[key], validate=True))
    return result


def test_archive_round_trip_redacts_credentials_and_assigns_negative_ids(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    config = ConfigStub(True)
    archive = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]

    assert archive.save_snapshot([_entry(7)])
    raw_file = path.read_bytes()
    decoded = b'\n'.join(_decoded_messages(path))
    for secret in (
        b'ticket-secret',
        b'bearer-secret',
        b'cookie-secret',
        b'server-secret',
        b'response-secret',
    ):
        assert secret not in raw_file
        assert secret not in decoded
    assert b'<redacted>' in decoded

    restored = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]
    assert restored.count == 1
    entry = restored.entries()[0]
    assert entry['id'] == -1
    assert entry['pending_stage'] is None
    assert entry['archived'] is True
    assert entry['path'].endswith('accessCode=<redacted>')


def test_archive_checkpoint_keeps_archived_and_live_id_ranges_disjoint(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    config = ConfigStub(True)
    archive = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]

    assert archive.checkpoint([_entry(10), _entry(11)])
    assert [entry['id'] for entry in archive.entries()] == [-1, -2]
    combined = archive.combined([_entry(0)])
    assert [entry['id'] for entry in combined] == [-1, -2, 0]
    assert all(entry['pending_stage'] is None for entry in combined[:2])


def test_periodic_snapshots_keep_archive_ids_stable_as_live_rows_append(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    config = ConfigStub(True)
    archive = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]

    assert archive.save_snapshot([_entry(10), _entry(11)])
    first = json.loads(path.read_text(encoding='utf-8'))['entries']
    assert [entry['archive_id'] for entry in first] == [-1, -2]

    live = [_entry(10), _entry(11), _entry(12)]
    assert archive.save_snapshot(live)
    appended = json.loads(path.read_text(encoding='utf-8'))['entries']
    assert [entry['archive_id'] for entry in appended] == [-1, -2, -3]

    assert archive.checkpoint(live)
    assert [entry['id'] for entry in archive.entries()] == [-1, -2, -3]


def test_archive_bounds_count_payload_and_total_file_size(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    config = ConfigStub(True)
    archive = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]
    entries = [_entry(index, secret='safe') for index in range(MAX_ARCHIVE_ENTRIES + 5)]
    entries[-1]['response_raw'] = b'HTTP/1.1 200 OK\r\n\r\n' + b'x' * (
        MAX_ARCHIVED_MESSAGE_BYTES * 2
    )

    assert archive.checkpoint(entries)
    assert archive.count <= MAX_ARCHIVE_ENTRIES
    assert path.stat().st_size <= MAX_ARCHIVE_FILE_BYTES
    stored = json.loads(path.read_text(encoding='utf-8'))['entries'][-1]
    response = base64.b64decode(stored['response_raw'], validate=True)
    assert len(response) <= MAX_ARCHIVED_MESSAGE_BYTES


def test_archive_rejects_oversized_or_corrupt_input_and_rewrites_safely(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    config = ConfigStub(True)
    path.write_bytes(b'x' * (MAX_ARCHIVE_FILE_BYTES + 1))

    oversized = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]
    assert oversized.entries() == []
    assert json.loads(path.read_text(encoding='utf-8'))['entries'] == []

    path.write_text('{broken', encoding='utf-8')
    corrupt = ProxyTrafficArchive(config, path)  # pyright: ignore[reportArgumentType]
    assert corrupt.entries() == []
    assert json.loads(path.read_text(encoding='utf-8'))['entries'] == []


def test_disabling_archive_removes_stale_snapshot(tmp_path: Path):
    path = tmp_path / 'traffic.json'
    enabled = ConfigStub(True)
    archive = ProxyTrafficArchive(enabled, path)  # pyright: ignore[reportArgumentType]
    assert archive.save_snapshot([_entry(1)])
    assert path.exists()

    disabled = ConfigStub(False)
    ProxyTrafficArchive(disabled, path)  # pyright: ignore[reportArgumentType]
    assert not path.exists()


def test_headerless_json_and_binary_payloads_are_safe_to_archive():
    sanitized_json = sanitize_http_message(
        b'{"accessCode":"private-code","placeId":123}'
    )
    assert sanitized_json is not None
    assert b'private-code' not in sanitized_json
    assert b'<redacted>' in sanitized_json

    sanitized_binary = sanitize_http_message(b'\x00\xff\x80private-code')
    assert sanitized_binary == b'<binary payload omitted from preserved traffic>'
