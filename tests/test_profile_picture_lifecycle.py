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
