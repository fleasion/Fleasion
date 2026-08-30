from collections.abc import Callable
from pathlib import Path
from typing import Never, cast

import pytest

from fleasion.proxy import master as proxy_master

type ErrorDetails = dict[str, object]


def _write_hosts_file(content: str) -> None:
    callback = cast('Callable[[str], None]', proxy_master.__dict__['_write_hosts_file'])
    callback(content)


def _add_hosts_entries(hosts: set[str], details: ErrorDetails | None = None) -> bool:
    callback = cast(
        'Callable[[set[str], ErrorDetails | None], bool]',
        proxy_master.__dict__['_add_hosts_entries'],
    )
    return callback(hosts, details)


def _verify_hosts_entries(hosts: set[str], details: ErrorDetails | None = None) -> bool:
    callback = cast(
        'Callable[[set[str], ErrorDetails | None], bool]',
        proxy_master.__dict__['_verify_hosts_entries'],
    )
    return callback(hosts, details)


def _remove_hosts_entries(hosts: set[str], details: ErrorDetails | None = None) -> bool:
    callback = cast(
        'Callable[[set[str], ErrorDetails | None], bool]',
        proxy_master.__dict__['_remove_hosts_entries'],
    )
    return callback(hosts, details)


def _record_log(values: list[tuple[str, str]]) -> Callable[[str, str], None]:
    def record(category: str, message: str) -> None:
        values.append((category, message))

    return record


def _fail_read_text(*_args: object, **_kwargs: object) -> Never:
    msg = 'oversized file was read'
    raise AssertionError(msg)


def test_windows_hosts_writer_creates_missing_immediate_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_root = tmp_path / 'SystemRoot'
    drivers_dir = hosts_root / 'System32' / 'drivers'
    drivers_dir.mkdir(parents=True)
    hosts_file = drivers_dir / 'etc' / 'hosts'

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)

    _write_hosts_file('127.0.0.1 assetdelivery.roblox.com\n')

    assert hosts_file.parent.is_dir()
    assert hosts_file.read_text(encoding='utf-8') == '127.0.0.1 assetdelivery.roblox.com\n'


def test_windows_hosts_entries_include_ipv4_and_ipv6_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('127.0.0.1 localhost\n', encoding='utf-8')
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, '_HOSTS_ACTIVE_LOOPBACK_IPS', None)
    monkeypatch.setattr(proxy_master.log_buffer, 'log', _record_log(logs))

    assert _add_hosts_entries({'assetdelivery.roblox.com'})
    content = hosts_file.read_text(encoding='utf-8')

    assert '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry' in content
    assert '::1 assetdelivery.roblox.com # Fleasion proxy entry' in content
    assert _verify_hosts_entries({'assetdelivery.roblox.com'})


def test_hosts_cleanup_removes_ipv4_and_ipv6_loopback_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text(
        '127.0.0.1 localhost\n'
        '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\n'
        '::1 assetdelivery.roblox.com # Fleasion proxy entry\n'
        '::1 gamejoin.roblox.com\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)

    assert _remove_hosts_entries({'assetdelivery.roblox.com', 'gamejoin.roblox.com'})
    content = hosts_file.read_text(encoding='utf-8')

    assert 'assetdelivery.roblox.com' not in content
    assert 'gamejoin.roblox.com' not in content
    assert '127.0.0.1 localhost' in content


def test_stale_hosts_detection_is_read_only_and_scoped_to_fleasion_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text(
        '127.0.0.1 localhost\n'
        '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\n'
        '127.0.0.1 unrelated.example\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)

    assert proxy_master.has_stale_hosts_entries({'assetdelivery.roblox.com'})
    assert hosts_file.read_text(encoding='utf-8').count('Fleasion proxy entry') == 1


def test_stale_hosts_detection_ignores_unrelated_loopback_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('127.0.0.1 unrelated.example\n', encoding='utf-8')

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)

    assert not proxy_master.has_stale_hosts_entries({'assetdelivery.roblox.com'})


def test_hosts_writer_removes_only_voidstrap_gu_acc_entries_for_requested_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text(
        '128.116.54.3 assetdelivery.roblox.com #gu_acc\n'
        '#gu_acc127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\n'
        '128.116.54.3 unrelated.example #gu_acc\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, '_HOSTS_ACTIVE_LOOPBACK_IPS', None)

    assert _add_hosts_entries({'assetdelivery.roblox.com'})
    content = hosts_file.read_text(encoding='utf-8')

    assert '128.116.54.3 assetdelivery.roblox.com #gu_acc' not in content
    assert '#gu_acc127.0.0.1 assetdelivery.roblox.com' not in content
    assert '128.116.54.3 unrelated.example #gu_acc' in content
    assert '127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry' in content
    assert '::1 assetdelivery.roblox.com # Fleasion proxy entry' in content


def test_oversized_hosts_file_is_rejected_before_whole_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_bytes(b'\n' * 64)

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 16)
    monkeypatch.setattr(
        Path,
        'read_text',
        _fail_read_text,
    )

    details: ErrorDetails = {}
    assert not _add_hosts_entries({'assetdelivery.roblox.com'}, details)
    assert details['error_code'] == 'hosts_file_too_large'
    assert details['hosts_size_bytes'] == 64


def test_repair_hosts_file_streams_blank_lines_and_preserves_user_mappings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_bytes(
        b'\r\n' * 64
        + b'# VM-managed mapping\r\n'
        + b'10.0.0.2 vm-host\r\n'
        + b'127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\r\n'
        + b'::1 assetdelivery.roblox.com # Fleasion proxy entry\r\n'
        + b'128.116.54.3 assetdelivery.roblox.com #gu_acc\r\n'
        + b'128.116.54.3 unrelated.example #gu_acc\r\n'
    )

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 100)

    details: ErrorDetails = {}
    assert proxy_master.repair_hosts_file({'assetdelivery.roblox.com'}, details)
    repaired = hosts_file.read_bytes()

    assert b'\r\n\r\n' not in repaired
    assert b'# VM-managed mapping\r\n' in repaired
    assert b'10.0.0.2 vm-host\r\n' in repaired
    assert b'assetdelivery.roblox.com' not in repaired
    assert b'128.116.54.3 unrelated.example #gu_acc\r\n' in repaired
    assert details['repair_succeeded'] is True
    assert details['backup_deleted'] is True
    backup_path = cast('str', details['backup_path'])
    assert not Path(backup_path).exists()


def test_repair_hosts_file_preserves_original_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_bytes(
        b'\r\n' * 64
        + b'10.0.0.2 vm-host\r\n'
        + b'127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\r\n'
    )
    hosts_file.chmod(0o644)

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 100)

    assert proxy_master.repair_hosts_file({'assetdelivery.roblox.com'})
    assert hosts_file.stat().st_mode & 0o777 == 0o644


def test_remove_hosts_entries_repairs_oversized_file_even_if_user_content_remains_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_bytes(
        b'10.0.0.2 vm-host-with-a-long-name\n'
        b'127.0.0.1 assetdelivery.roblox.com # Fleasion proxy entry\n'
    )

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 16)

    assert _remove_hosts_entries({'assetdelivery.roblox.com'})
    content = hosts_file.read_bytes()
    assert b'vm-host-with-a-long-name' in content
    assert b'Fleasion proxy entry' not in content


def test_hosts_writer_rejects_append_that_would_cross_safety_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('10.0.0.2 vm-host\n', encoding='utf-8')

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, '_HOSTS_ACTIVE_LOOPBACK_IPS', None)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 40)

    details: ErrorDetails = {}
    assert not _add_hosts_entries({'assetdelivery.roblox.com'}, details)
    assert details['error_code'] == 'hosts_entries_would_exceed_limit'
    assert 'Fleasion proxy entry' not in hosts_file.read_text(encoding='utf-8')


def test_repair_hosts_file_refuses_output_that_remains_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_bytes(b'10.0.0.2 vm-host\n')

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 8)

    details: ErrorDetails = {}
    assert not proxy_master.repair_hosts_file({'assetdelivery.roblox.com'}, details)
    assert details['error_code'] == 'hosts_file_repair_failed'
    assert hosts_file.read_bytes() == b'10.0.0.2 vm-host\n'


def test_repair_hosts_file_spills_unterminated_long_lines_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    long_line = b'legitimate-entry-' + (b'x' * (3 * 1024 * 1024))
    hosts_file.write_bytes(long_line)

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 16)

    details: ErrorDetails = {}
    assert proxy_master.repair_hosts_file(
        {'assetdelivery.roblox.com'}, details, require_safe_size=False
    )
    assert hosts_file.read_bytes() == long_line
    assert details['repair_output_oversized'] is True


def test_candidate_hosts_overflow_is_not_reported_as_repairable_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts_file = tmp_path / 'hosts'
    hosts_file.write_text('10.0.0.2 vm-host\n', encoding='utf-8')

    monkeypatch.setattr(proxy_master, 'HOSTS_FILE', hosts_file)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(proxy_master, '_HOSTS_ACTIVE_LOOPBACK_IPS', None)
    monkeypatch.setattr(proxy_master, '_HOSTS_FILE_REPAIR_THRESHOLD_BYTES', 40)

    details: ErrorDetails = {}
    assert not _add_hosts_entries({'assetdelivery.roblox.com'}, details)
    assert details['error_code'] == 'hosts_entries_would_exceed_limit'
