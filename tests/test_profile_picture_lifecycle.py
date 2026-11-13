"""Regression coverage for profile pictures during proxy restarts and live imports."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fleasion.proxy import master as proxy_module
from fleasion.proxy.addons.profile_picture_spoofer import (
    PFP_ROUTE_HOST,
    ProfilePictureSpoofer,
    clear_local_images,
    lookup_local_image,
    register_local_image,
)
from fleasion.proxy.server import ProxyFlow, _FlowResponse  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fleasion.utils.json_types import JsonObject


@pytest.fixture(autouse=True)
def clear_routes_between_tests() -> Iterator[None]:
    clear_local_images()
    yield
    clear_local_images()


def _restartable_master(
    spoofer: ProfilePictureSpoofer, started: threading.Event
) -> proxy_module.ProxyMaster:
    """Build a proxy whose actual lifecycle methods run without changing system routing."""
    master = proxy_module.ProxyMaster.__new__(proxy_module.ProxyMaster)
    master._lock = threading.Lock()  # pyright: ignore[reportPrivateUsage]
    master._running = False  # pyright: ignore[reportPrivateUsage]
    master._thread = None  # pyright: ignore[reportPrivateUsage]
    master._env_proxy_ready = threading.Event()  # pyright: ignore[reportPrivateUsage]
    master._hosts_proxy_ready = threading.Event()  # pyright: ignore[reportPrivateUsage]
    master._texture_stripper = None  # pyright: ignore[reportPrivateUsage]
    master.profile_picture_spoofer = spoofer
    master._stop_linux_sober_custom_fflag_timer = lambda: None  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    master._cleanup_linux_client_proxy_state = lambda: None  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    master._run_proxy_worker = started.set  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    return master


def _thumbnail_for(user_id: int) -> ProxyFlow:
    flow = ProxyFlow(
        f'GET /v1/users/avatar-headshot?userIds={user_id} HTTP/1.1'.encode(),
        {},
        b'',
        'thumbnails.roblox.com',
    )
    payload = {'data': [{'targetId': user_id, 'imageUrl': 'upstream'}]}
    flow.response = _FlowResponse(b'HTTP/1.1 200 OK', json.dumps(payload).encode())
    return flow


def test_selected_me_identity_survives_proxy_stop_and_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_module, 'IS_MACOS', False)
    spoofer = ProfilePictureSpoofer()
    spoofer.set_entries(
        [{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/profile.png'}]
    )
    spoofer.set_current_user('100')
    started = threading.Event()
    master = _restartable_master(spoofer, started)
    before = _thumbnail_for(100)
    spoofer.response(before)
    assert before.response is not None
    assert json.loads(before.response.content)['data'][0]['imageUrl'].endswith('profile.png')
    master.stop()
    assert spoofer.current_user_id() == '100'
    master.start()
    assert started.wait(timeout=2)
    assert spoofer.current_user_id() == '100'
    after = _thumbnail_for(100)
    spoofer.response(after)
    assert after.response is not None
    assert json.loads(after.response.content)['data'][0]['imageUrl'].endswith('profile.png')
    # A late startup identity must not replace the account selected in the GUI
    spoofer.adopt_initial_account('200')
    assert spoofer.current_user_id() == '100'


def test_advertised_local_image_survives_proxy_restart_but_not_final_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_module, 'IS_MACOS', False)
    file = tmp_path / 'picture.png'
    file.write_bytes(b'png')
    url = register_local_image(file)
    route = url.removeprefix(f'https://{PFP_ROUTE_HOST}')
    spoofer = ProfilePictureSpoofer()
    master = _restartable_master(spoofer, threading.Event())
    assert lookup_local_image(PFP_ROUTE_HOST, route) == str(file.resolve())
    master.stop()
    assert lookup_local_image(PFP_ROUTE_HOST, route) == str(file.resolve())
    master.start()
    assert lookup_local_image(PFP_ROUTE_HOST, route) == str(file.resolve())
    # Application exit, unlike a mode switch, explicitly releases these URLs
    spoofer.shutdown()
    assert lookup_local_image(PFP_ROUTE_HOST, route) is None


def test_saved_config_load_cannot_overwrite_concurrent_gui_update() -> None:
    read_started = threading.Event()
    finish_read = threading.Event()

    class SlowConfig:
        @property
        def profile_picture_entries(self) -> list[JsonObject]:
            read_started.set()
            assert finish_read.wait(timeout=3)
            return [{'enabled': True, 'target': '1', 'image_url': 'https://example.com/old.png'}]

    spoofer = ProfilePictureSpoofer(SlowConfig())
    completed = threading.Event()

    def load_saved() -> None:
        spoofer.is_enabled()
        completed.set()

    worker = threading.Thread(target=load_saved, daemon=True)
    worker.start()
    try:
        assert read_started.wait(timeout=2)
        spoofer.set_entries(
            [{'enabled': True, 'target': '2', 'image_url': 'https://example.com/new.png'}]
        )
    finally:
        finish_read.set()
        worker.join(timeout=2)
    assert completed.is_set()
    with spoofer._lock:  # pyright: ignore[reportPrivateUsage]
        assert [entry['target'] for entry in spoofer._entries] == ['2']  # pyright: ignore[reportPrivateUsage]
    assert spoofer.is_enabled()


def test_concurrent_initial_readers_wait_until_configuration_is_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second is_enabled() must never observe an incomplete initial load."""
    read_started = threading.Event()
    release_read = threading.Event()
    waiting_for_load = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()
    results: dict[str, bool] = {}

    class SlowConfig:
        @property
        def profile_picture_entries(self) -> list[JsonObject]:
            read_started.set()
            assert release_read.wait(timeout=3)
            return [{'enabled': True, 'target': '1', 'image_url': 'https://example.com/yes.png'}]

    spoofer = ProfilePictureSpoofer(SlowConfig())
    condition = spoofer._config_condition  # pyright: ignore[reportPrivateUsage]
    original_wait = condition.wait

    def record_wait(timeout: float | None = None) -> bool:
        waiting_for_load.set()
        return original_wait(timeout)

    monkeypatch.setattr(condition, 'wait', record_wait)

    def first_reader() -> None:
        results['first'] = spoofer.is_enabled()
        first_done.set()

    def second_reader() -> None:
        results['second'] = spoofer.is_enabled()
        second_done.set()

    first = threading.Thread(target=first_reader, daemon=True)
    second = threading.Thread(target=second_reader, daemon=True)
    first.start()
    try:
        assert read_started.wait(timeout=2)
        with spoofer._lock:  # pyright: ignore[reportPrivateUsage]
            assert not spoofer._config_loaded  # pyright: ignore[reportPrivateUsage]
            assert spoofer._config_loading  # pyright: ignore[reportPrivateUsage]
        second.start()
        assert waiting_for_load.wait(timeout=2)
        assert not second_done.is_set()
    finally:
        release_read.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)

    assert first_done.is_set()
    assert second_done.is_set()
    assert results == {'first': True, 'second': True}


def test_concurrent_reader_uses_gui_snapshot_even_if_initial_disk_read_is_pending() -> None:
    """A GUI update should release waiting readers without waiting for disk I/O."""
    read_started = threading.Event()
    release_read = threading.Event()
    results: list[bool] = []

    class SlowConfig:
        @property
        def profile_picture_entries(self) -> list[JsonObject]:
            read_started.set()
            assert release_read.wait(timeout=3)
            return [{'enabled': False, 'target': '1', 'image_url': 'https://example.com/old.png'}]

    spoofer = ProfilePictureSpoofer(SlowConfig())
    first = threading.Thread(target=spoofer.is_enabled, daemon=True)
    first.start()
    try:
        assert read_started.wait(timeout=2)
        spoofer.set_entries(
            [{'enabled': True, 'target': '2', 'image_url': 'https://example.com/new.png'}]
        )
        # The completed GUI snapshot is available while the disk getter is blocked
        results.append(spoofer.is_enabled())
    finally:
        release_read.set()
        first.join(timeout=2)

    assert results == [True]
    assert spoofer.is_enabled()
    with spoofer._lock:  # pyright: ignore[reportPrivateUsage]
        assert [entry['target'] for entry in spoofer._entries] == ['2']  # pyright: ignore[reportPrivateUsage]
