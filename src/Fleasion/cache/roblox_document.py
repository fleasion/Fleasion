"""Helpers for detecting and exporting Roblox document files."""

from __future__ import annotations

import base64
import gzip
import re
import struct
import xml.etree.ElementTree as ET
from typing import Any

from .tools.solidmodel_converter.rbxm.binary_reader import read_string
from .tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer, _decompress_chunk
from .tools.solidmodel_converter.rbxm.serializer import write_rbxm
from .tools.solidmodel_converter.rbxm.types import (
    PROPERTY_FORMAT_TO_XML_TAG,
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxMetadata,
    RbxProperty,
)
from .tools.solidmodel_converter.rbxm.xml_writer import write_rbxmx

RBXM_MAGIC = b'<roblox!\x89\xff\x0d\x0a\x1a\x0a'


def decompress_if_needed(data: bytes) -> bytes:
    """Return decompressed document bytes when the cache stored a gzip wrapper."""
    if data.startswith(b'\x1f\x8b'):
        return gzip.decompress(data)
    return data


def classify_roblox_document(data: bytes) -> str | None:
    """Classify bytes as rbxm, rbxmx, rbxl, or not a Roblox document."""
    try:
        data = decompress_if_needed(data)
    except Exception:
        return None

    if data.startswith(RBXM_MAGIC):
        return 'rbxl' if _binary_contains_class(data, 'DataModel') else 'rbxm'

    root = _parse_roblox_xml(data)
    if root is not None:
        return 'rbxl' if _xml_contains_datamodel(root) else 'rbxmx'

    return None


def get_roblox_document_export_formats(data: bytes, asset_type: int | None = None) -> list[str]:
    """Return converted export formats for a Roblox document payload."""
    kind = classify_roblox_document(data)
    if asset_type == 9 and kind in {'rbxl', 'rbxm', 'rbxmx'}:
        return ['converted_document_rbxl']
    if kind == 'rbxl':
        return ['converted_document_rbxl']
    if kind in {'rbxm', 'rbxmx'}:
        return ['converted_document_rbxm', 'converted_document_rbxmx']
    return []


def get_default_roblox_document_export_format(data: bytes, asset_type: int | None = None) -> str | None:
    """Return the best default converted export format for a Roblox document."""
    kind = classify_roblox_document(data)
    if asset_type == 9 and kind in {'rbxl', 'rbxm', 'rbxmx'}:
        return 'converted_document_rbxl'
    if kind == 'rbxl':
        return 'converted_document_rbxl'
    if kind == 'rbxmx':
        return 'converted_document_rbxmx'
    if kind == 'rbxm':
        return 'converted_document_rbxm'
    return None


def export_roblox_document(
    data: bytes,
    export_format: str,
    asset_type: int | None = None,
) -> tuple[bytes, str]:
    """Convert a Roblox document payload to the requested export format."""
    data = decompress_if_needed(data)
    kind = classify_roblox_document(data)
    if kind is None:
        raise ValueError('Data is not an RBXM/RBXMX/RBXL document')

    if export_format == 'converted_document_rbxl':
        if kind != 'rbxl' and asset_type != 9:
            raise ValueError('Only DataModel documents can be exported as RBXL')
        return _to_binary_document(data), '.rbxl'

    if export_format == 'converted_document_rbxm':
        if kind == 'rbxl' or asset_type == 9:
            raise ValueError('RBXL documents must be exported as RBXL')
        return _to_binary_document(data), '.rbxm'

    if export_format == 'converted_document_rbxmx':
        if kind == 'rbxl' or asset_type == 9:
            raise ValueError('RBXL documents must be exported as RBXL')
        if _parse_roblox_xml(data) is not None:
            return data, '.rbxmx'
        return write_rbxmx(RbxmDeserializer().deserialize(data)), '.rbxmx'

    raise ValueError(f'Unsupported Roblox document export format: {export_format}')


def _to_binary_document(data: bytes) -> bytes:
    if data.startswith(RBXM_MAGIC):
        return data
    return write_rbxm(_xml_to_document(data))


def _document_contains_datamodel(doc: RbxDocument) -> bool:
    return any(inst.class_name == 'DataModel' for inst in doc.instances.values())


def _binary_contains_class(data: bytes, class_name: str) -> bool:
    offset = 32
    target = class_name
    try:
        while offset + 16 <= len(data):
            chunk_name = data[offset : offset + 4].decode('ascii')
            compressed_size = struct.unpack_from('<I', data, offset + 4)[0]
            uncompressed_size = struct.unpack_from('<I', data, offset + 8)[0]
            offset += 16

            if chunk_name == 'END\x00':
                break

            if compressed_size == 0:
                chunk_start = offset
                offset += uncompressed_size
                if chunk_name != 'INST':
                    continue
                chunk_data = data[chunk_start:offset]
            else:
                raw = data[offset : offset + compressed_size]
                offset += compressed_size
                if chunk_name != 'INST':
                    continue
                chunk_data = _decompress_chunk(raw, uncompressed_size)

            if chunk_name == 'INST':
                found_class, _ = read_string(chunk_data, 4)
                if found_class == target:
                    return True
    except Exception:
        try:
            return _document_contains_datamodel(RbxmDeserializer().deserialize(data))
        except Exception:
            return False
    return False


def _xml_contains_datamodel(root: ET.Element) -> bool:
    return any(
        _tag_name(elem) == 'Item' and elem.get('class') == 'DataModel'
        for elem in root.iter()
    )


def _parse_roblox_xml(data: bytes) -> ET.Element | None:
    stripped = data.lstrip()
    if stripped.startswith(b'\xef\xbb\xbf'):
        stripped = stripped[3:].lstrip()
    if not stripped.startswith(b'<'):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if _tag_name(root) != 'roblox':
        return None
    return root


def _xml_to_document(data: bytes) -> RbxDocument:
    root = ET.fromstring(data)
    if _tag_name(root) != 'roblox':
        raise ValueError('XML root is not a Roblox document')

    shared_by_md5: dict[str, bytes] = {}
    shared_strings: list[bytes] = []
    ss_root = _find_child(root, 'SharedStrings')
    if ss_root is not None:
        for shared in _children_named(ss_root, 'SharedString'):
            text = (shared.text or '').strip()
            blob = b''
            if text:
                try:
                    blob = base64.b64decode(text)
                except Exception:
                    blob = text.encode('utf-8', errors='replace')
            md5 = shared.get('md5') or ''
            if md5:
                shared_by_md5[md5] = blob
            shared_strings.append(blob)

    ref_map: dict[str, int] = {}
    next_ref = 1

    def mapped_ref(ref: str) -> int:
        nonlocal next_ref
        if ref in ref_map:
            return ref_map[ref]
        try:
            value = int(ref)
        except ValueError:
            while next_ref in ref_map.values():
                next_ref += 1
            value = next_ref
            next_ref += 1
        ref_map[ref] = value
        return value

    instances: dict[int, RbxInstance] = {}

    def parse_item(item: ET.Element) -> RbxInstance:
        referent_text = item.get('referent') or ''
        referent = mapped_ref(referent_text)
        inst = RbxInstance(class_name=item.get('class') or 'Folder', referent=referent)
        instances[referent] = inst

        props_elem = _find_child(item, 'Properties')
        if props_elem is not None:
            for prop_elem in list(props_elem):
                prop_name = prop_elem.get('name') or ''
                if not prop_name:
                    continue
                type_name = _tag_name(prop_elem)
                fmt = _property_format_from_type_name(type_name)
                if fmt is None:
                    continue
                value = _xml_property_value(prop_elem, type_name, shared_by_md5)
                inst.properties[prop_name] = RbxProperty(
                    name=prop_name,
                    fmt=fmt,
                    value=_value_for_format(value, fmt, mapped_ref),
                )

        inst.children = [parse_item(child) for child in _children_named(item, 'Item')]
        return inst

    roots = [parse_item(item) for item in _children_named(root, 'Item')]
    metadata = {
        elem.get('name') or _tag_name(elem): (elem.text or '').strip()
        for elem in list(root)
        if _tag_name(elem) == 'Meta'
    }

    return RbxDocument(
        version=0,
        type_count=0,
        object_count=len(instances),
        metadata=RbxMetadata(entries=metadata),
        instances=instances,
        roots=roots,
        shared_strings=shared_strings,
    )


def _xml_property_value(
    elem: ET.Element,
    type_name: str,
    shared_by_md5: dict[str, bytes],
) -> Any:
    text = elem.text or ''
    if type_name == 'SharedString':
        return shared_by_md5.get(text.strip(), b'')
    if type_name == 'BinaryString':
        stripped = text.strip()
        if not stripped:
            return b''
        try:
            return base64.b64decode(stripped)
        except Exception:
            return stripped.encode('utf-8', errors='replace')
    if type_name == 'ProtectedString':
        return text
    if list(elem):
        return {_tag_name(child): (child.text or '').strip() for child in elem}
    return text.strip()


def _property_format_from_type_name(type_name: str) -> PropertyFormat | None:
    normalized = type_name.strip()
    if not normalized:
        return PropertyFormat.STRING
    upper = normalized.upper()
    if upper in PropertyFormat.__members__:
        return PropertyFormat[upper]

    tag_to_format = {tag.lower(): fmt for fmt, tag in PROPERTY_FORMAT_TO_XML_TAG.items()}
    aliases = {
        'class': None,
        'refid': None,
        'binarystring': PropertyFormat.STRING,
        'protectedstring': PropertyFormat.STRING,
        'content': PropertyFormat.CONTENT,
        'token': PropertyFormat.ENUM,
        'optionalcoordinateframe': PropertyFormat.OPTIONAL_CFRAME,
        'uniqueid': PropertyFormat.UNIQUE_ID,
        'securitycapabilities': PropertyFormat.SECURITY_CAPABILITIES,
    }
    key = normalized.lower()
    if key in aliases:
        return aliases[key]
    return tag_to_format.get(key, PropertyFormat.STRING)


def _value_for_format(value: Any, fmt: PropertyFormat, ref_mapper) -> Any:
    if fmt in {
        PropertyFormat.INT,
        PropertyFormat.ENUM,
        PropertyFormat.BRICK_COLOR,
        PropertyFormat.SECURITY_CAPABILITIES,
    }:
        return _safe_int(value)
    if fmt == PropertyFormat.INT64:
        return _safe_int(value)
    if fmt in {PropertyFormat.FLOAT, PropertyFormat.DOUBLE}:
        return _safe_float(value)
    if fmt == PropertyFormat.BOOL:
        return _safe_bool(value)
    if fmt == PropertyFormat.REF:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get('Ref') or value.get('referent') or value.get('id')
        text = str(value or '').strip()
        if text in {'', 'None', '-1', 'null'}:
            return None
        if '->' in text:
            text = text.split('->', 1)[0].strip()
        return ref_mapper(text)
    if fmt == PropertyFormat.UNIQUE_ID:
        if isinstance(value, dict) or isinstance(value, bytes):
            return value
        text = str(value).strip().replace('-', '')
        if len(text) == 32:
            try:
                xml_random = int(text[:16], 16)
                random_bits = (xml_random >> 1) | ((xml_random & 1) << 63)
                return {
                    'Index': int(text[24:32], 16),
                    'Time': int(text[16:24], 16),
                    'Random': random_bits,
                }
            except ValueError:
                pass
        return {'Index': 0, 'Time': 0, 'Random': 0}
    if fmt == PropertyFormat.CONTENT:
        if isinstance(value, dict):
            uri = value.get('Uri') or value.get('uri') or value.get('url')
            if uri:
                return {'SourceType': 'Uri', 'Uri': str(uri)}
            ref = value.get('Ref')
            if ref is not None:
                return {'SourceType': 'Object', 'Ref': ref_mapper(str(ref))}
            if 'null' in value:
                return None
            return value
        if value is None:
            return value
        text = str(value)
        return {'SourceType': 'Uri', 'Uri': text} if text else None
    if fmt == PropertyFormat.UDIM:
        return _parse_udim_value(value)
    if fmt == PropertyFormat.UDIM2:
        return _parse_udim2_value(value)
    if fmt == PropertyFormat.RAY:
        return _parse_ray_value(value)
    if fmt == PropertyFormat.COLOR3:
        return _parse_vector_value(value, ('R', 'G', 'B'), float)
    if fmt == PropertyFormat.VECTOR2:
        return _parse_vector_value(value, ('X', 'Y'), float)
    if fmt == PropertyFormat.VECTOR3:
        return _parse_vector_value(value, ('X', 'Y', 'Z'), float)
    if fmt == PropertyFormat.VECTOR2INT16:
        return _parse_vector_value(value, ('X', 'Y'), int)
    if fmt == PropertyFormat.VECTOR3INT16:
        return _parse_vector_value(value, ('X', 'Y', 'Z'), int)
    if fmt in {PropertyFormat.CFRAME_MATRIX, PropertyFormat.CFRAME_QUAT, PropertyFormat.OPTIONAL_CFRAME}:
        return _parse_cframe_value(value)
    if fmt == PropertyFormat.NUMBER_RANGE:
        return _parse_number_range_value(value)
    if fmt == PropertyFormat.RECT2D:
        return _parse_rect2d_value(value)
    if fmt == PropertyFormat.PHYSICAL_PROPERTIES:
        return _parse_physical_properties_value(value)
    if fmt == PropertyFormat.COLOR3UINT8:
        return _parse_vector_value(value, ('R', 'G', 'B'), int)
    if fmt == PropertyFormat.FONT:
        return _parse_font_value(value)
    return value


def _parse_udim_value(value: Any) -> dict[str, float | int]:
    pairs = {str(k): v for k, v in value.items()} if isinstance(value, dict) else _parse_key_values(str(value))
    if pairs:
        return {'S': _safe_float(pairs.get('S', 0.0)), 'O': _safe_int(pairs.get('O', 0))}
    numbers = _parse_numbers(str(value))
    return {'S': numbers[0] if len(numbers) > 0 else 0.0, 'O': int(numbers[1]) if len(numbers) > 1 else 0}


def _parse_udim2_value(value: Any) -> dict[str, float | int]:
    pairs = {str(k): v for k, v in value.items()} if isinstance(value, dict) else _parse_key_values(str(value))
    if pairs:
        return {
            'XS': _safe_float(pairs.get('XS', 0.0)),
            'XO': _safe_int(pairs.get('XO', 0)),
            'YS': _safe_float(pairs.get('YS', 0.0)),
            'YO': _safe_int(pairs.get('YO', 0)),
        }
    numbers = _parse_numbers(str(value))
    return {
        'XS': numbers[0] if len(numbers) > 0 else 0.0,
        'XO': int(numbers[1]) if len(numbers) > 1 else 0,
        'YS': numbers[2] if len(numbers) > 2 else 0.0,
        'YO': int(numbers[3]) if len(numbers) > 3 else 0,
    }


def _parse_vector_value(value: Any, keys: tuple[str, ...], caster) -> dict[str, Any]:
    pairs = {str(k): v for k, v in value.items()} if isinstance(value, dict) else _parse_key_values(str(value))
    if pairs:
        return {key: _cast_number(pairs.get(key, 0), caster) for key in keys}
    numbers = _parse_numbers(str(value))
    return {key: _cast_number(numbers[index] if index < len(numbers) else 0, caster) for index, key in enumerate(keys)}


def _parse_ray_value(value: Any) -> dict[str, dict[str, float]]:
    if isinstance(value, dict):
        return {
            'origin': _parse_vector_value(value.get('origin', {}), ('X', 'Y', 'Z'), float),
            'direction': _parse_vector_value(value.get('direction', {}), ('X', 'Y', 'Z'), float),
        }
    numbers = _parse_numbers(str(value))
    padded = numbers + [0.0] * max(0, 6 - len(numbers))
    return {
        'origin': {'X': padded[0], 'Y': padded[1], 'Z': padded[2]},
        'direction': {'X': padded[3], 'Y': padded[4], 'Z': padded[5]},
    }


def _parse_cframe_value(value: Any) -> dict[str, float] | None:
    text = str(value).strip()
    if value is None or text.lower() in {'', 'none', 'null'}:
        return None
    result = {
        'X': 0.0, 'Y': 0.0, 'Z': 0.0,
        'R00': 1.0, 'R01': 0.0, 'R02': 0.0,
        'R10': 0.0, 'R11': 1.0, 'R12': 0.0,
        'R20': 0.0, 'R21': 0.0, 'R22': 1.0,
    }
    if isinstance(value, dict):
        result.update({key: _safe_float(value.get(key, result[key])) for key in result})
        return result
    pairs = _parse_key_values(text)
    if pairs:
        for key in result:
            if key in pairs:
                result[key] = _safe_float(pairs[key])
        return result
    numbers = _parse_numbers(text)
    if len(numbers) >= 12:
        for key, number in zip(result, numbers[:12], strict=False):
            result[key] = number
    elif len(numbers) >= 3:
        result['X'], result['Y'], result['Z'] = numbers[:3]
    return result


def _parse_number_range_value(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {'Min': _safe_float(value.get('Min', 0.0)), 'Max': _safe_float(value.get('Max', 0.0))}
    pairs = _parse_key_values(str(value))
    if pairs:
        return {'Min': _safe_float(pairs.get('Min', 0.0)), 'Max': _safe_float(pairs.get('Max', 0.0))}
    numbers = _parse_numbers(str(value))
    return {'Min': numbers[0] if len(numbers) > 0 else 0.0, 'Max': numbers[1] if len(numbers) > 1 else 0.0}


def _parse_rect2d_value(value: Any) -> dict[str, dict[str, float]]:
    if isinstance(value, dict):
        return {
            'min': _parse_vector_value(value.get('min', {}), ('X', 'Y'), float),
            'max': _parse_vector_value(value.get('max', {}), ('X', 'Y'), float),
        }
    numbers = _parse_numbers(str(value))
    padded = numbers + [0.0] * max(0, 4 - len(numbers))
    return {'min': {'X': padded[0], 'Y': padded[1]}, 'max': {'X': padded[2], 'Y': padded[3]}}


def _parse_physical_properties_value(value: Any) -> dict[str, Any] | None:
    text = str(value).strip()
    if value is None or text.lower() in {'', 'none', 'null', 'default'}:
        return None
    if isinstance(value, dict):
        return {
            'CustomPhysics': _safe_bool(value.get('CustomPhysics', True)),
            'Density': _safe_float(value.get('Density', 0.0)),
            'Friction': _safe_float(value.get('Friction', 0.0)),
            'Elasticity': _safe_float(value.get('Elasticity', 0.0)),
            'FrictionWeight': _safe_float(value.get('FrictionWeight', 0.0)),
            'ElasticityWeight': _safe_float(value.get('ElasticityWeight', 0.0)),
            'AcousticAbsorption': _safe_float(value.get('AcousticAbsorption', 1.0)),
        }
    pairs = _parse_key_values(text)
    if pairs:
        return _parse_physical_properties_value(pairs)
    numbers = _parse_numbers(text)
    if len(numbers) < 5:
        return None
    result: dict[str, Any] = {
        'CustomPhysics': True,
        'Density': numbers[0],
        'Friction': numbers[1],
        'Elasticity': numbers[2],
        'FrictionWeight': numbers[3],
        'ElasticityWeight': numbers[4],
    }
    if len(numbers) > 5:
        result['AcousticAbsorption'] = numbers[5]
    return result


def _parse_font_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            'Family': str(value.get('Family', '')),
            'Weight': _safe_int(value.get('Weight', 400)),
            'Style': _safe_int(value.get('Style', 0)),
            'CachedFaceId': str(value.get('CachedFaceId', '')),
        }
    pairs = _parse_key_values(str(value))
    if pairs:
        return _parse_font_value(pairs)
    parts = [part.strip() for part in str(value).split(',')]
    return {
        'Family': parts[0] if len(parts) > 0 else '',
        'Weight': _safe_int(parts[1] if len(parts) > 1 else 400),
        'Style': _safe_int(parts[2] if len(parts) > 2 else 0),
        'CachedFaceId': parts[3] if len(parts) > 3 else '',
    }


def _parse_key_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in re.split(r'[,;]\s*', text.strip().strip('[]{}()')):
        if '=' in part:
            key, raw_value = part.split('=', 1)
        elif ':' in part:
            key, raw_value = part.split(':', 1)
        else:
            continue
        key = key.strip().strip('"\'{}[]()')
        raw_value = raw_value.strip().strip('"\'{}[]()')
        if key:
            result[key] = raw_value
    return result


def _parse_numbers(text: str) -> list[float]:
    return [
        float(match.group(0))
        for match in re.finditer(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', text)
    ]


def _cast_number(value: Any, caster):
    if caster is int:
        return int(round(float(value)))
    return caster(value)


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _tag_name(child) == name:
            return child
    return None


def _children_named(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(parent) if _tag_name(child) == name]


def _tag_name(elem: ET.Element) -> str:
    return elem.tag.rsplit('}', 1)[-1]
