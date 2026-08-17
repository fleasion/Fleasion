from __future__ import annotations

import base64
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl

from fleasion.prejsons import PresetValue
from fleasion.qml_api.community_presets import CommunityPresetsApi
from fleasion.qml_api.community_value_preview import CommunityValueResolver
from fleasion.qml_api.payload_preview import PayloadPreviewApi, PreviewPayload

_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)
_DOCUMENT_XML = b'''<roblox version="4">
<Item class="Folder" referent="RBXRoot">
  <Properties><string name="Name">Root</string></Properties>
</Item>
</roblox>'''
_MESH = (
    b'version 1.00\n'
    b'1\n'
    b'[0,0,0][0,1,0][0,0,0]'
    b'[1,0,0][0,1,0][1,0,0]'
    b'[0,1,0][0,1,0][0,1,0]'
)
_ANIMATION_XML = b'''<roblox version="4">
<Item class="KeyframeSequence" referent="RBXSequence">
  <Properties><string name="Name">Walk</string></Properties>
  <Item class="Keyframe" referent="RBXKey">
    <Properties><float name="Time">0.5</float></Properties>
    <Item class="Pose" referent="RBXPose">
      <Properties>
        <string name="Name">Torso</string>
        <CoordinateFrame name="CFrame">
          <X>0</X><Y>0</Y><Z>0</Z>
          <R00>1</R00><R01>0</R01><R02>0</R02>
          <R10>0</R10><R11>1</R11><R12>0</R12>
          <R20>0</R20><R21>0</R21><R22>1</R22>
        </CoordinateFrame>
      </Properties>
    </Item>
  </Item>
</Item>
</roblox>'''
_TEXTURE_PACK_XML = b'''<?xml version="1.0"?>
<roblox><color>123</color><normal>456</normal></roblox>'''


def _application() -> QCoreApplication:
    current = QCoreApplication.instance()
    return current if current is not None else QCoreApplication([])


def _wait_for_task(task: Any, timeout: float = 3.0) -> None:
    application = _application()
    deadline = time.monotonic() + timeout
    idle_passes = 0
    while time.monotonic() < deadline:
        application.processEvents()
        idle_passes = idle_passes + 1 if not task.busy else 0
        if idle_passes >= 2:
            break
        time.sleep(0.005)
    assert not task.busy


def test_payload_preview_matches_asset_preview_contract_and_routes_rich_payloads() -> None:
    _application()
    preview = PayloadPreviewApi()  # pyright: ignore[reportCallIssue]
    try:
        assert isinstance(preview.task, QObject)
        assert isinstance(preview.fontPreview, QObject)
        assert isinstance(preview.jsonPreview, QObject)
        assert isinstance(preview.documentPreview, QObject)
        assert isinstance(preview.animationPreview, QObject)
        assert isinstance(preview.texturePackPreview, QObject)
        assert preview.meshGeometry is None

        preview.load_payload(PreviewPayload(b'{"nested":{"asset":123}}', label='Data'))
        assert preview.previewKind == 'json'
        assert preview.jsonPreview.model.count == 2  # pyright: ignore[reportAttributeAccessIssue]

        preview.load_payload(PreviewPayload(_DOCUMENT_XML, label='Model'))
        assert preview.previewKind == 'document'
        assert preview.documentPreview.treeModel.count == 1  # pyright: ignore[reportAttributeAccessIssue]

        preview.load_payload(PreviewPayload(_MESH, label='Mesh'))
        assert preview.previewKind == 'mesh'
        assert preview.meshGeometry is not None

        preview.load_payload(PreviewPayload(b'\x00\x81\xffbinary'))
        assert preview.previewKind == 'hex'
        assert '00000000' in preview.previewText
    finally:
        preview.shutdown()


def test_payload_preview_materializes_images_and_removes_temporary_files() -> None:
    _application()
    preview = PayloadPreviewApi()  # pyright: ignore[reportCallIssue]
    preview.load_payload(PreviewPayload(_PNG, label='Pixel'))
    path = Path(QUrl(preview.previewSource).toLocalFile())
    assert preview.previewKind == 'image'
    assert preview.canCopyImage
    assert path.read_bytes() == _PNG

    preview.clear()

    assert preview.previewKind == 'none'
    assert not path.exists()
    preview.shutdown()


def test_payload_preview_routes_audio_animation_and_texture_pack() -> None:
    _application()
    preview = PayloadPreviewApi()  # pyright: ignore[reportCallIssue]
    try:
        preview.load_payload(PreviewPayload(b'OggS\x00preview', label='Sound'))
        audio_path = Path(QUrl(preview.previewSource).toLocalFile())
        assert preview.previewKind == 'audio'
        assert audio_path.suffix == '.ogg'

        preview.load_payload(
            PreviewPayload(_ANIMATION_XML, label='Walk', asset_id='24', asset_type=24)
        )
        assert preview.previewKind == 'animation'
        assert preview.animationPreview.keyframeCount == 1  # pyright: ignore[reportAttributeAccessIssue]

        preview.load_payload(
            PreviewPayload(
                _TEXTURE_PACK_XML,
                label='Pack',
                asset_id='900',
                asset_type=63,
            )
        )
        assert preview.previewKind == 'texturepack'
        assert preview.texturePackPreview.model.count == 2  # pyright: ignore[reportAttributeAccessIssue]
        assert preview.texturePackPreview.set_map_bytes('123', _PNG)  # pyright: ignore[reportAttributeAccessIssue]
        assert preview.texturePackPreview.model.get(0)['imageSource'].startswith(  # pyright: ignore[reportAttributeAccessIssue]
            'file:'
        )
    finally:
        preview.shutdown()


def test_payload_preview_replaces_a_cancelled_load_without_publishing_stale_bytes() -> None:
    _application()
    preview = PayloadPreviewApi()  # pyright: ignore[reportCallIssue]
    entered = threading.Event()

    def first(cancel_event: threading.Event) -> PreviewPayload:
        entered.set()
        cancel_event.wait(timeout=1)
        return PreviewPayload(b'{"stale":true}', label='stale')

    try:
        assert preview.load_async('First', first)
        assert entered.wait(1)
        assert preview.load_async(
            'Second',
            lambda _cancel_event: PreviewPayload(b'{"fresh":true}', label='fresh'),
        )
        _wait_for_task(preview.task)
        deadline = time.monotonic() + 1
        while preview.previewKind != 'json' and time.monotonic() < deadline:
            _application().processEvents()
            time.sleep(0.005)
        assert preview.previewKind == 'json'
        assert preview.sourceLabel == 'fresh'
        assert preview.jsonPreview.model.count == 1  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        preview.shutdown()


class _CacheStub:
    def list_assets(self) -> list[dict[str, object]]:
        return [
            {
                'id': '123',
                'type': 1,
                'type_name': 'Image',
                'resolved_name': 'Cached pixel',
            }
        ]

    @staticmethod
    def get_asset(asset_id: str, asset_type: int) -> bytes | None:
        return _PNG if (asset_id, asset_type) == ('123', 1) else None

    @staticmethod
    def get_asset_type_name(_asset_type: int) -> str:
        return 'Image'


class _CookieJarStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def set(self, name: str, value: str, **kwargs: object) -> None:
        self.calls.append((name, value, kwargs))


class _ResponseStub:
    def __init__(
        self,
        status: int,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b'',
    ) -> None:
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self._body = body

    def __enter__(self) -> _ResponseStub:
        return self

    def __exit__(self, *_args: object) -> None:
        return

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        return [
            self._body[offset : offset + chunk_size]
            for offset in range(0, len(self._body), chunk_size)
        ]


class _SessionStub:
    def __init__(self) -> None:
        self.trust_env = True
        self.proxies: dict[str, str] = {'https': 'unexpected'}
        self.headers: dict[str, str] = {}
        self.cookies = _CookieJarStub()
        self.requests: list[str] = []
        self.closed = False

    def get(self, url: str, **_kwargs: object) -> _ResponseStub:
        self.requests.append(url)
        if len(self.requests) == 1:
            return _ResponseStub(
                302,
                url,
                headers={'Location': 'https://cdn.rbxcdn.com/pixel.png'},
            )
        return _ResponseStub(200, url, body=_PNG)

    def close(self) -> None:
        self.closed = True


def test_community_resolver_prefers_existing_cache_for_asset_ids() -> None:
    resolver = CommunityValueResolver(_CacheStub())  # pyright: ignore[reportArgumentType]
    result = resolver.resolve(
        PresetValue('row', 'Images › Icon', 'Icon', 123, 'id'),
        threading.Event(),
    )

    assert result.data == _PNG
    assert result.asset_id == '123'
    assert result.asset_type == 1
    assert result.type_name == 'Image'
    assert result.source_kind == 'Cached Roblox asset'


def test_roblox_fetch_scopes_cookie_and_validates_every_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.qml_api import community_value_preview as resolver_module

    session = _SessionStub()
    validated: list[str] = []
    monkeypatch.setattr(
        resolver_module,
        'validate_public_https_url',
        lambda value: validated.append(value) or value,
    )
    resolver = CommunityValueResolver(
        cookie_reader=lambda: 'secret-cookie',
        session_factory=lambda: session,
    )

    result = resolver.resolve_asset_id('123', threading.Event())

    assert result.data == _PNG
    assert session.trust_env is False
    assert session.proxies == {}
    assert session.cookies.calls == [
        (
            '.ROBLOSECURITY',
            'secret-cookie',
            {'domain': '.roblox.com', 'path': '/', 'secure': True},
        )
    ]
    assert 'Cookie' not in session.headers
    assert validated == session.requests
    assert session.closed
    assert 'secret-cookie' not in repr(result)


def test_community_resolver_fetches_public_https_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.qml_api import community_value_preview as resolver_module

    calls: list[dict[str, object]] = []

    def fetch(url: str, **kwargs: object) -> bytes:
        calls.append({'url': url, **kwargs})
        return b'{"ok":true}'

    monkeypatch.setattr(resolver_module, 'validate_public_https_url', lambda value: value)
    resolver = CommunityValueResolver(public_fetch=fetch)
    result = resolver.resolve(
        PresetValue(
            'row',
            'Files › Data',
            'Data',
            'https://cdn.example/data.json',
            'url',
        ),
        threading.Event(),
    )

    assert result.data == b'{"ok":true}'
    assert result.type_name == 'Json'
    assert len(calls) == 1
    assert calls[0]['url'] == 'https://cdn.example/data.json'
    assert calls[0]['timeout'] == 15
    assert calls[0]['max_bytes'] == 64 * 1024 * 1024
    assert isinstance(calls[0]['cancel_event'], threading.Event)
    assert 'headers' not in calls[0]


def test_community_resolver_rejects_symlinks_and_oversized_local_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / 'target.json'
    target.write_text('{"ok":true}', encoding='utf-8')
    symlink = tmp_path / 'link.json'
    symlink.symlink_to(target)
    resolver = CommunityValueResolver()

    with pytest.raises(ValueError, match='Symbolic links'):
        resolver.resolve(
            PresetValue('link', 'Link', 'Link', str(symlink), 'path'),
            threading.Event(),
        )

    oversized = tmp_path / 'large.bin'
    with oversized.open('wb') as handle:
        handle.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='64 MB'):
        resolver.resolve(
            PresetValue('large', 'Large', 'Large', str(oversized), 'path'),
            threading.Event(),
        )


def test_exactly_one_selected_community_leaf_drives_and_clears_preview(
    tmp_path: Path,
) -> None:
    _application()
    source = tmp_path / 'preview.json'
    source.write_text('{"asset":123}', encoding='utf-8')
    api = CommunityPresetsApi()  # pyright: ignore[reportCallIssue]
    first = PresetValue('first', 'Files › Data', 'Data', str(source), 'path')
    second = PresetValue('second', 'Images › Icon', 'Icon', 123, 'id')
    api._values = [first, second]
    try:
        api.valueSelection.setSelected('first', True)  # pyright: ignore[reportAttributeAccessIssue]
        _wait_for_task(api.valuePreview.task)  # pyright: ignore[reportAttributeAccessIssue]
        assert api.selectedCount == 1
        assert api.selectedValuePath == 'Files › Data'
        assert api.valuePreview.previewKind == 'json'  # pyright: ignore[reportAttributeAccessIssue]

        api.valueSelection.setSelected('second', True)  # pyright: ignore[reportAttributeAccessIssue]
        assert api.selectedCount == 2
        assert api.valuePreview.previewKind == 'none'  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        api.shutdown()
