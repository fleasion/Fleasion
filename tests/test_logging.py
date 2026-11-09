import shutil
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

import fleasion.utils.logging as fleasion_logging


def test_log_file_rotates_at_one_mib_before_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / 'logs'
    log_file = logs_dir / 'fleasion.log'
    logs_dir.mkdir()
    log_file.write_bytes(b'x' * fleasion_logging.MAX_LOG_FILE_BYTES)

    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', logs_dir)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', log_file)

    buffer = fleasion_logging.LogBuffer()
    buffer.log('Test', 'after rotation')

    rotated = logs_dir / 'fleasion.log.1'
    assert rotated.exists()
    assert rotated.stat().st_size == fleasion_logging.MAX_LOG_FILE_BYTES
    assert log_file.stat().st_size < fleasion_logging.MAX_LOG_FILE_BYTES
    assert 'after rotation' in log_file.read_text(encoding='utf-8')


def test_logging_recreates_a_removed_log_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / 'logs'
    log_file = logs_dir / 'fleasion.log'
    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', logs_dir)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', log_file)
    buffer = fleasion_logging.LogBuffer()
    buffer.log('Test', 'before removal')
    shutil.rmtree(logs_dir)
    buffer.log('Test', 'after removal')
    assert 'after removal' in log_file.read_text(encoding='utf-8')
    assert len(buffer.get_all()) == 2


def test_log_notifications_skip_threads_without_listeners_and_preserve_batch_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', tmp_path)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', tmp_path / 'fleasion.log')
    clock = [10.0]
    monkeypatch.setattr(fleasion_logging, 'monotonic', lambda: clock[0])
    buffer = fleasion_logging.LogBuffer()
    callback = Mock()
    with patch.object(fleasion_logging.threading, 'Timer') as timer:
        buffer.log('Test', 'first')
        timer.assert_not_called()
        clock[0] += 0.01
        buffer.add_callback(callback)
        assert timer.call_count == 1
        assert timer.call_args.args[0] == pytest.approx(0.04)
        buffer.log('Test', 'second')
        assert timer.call_count == 1
        buffer._notify_callbacks()  # pyright: ignore[reportPrivateUsage]
        callback.assert_called_once_with()
        buffer.remove_callback(callback)
        buffer.log('Test', 'third')
        assert timer.call_count == 1
        clock[0] += 0.06
        buffer.add_callback(callback)
        assert timer.call_count == 1
        buffer.log('Test', 'fourth')
        assert timer.call_count == 2


def test_log_callback_can_log_again_without_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', tmp_path)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', tmp_path / 'fleasion.log')
    buffer = fleasion_logging.LogBuffer()
    notified = Event()

    def callback() -> None:
        buffer.remove_callback(callback)
        buffer.log('Test', 'from callback')
        notified.set()

    buffer.add_callback(callback)
    buffer.log('Test', 'first')
    assert notified.wait(timeout=2.0)
    assert 'from callback' in buffer.get_all()[-1]


def test_get_since_returns_a_snapshot_from_the_previous_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleasion_logging, 'LOGS_DIR', tmp_path)
    monkeypatch.setattr(fleasion_logging, 'LOG_FILE', tmp_path / 'fleasion.log')
    buffer = fleasion_logging.LogBuffer()
    buffer.log('Test', 'first')
    count, first = buffer.get_since(0)
    assert count == 1 and len(first) == 1 and 'first' in first[0]
    assert buffer.get_since(count) == (1, [])
    buffer.log('Test', 'second')
    next_count, second = buffer.get_since(count)
    assert next_count == 2 and len(second) == 1 and 'second' in second[0]
    assert len(first) == 1
