"""TCP resets during an intentional Player exit are not proxy failures."""

from __future__ import annotations

# These tests exercise the private error-classification boundary.
# pyright: reportPrivateUsage=false

import asyncio

import pytest

from fleasion.proxy import server

@pytest.mark.parametrize('exc', [ConnectionResetError('Connection lost'), BrokenPipeError(32, 'broken pipe'), ConnectionAbortedError(103, 'aborted')])
def test_normal_player_exit_suppresses_connection_teardown(
    exc: OSError, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, '_roblox_player_running_for_disconnect', lambda: False)
    monkeypatch.setattr(server.asyncio, 'sleep', _no_sleep)
    assert asyncio.run(server._was_splice_disconnected_by_player_exit(exc))


def test_disconnect_while_player_still_running_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, '_roblox_player_running_for_disconnect', lambda: True)
    monkeypatch.setattr(server.asyncio, 'sleep', _no_sleep)
    assert not asyncio.run(
        server._was_splice_disconnected_by_player_exit(ConnectionResetError(104, 'reset'))
    )


@pytest.mark.parametrize('exc', [ValueError('bad ciphertext'), TimeoutError('key lookup'), OSError('network unavailable')])
def test_real_splice_failures_still_report_without_process_probe(
    exc: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> bool:
        raise AssertionError('No process probe required for protocol or timeout errors')

    monkeypatch.setattr(server, '_roblox_player_running_for_disconnect', forbidden)
    assert not asyncio.run(server._was_splice_disconnected_by_player_exit(exc))


def test_process_query_failure_preserves_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> bool:
        raise OSError('process query unavailable')

    monkeypatch.setattr(server, '_roblox_player_running_for_disconnect', unavailable)
    monkeypatch.setattr(server.asyncio, 'sleep', _no_sleep)
    assert not asyncio.run(server._was_splice_disconnected_by_player_exit(ConnectionResetError()))


async def _no_sleep(_duration: float) -> None:
    return
