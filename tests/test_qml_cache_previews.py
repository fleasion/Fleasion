from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QUrl
from PySide6.QtGui import QFontDatabase

from fleasion.cache.roblox_document import classify_roblox_document
from fleasion.qml_api.animation_preview import AnimationPreviewApi
from fleasion.qml_api.cache import CacheApi
from fleasion.qml_api.font_preview import FontPreviewApi
from fleasion.qml_api.roblox_document_preview import RobloxDocumentPreviewApi

DOCUMENT_XML = b"""<roblox version="4">
<Item class="Folder" referent="RBXRoot">
  <Properties>
    <string name="Name">Root</string>
    <int name="ArchivableCount">4</int>
  </Properties>
  <Item class="Part" referent="RBXChild">
    <Properties>
      <string name="Name">Child</string>
      <bool name="Anchored">true</bool>
      <Vector3 name="Size"><X>1</X><Y>2</Y><Z>3</Z></Vector3>
    </Properties>
  </Item>
</Item>
</roblox>"""


def _pose_xml(name: str, x: float) -> str:
    return f"""<Item class="Pose" referent="RBX{name.replace(' ', '')}{x}">
<Properties>
  <CoordinateFrame name="CFrame">
    <X>{x}</X><Y>0</Y><Z>0</Z>
    <R00>1</R00><R01>0</R01><R02>0</R02>
    <R10>0</R10><R11>1</R11><R12>0</R12>
    <R20>0</R20><R21>0</R21><R22>1</R22>
  </CoordinateFrame>
  <string name="Name">{name}</string>
  <float name="Weight">1</float>
</Properties>
</Item>"""


def _animation_xml() -> bytes:
    return f"""<roblox version="4">
<Item class="KeyframeSequence" referent="RBXSequence">
<Properties><string name="Name">Walk</string></Properties>
<Item class="Keyframe" referent="RBXKey0">
<Properties><float name="Time">0</float></Properties>
{_pose_xml('HumanoidRootPart', 0)}
{_pose_xml('Torso', 0)}
</Item>
<Item class="Keyframe" referent="RBXKey1">
<Properties><float name="Time">1.25</float></Properties>
{_pose_xml('HumanoidRootPart', 2)}
{_pose_xml('Torso', 1)}
</Item>
</Item>
</roblox>""".encode()


def _wait_for_task(task: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while task.busy and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert not task.busy


class _PreviewCache:
    def __init__(self) -> None:
        self.index = {'assets': {}}
        self.config_manager = None
        self.payloads = {
            ('font', 74): b'OTTOfont-data',
            ('model', 10): DOCUMENT_XML,
            ('walk', 24): _animation_xml(),
        }

    def list_assets(self) -> list[dict[str, object]]:
        return [
            {
                'id': asset_id,
                'type': asset_type,
                'type_name': type_name,
                'raw_size': len(self.payloads[(asset_id, asset_type)]),
            }
            for asset_id, asset_type, type_name in (
                ('font', 74, 'FontFace'),
                ('model', 10, 'Model'),
                ('walk', 24, 'Animation'),
            )
        ]

    def get_cache_stats(self) -> dict[str, int]:
        return {
            'total_assets': len(self.payloads),
            'total_size': sum(map(len, self.payloads.values())),
        }

    def get_asset(self, asset_id: str, asset_type: int) -> bytes | None:
        return self.payloads.get((asset_id, asset_type))

    def _flush_index(self) -> None:
        return


class _MeshPreviewCache:
    def __init__(self) -> None:
        self.index = {'assets': {}}
        self.config_manager = None

    def list_assets(self) -> list[dict[str, object]]:
        return [
            {'id': 'one', 'type': 1, 'type_name': 'Mesh', 'raw_size': 4},
            {'id': 'two', 'type': 1, 'type_name': 'Mesh', 'raw_size': 4},
        ]

    def get_cache_stats(self) -> dict[str, int]:
        return {'total_assets': 2, 'total_size': 8}

    def get_asset(self, _asset_id: str, _asset_type: int) -> bytes:
        return b'mesh'

    def _flush_index(self) -> None:
        return


def test_font_preview_registers_families_and_unregisters_on_replacement(
    monkeypatch,
) -> None:
    registered = iter((7, 8))
    removed: list[int] = []
    monkeypatch.setattr(
        QFontDatabase,
        'addApplicationFontFromData',
        staticmethod(lambda _data: next(registered)),
    )
    monkeypatch.setattr(
        QFontDatabase,
        'applicationFontFamilies',
        staticmethod(lambda font_id: [f'Preview {font_id}', f'Alternate {font_id}']),
    )
    monkeypatch.setattr(
        QFontDatabase,
        'removeApplicationFont',
        staticmethod(lambda font_id: removed.append(font_id) or True),
    )

    api = FontPreviewApi()  # pyright: ignore[reportCallIssue]
    assert api.load_bytes(b'OTTOfont-one')
    assert api.loaded
    assert api.formatName == 'OpenType'
    assert api.families == ['Preview 7', 'Alternate 7']
    api.selectedFamily = 'Alternate 7'
    assert api.selectedFamily == 'Alternate 7'

    assert api.load_bytes(b'\x00\x01\x00\x00font-two')
    assert removed == [7]
    assert api.selectedFamily == 'Preview 8'
    api.shutdown()
    assert removed == [7, 8]


def test_document_preview_supports_validated_edits_undo_and_explicit_export(
    tmp_path: Path,
) -> None:
    api = RobloxDocumentPreviewApi()  # pyright: ignore[reportCallIssue]
    api.set_export_directory(tmp_path)
    assert api.load_bytes(DOCUMENT_XML, '10_model', 'Example model')
    assert api.documentKind == 'RBXMX'
    assert api.treeModel.count == 2
    assert api.selectedName == 'Root'

    root_name_row = api.propertiesModel.indexOf('name', 'Name')
    count_row = api.propertiesModel.indexOf('name', 'ArchivableCount')
    assert not api.updateProperty(count_row, 'not-a-number')
    assert api.propertiesModel.get(count_row)['valueText'] == '4'
    assert api.renameSelected('Edited Root')
    assert api.propertiesModel.get(root_name_row)['valueText'] == 'Edited Root'
    assert api.addProperty('Visible', 'Bool')
    assert api.modified

    assert api.undo()
    assert api.propertiesModel.indexOf('name', 'Visible') == -1
    assert api.canUndo

    destination = tmp_path / 'edited.rbxmx'
    assert api.exportDocument('rbxmx', QUrl.fromLocalFile(str(destination)).toString())
    assert destination.is_file()
    assert classify_roblox_document(destination.read_bytes()) == 'rbxmx'
    assert b'Edited Root' in destination.read_bytes()
    assert b'Edited Root' not in DOCUMENT_XML

    api.detach()
    assert api.load_bytes(DOCUMENT_XML, '10_model', 'Example model')
    assert api.selectedName == 'Edited Root'
    assert api.revert()
    assert api.selectedName == 'Root'
    assert not api.modified


def test_document_preview_filters_hierarchy_and_validates_refs() -> None:
    api = RobloxDocumentPreviewApi()  # pyright: ignore[reportCallIssue]
    assert api.load_bytes(DOCUMENT_XML, '10_model')
    api.query = 'anchored'
    assert api.treeModel.count == 2
    api.query = 'does-not-exist'
    assert api.treeModel.count == 0
    api.query = ''
    api.selectInstance('2')
    assert api.selectedName == 'Child'
    size_row = api.propertiesModel.indexOf('name', 'Size')
    assert not api.updateProperty(size_row, '[1, 2, 3]')
    assert api.updateProperty(size_row, '{"X": 4, "Y": 5, "Z": 6}')
    assert '"X": 4' in api.propertiesModel.get(size_row)['valueText']


def test_animation_preview_exposes_tracks_converter_and_removes_temp_source() -> None:
    api = AnimationPreviewApi()  # pyright: ignore[reportCallIssue]
    api.set_export_directory(Path('/tmp/exports'))
    assert api.load_bytes(_animation_xml(), 'Walk')
    _wait_for_task(api.converter.task)

    assert api.loaded
    assert api.sourceLabel == 'Walk'
    assert api.duration == 1.25
    assert api.keyframeCount == 2
    assert api.trackCount == 2
    assert api.tracksModel.count == 2
    assert api.keyframeMarkers == [0.0, 1.0]
    assert api.converter.sourceLoaded
    assert api.converter.detectedRig == 'R6'
    assert QUrl(api.suggestedOutputUrl('R15')).toLocalFile() == '/tmp/exports/Walk_r15.rbxmx'

    preview_paths = list(api._preview_files)
    assert all(path.is_file() for path in preview_paths)
    api.shutdown()
    assert all(not path.exists() for path in preview_paths)


def test_animation_preview_evicts_replaced_converter_and_temp_source() -> None:
    api = AnimationPreviewApi()  # pyright: ignore[reportCallIssue]
    assert api.load_bytes(_animation_xml(), 'Walk one')
    _wait_for_task(api.converter.task)
    previous_converter = api.converter
    previous_paths = set(api._preview_files)

    assert api.load_bytes(_animation_xml(), 'Walk two')

    assert previous_converter.parent() is None
    assert all(not path.exists() for path in previous_paths)
    assert len(api._preview_files) == 1
    api.shutdown()


def test_cache_preview_router_prefers_rich_payload_controllers(monkeypatch) -> None:
    removed: list[int] = []
    monkeypatch.setattr(
        QFontDatabase,
        'addApplicationFontFromData',
        staticmethod(lambda _data: 11),
    )
    monkeypatch.setattr(
        QFontDatabase,
        'applicationFontFamilies',
        staticmethod(lambda _font_id: ['Cache Preview Font']),
    )
    monkeypatch.setattr(
        QFontDatabase,
        'removeApplicationFont',
        staticmethod(lambda font_id: removed.append(font_id) or True),
    )
    api = CacheApi(_PreviewCache())  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        api.loadPreview('74_font')
        assert api.previewKind == 'font'
        assert api.fontPreview.selectedFamily == 'Cache Preview Font'

        api.loadPreview('10_model')
        assert api.previewKind == 'document'
        assert api.documentPreview.treeModel.count == 2
        assert removed == [11]

        api.loadPreview('24_walk')
        assert api.previewKind == 'animation'
        assert api.animationPreview.keyframeCount == 2
        _wait_for_task(api.animationPreview.converter.task)
    finally:
        api.shutdown()


def test_cache_preview_releases_replaced_mesh_geometry(monkeypatch) -> None:
    from fleasion.qml_api import mesh_geometry

    class FakeGeometry(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.deleted = False

        def load(self, _payload: bytes) -> bool:
            return True

        def deleteLater(self) -> None:  # noqa: N802
            self.deleted = True

    monkeypatch.setattr(mesh_geometry, 'MeshGeometry', FakeGeometry)
    api = CacheApi(_MeshPreviewCache())  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        api.loadPreview('1_one')
        first = api.meshGeometry
        assert isinstance(first, FakeGeometry)

        api.loadPreview('1_two')

        assert first.deleted
        assert first.parent() is None
        assert api.meshGeometry is not first
    finally:
        current = api.meshGeometry
        api.shutdown()
        assert isinstance(current, FakeGeometry)
        assert current.deleted
