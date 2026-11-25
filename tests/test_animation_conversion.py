import base64
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from fleasion.cache import cache_manager as cache_manager_module
from fleasion.cache.animation_viewer import load_animation_data
from fleasion.cache.roblox_document import export_roblox_document
from fleasion.cache.tools.solidmodel_converter.rbxm.types import (
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxMetadata,
    RbxProperty,
)
from fleasion.cache.tools.solidmodel_converter.rbxm.xml_writer import write_rbxmx
from fleasion.utils.r15_to_r6 import keyframe_to_curve_anim


def _pose_xml(name: str, x: float, weight: float) -> str:
    return f"""<Item class="Pose" referent="RBX{name}{x}">
<Properties>
  <CoordinateFrame name="CFrame">
    <X>{x}</X><Y>0</Y><Z>0</Z>
    <R00>1</R00><R01>0</R01><R02>0</R02>
    <R10>0</R10><R11>1</R11><R12>0</R12>
    <R20>0</R20><R21>0</R21><R22>1</R22>
  </CoordinateFrame>
  <string name="Name">{name}</string>
  <float name="Weight">{weight}</float>
</Properties>
</Item>"""


def _keyframe_xml(time: float, poses: str) -> str:
    return f"""<Item class="Keyframe" referent="RBX{time}">
<Properties><float name="Time">{time}</float></Properties>
{poses}
</Item>"""


def _sparse_weight_animation() -> bytes:
    return f"""<roblox version="4">
<Item class="KeyframeSequence" referent="RBX0">
<Properties />
{_keyframe_xml(0.0, _pose_xml('UpperTorso', 1.0, 1.0))}
{_keyframe_xml(0.5, _pose_xml('UpperTorso', 100.0, 0.0) + _pose_xml('FaceBone', 2.0, 1.0))}
{_keyframe_xml(1.0, _pose_xml('UpperTorso', 3.0, 1.0))}
</Item>
</roblox>""".encode()


def test_zero_weight_pose_does_not_interrupt_sparse_body_track() -> None:
    keys = load_animation_data(_sparse_weight_animation())

    assert len(keys) == 3
    assert np.isclose(keys[1].pose_by_part_name['UpperTorso'][0, 3], 2.0)
    assert np.isclose(keys[1].pose_by_part_name['FaceBone'][0, 3], 2.0)


def test_curve_animation_export_uses_binary_metadata_and_preserves_sparse_track() -> None:
    curve_xml = keyframe_to_curve_anim(_sparse_weight_animation())
    root = ET.fromstring(curve_xml)

    assert not list(root.iterfind(".//string[@name='AttributesSerialize']"))
    assert not list(root.iterfind(".//string[@name='Tags']"))
    assert list(root.iterfind(".//BinaryString[@name='AttributesSerialize']"))
    assert list(root.iterfind(".//BinaryString[@name='Tags']"))

    torso = next(
        item
        for item in root.iter('Item')
        if item.get('class') == 'Folder'
        and item.findtext("Properties/string[@name='Name']") == 'UpperTorso'
    )
    position = next(
        item
        for item in torso.findall('Item')
        if item.get('class') == 'Vector3Curve'
        and item.findtext("Properties/string[@name='Name']") == 'Position'
    )
    x_curve = next(
        item
        for item in position.findall('Item')
        if item.get('class') == 'FloatCurve'
        and item.findtext("Properties/string[@name='Name']") == 'X'
    )
    encoded = x_curve.findtext("Properties/BinaryString[@name='ValuesAndTimes']")
    assert encoded is not None
    values_and_times = base64.b64decode(encoded)
    version, key_count = struct.unpack_from('<II', values_and_times)
    values = [struct.unpack_from('<f', values_and_times, 10 + i * 14)[0] for i in range(key_count)]
    times_offset = 8 + key_count * 14
    section_type, time_count = struct.unpack_from('<II', values_and_times, times_offset)
    ticks = [
        struct.unpack_from('<I', values_and_times, times_offset + 8 + i * 4)[0]
        for i in range(time_count)
    ]

    assert version == 2
    assert section_type == 1
    assert values == [1.0, 3.0]
    assert ticks == [0, 14400]


def test_cache_manager_exports_binary_keyframes_as_curve_animation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_binary, _ = export_roblox_document(
        _sparse_weight_animation(),
        'converted_document_rbxm',
        asset_type=24,
    )
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    assert manager.store_asset('weighted', 24, source_binary)

    exported = manager.export_asset('weighted', 24, export_format='converted_rbxmx_curve')

    assert exported is not None
    assert exported.suffix == '.rbxmx'
    root = ET.fromstring(exported.read_bytes())
    assert root.find("./Item[@class='CurveAnimation']") is not None
    assert not list(root.iterfind(".//string[@name='AttributesSerialize']"))
    assert not list(root.iterfind(".//string[@name='Tags']"))


def test_engine_binary_string_properties_keep_rbxmx_type() -> None:
    instance = RbxInstance(
        class_name='KeyframeSequence',
        referent=1,
        properties={
            name: RbxProperty(name=name, fmt=PropertyFormat.STRING, value=value)
            for name, value in {
                'AttributesSerialize': '',
                'GuidBinaryString': '0123456789abcdef',
                'Name': 'Animation',
                'Tags': 'plain-looking binary payload',
            }.items()
        },
    )
    document = RbxDocument(
        version=0,
        type_count=1,
        object_count=1,
        metadata=RbxMetadata(),
        instances={1: instance},
        roots=[instance],
    )

    root = ET.fromstring(write_rbxmx(document))
    properties = root.find('./Item/Properties')

    assert properties is not None
    assert properties.find("BinaryString[@name='AttributesSerialize']") is not None
    assert properties.find("BinaryString[@name='GuidBinaryString']") is not None
    assert properties.find("BinaryString[@name='Tags']") is not None
    assert properties.find("string[@name='Name']") is not None
