"""RBXMX (XML) writer for Roblox model files.

Converts an in-memory RbxDocument to the Roblox XML model format (.rbxmx).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from .types import (
    PROPERTY_FORMAT_TO_XML_TAG,
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxProperty,
)

log = logging.getLogger(__name__)


# Collector for shared strings during XML writing.
# Maps md5 hash -> base64-encoded content.
_shared_string_registry: dict[str, str] = {}


def write_rbxmx(doc: RbxDocument) -> bytes:
    """Convert an RbxDocument to RBXMX (XML) bytes."""
    # Reset shared string registry for this file
    _shared_string_registry.clear()

    root = Element('roblox')
    root.set('xmlns:xmime', 'http://www.w3.org/2005/05/xmlmime')
    root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    root.set(
        'xsi:noNamespaceSchemaLocation',
        'http://www.roblox.com/roblox.xsd',
    )
    root.set('version', '4')

    # External declarations (standard for RBXMX)
    ext1 = SubElement(root, 'External')
    ext1.text = 'null'
    ext2 = SubElement(root, 'External')
    ext2.text = 'nil'

    # Write metadata as Meta tags
    for key, value in doc.metadata.entries.items():
        meta_el = SubElement(root, 'Meta')
        meta_el.set('name', key)
        meta_el.text = value

    # Write instance tree
    for inst in doc.roots:
        _write_instance(root, inst, doc)

    # Write SharedStrings section if any shared strings were collected
    if _shared_string_registry:
        ss_section = SubElement(root, 'SharedStrings')
        for md5_hash, b64_content in _shared_string_registry.items():
            ss_el = SubElement(ss_section, 'SharedString')
            ss_el.set('md5', md5_hash)
            ss_el.text = b64_content

    indent(root, space='\t')
    xml_bytes = tostring(root, encoding='unicode', xml_declaration=False)
    header = '<?xml version="1.0" encoding="utf-8"?>\n'
    return (header + xml_bytes).encode('utf-8')


def _write_instance(
    parent_el: Element,
    inst: RbxInstance,
    doc: RbxDocument,
) -> None:
    """Write a single instance and its children as XML."""
    item = SubElement(parent_el, 'Item')
    item.set('class', inst.class_name)
    item.set('referent', f'RBX{inst.referent:032X}')

    props_el = SubElement(item, 'Properties')

    for prop in sorted(inst.properties.values(), key=lambda p: p.name):
        _write_property(props_el, prop, doc)

    for child in inst.children:
        _write_instance(item, child, doc)


def _write_property(
    props_el: Element,
    prop: RbxProperty,
    doc: RbxDocument,  # noqa: ARG001
) -> None:
    """Write a single property value as XML."""
    xml_tag = PROPERTY_FORMAT_TO_XML_TAG.get(prop.fmt, 'string')

    match prop.fmt:
        case PropertyFormat.STRING:
            _write_string_prop(props_el, xml_tag, prop)
        case PropertyFormat.BOOL:
            el = SubElement(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = 'true' if prop.value else 'false'
        case PropertyFormat.INT | PropertyFormat.ENUM | PropertyFormat.BRICK_COLOR:
            el = SubElement(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = str(prop.value)
        case PropertyFormat.FLOAT:
            el = SubElement(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = _fmt_float(prop.value)
        case PropertyFormat.DOUBLE:
            el = SubElement(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = _fmt_float(prop.value)
        case PropertyFormat.UDIM:
            _write_udim(props_el, prop)
        case PropertyFormat.UDIM2:
            _write_udim2(props_el, prop)
        case PropertyFormat.RAY:
            _write_ray(props_el, prop)
        case PropertyFormat.FACES:
            _write_faces(props_el, prop)
        case PropertyFormat.AXES:
            _write_axes(props_el, prop)
        case PropertyFormat.COLOR3:
            _write_color3(props_el, xml_tag, prop)
        case PropertyFormat.VECTOR2:
            _write_vector2(props_el, prop)
        case PropertyFormat.VECTOR3:
            _write_vector3(props_el, xml_tag, prop)
        case PropertyFormat.VECTOR2INT16:
            _write_vector_int(props_el, 'Vector2int16', prop, ('X', 'Y'))
        case PropertyFormat.VECTOR3INT16:
            _write_vector_int(props_el, 'Vector3int16', prop, ('X', 'Y', 'Z'))
        case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
            _write_cframe(props_el, prop)
        case PropertyFormat.REF:
            _write_ref(props_el, prop)
        case PropertyFormat.NUMBER_SEQUENCE:
            _write_number_sequence(props_el, prop)
        case PropertyFormat.COLOR_SEQUENCE:
            _write_color_sequence(props_el, prop)
        case PropertyFormat.NUMBER_RANGE:
            _write_number_range(props_el, prop)
        case PropertyFormat.RECT2D:
            _write_rect2d(props_el, prop)
        case PropertyFormat.PHYSICAL_PROPERTIES:
            _write_physical_properties(props_el, prop)
        case PropertyFormat.COLOR3UINT8:
            _write_color3uint8(props_el, prop)
        case PropertyFormat.INT64:
            el = SubElement(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = str(prop.value)
        case PropertyFormat.SHARED_STRING:
            _write_shared_string(props_el, prop)
        case _:
            log.warning('Skipping unhandled property format: %s', prop.fmt)


# --- Individual property writers ---


def _write_string_prop(parent: Element, tag: str, prop: RbxProperty) -> None:
    val = prop.value
    if isinstance(val, bytes):
        # Binary data -> base64 encode it
        el = SubElement(parent, 'BinaryString')
        el.set('name', prop.name)
        el.text = base64.b64encode(val).decode('ascii')
    elif prop.name in {'Source', 'LinkedSource'}:
        el = SubElement(parent, 'ProtectedString')
        el.set('name', prop.name)
        el.text = val
    # Check if it looks like a content URL
    elif _is_content_url(val, prop.name):
        el = SubElement(parent, 'Content')
        el.set('name', prop.name)
        if val:
            url_el = SubElement(el, 'url')
            url_el.text = val
        else:
            SubElement(el, 'null')
    else:
        el = SubElement(parent, tag)
        el.set('name', prop.name)
        el.text = val


def _is_content_url(value: str, prop_name: str) -> bool:
    """Heuristic: detect Content-type properties."""
    content_props = {
        'AssetId',
        'MeshId',
        'TextureId',
        'SoundId',
        'Texture',
        'LinkedSource',
        'Image',
        'Animation',
    }
    if prop_name in content_props:
        return True
    return value.startswith(('http://', 'https://', 'rbxassetid://', 'rbxasset://'))


def _write_udim(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'UDim')
    el.set('name', prop.name)
    SubElement(el, 'S').text = _fmt_float(prop.value['S'])
    SubElement(el, 'O').text = str(prop.value['O'])


def _write_udim2(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'UDim2')
    el.set('name', prop.name)
    SubElement(el, 'XS').text = _fmt_float(prop.value['XS'])
    SubElement(el, 'XO').text = str(prop.value['XO'])
    SubElement(el, 'YS').text = _fmt_float(prop.value['YS'])
    SubElement(el, 'YO').text = str(prop.value['YO'])


def _write_ray(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Ray')
    el.set('name', prop.name)
    origin = SubElement(el, 'origin')
    SubElement(origin, 'X').text = _fmt_float(prop.value['origin']['X'])
    SubElement(origin, 'Y').text = _fmt_float(prop.value['origin']['Y'])
    SubElement(origin, 'Z').text = _fmt_float(prop.value['origin']['Z'])
    direction = SubElement(el, 'direction')
    SubElement(direction, 'X').text = _fmt_float(prop.value['direction']['X'])
    SubElement(direction, 'Y').text = _fmt_float(prop.value['direction']['Y'])
    SubElement(direction, 'Z').text = _fmt_float(prop.value['direction']['Z'])


def _write_faces(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Faces')
    el.set('name', prop.name)
    mask = prop.value
    faces: list[str] = []
    face_names = ['Right', 'Top', 'Back', 'Left', 'Bottom', 'Front']
    for i, name in enumerate(face_names):
        if mask & (1 << i):
            faces.append(name)
    el.text = ', '.join(faces) if faces else ''


def _write_axes(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Axes')
    el.set('name', prop.name)
    mask = prop.value
    axes: list[str] = []
    axis_names = ['X', 'Y', 'Z']
    for i, name in enumerate(axis_names):
        if mask & (1 << i):
            axes.append(name)
    el.text = ', '.join(axes) if axes else ''


def _write_color3(parent: Element, tag: str, prop: RbxProperty) -> None:
    el = SubElement(parent, tag)
    el.set('name', prop.name)
    SubElement(el, 'R').text = _fmt_float(prop.value['R'])
    SubElement(el, 'G').text = _fmt_float(prop.value['G'])
    SubElement(el, 'B').text = _fmt_float(prop.value['B'])


def _write_vector2(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Vector2')
    el.set('name', prop.name)
    SubElement(el, 'X').text = _fmt_float(prop.value['X'])
    SubElement(el, 'Y').text = _fmt_float(prop.value['Y'])


def _write_vector3(parent: Element, tag: str, prop: RbxProperty) -> None:
    el = SubElement(parent, tag)
    el.set('name', prop.name)
    SubElement(el, 'X').text = _fmt_float(prop.value['X'])
    SubElement(el, 'Y').text = _fmt_float(prop.value['Y'])
    SubElement(el, 'Z').text = _fmt_float(prop.value['Z'])


def _write_vector_int(
    parent: Element, tag: str, prop: RbxProperty, axes: tuple[str, ...]
) -> None:
    el = SubElement(parent, tag)
    el.set('name', prop.name)
    for axis in axes:
        SubElement(el, axis).text = str(prop.value[axis])


def _write_cframe(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'CoordinateFrame')
    el.set('name', prop.name)
    cf: dict[str, float] = prop.value
    SubElement(el, 'X').text = _fmt_float(cf['X'])
    SubElement(el, 'Y').text = _fmt_float(cf['Y'])
    SubElement(el, 'Z').text = _fmt_float(cf['Z'])
    for row in range(3):
        for col in range(3):
            key = f'R{row}{col}'
            SubElement(el, key).text = _fmt_float(cf[key])


def _write_ref(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Ref')
    el.set('name', prop.name)
    if prop.value is None:
        el.text = 'null'
    else:
        el.text = f'RBX{prop.value:032X}'


def _write_number_sequence(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'NumberSequence')
    el.set('name', prop.name)
    parts: list[str] = [
        f'{_fmt_float(key["Time"])} {_fmt_float(key["Value"])} '
        f'{_fmt_float(key["Envelope"])}'
        for key in prop.value
    ]
    el.text = ' '.join(parts)


def _write_color_sequence(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'ColorSequence')
    el.set('name', prop.name)
    parts: list[str] = [
        f'{_fmt_float(key["Time"])} {_fmt_float(key["R"])} '
        f'{_fmt_float(key["G"])} {_fmt_float(key["B"])} 0'
        for key in prop.value
    ]
    el.text = ' '.join(parts)


def _write_number_range(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'NumberRange')
    el.set('name', prop.name)
    el.text = f'{_fmt_float(prop.value["Min"])} {_fmt_float(prop.value["Max"])}'


def _write_rect2d(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Rect2D')
    el.set('name', prop.name)
    mn: dict[str, Any] = prop.value['min']
    mx: dict[str, Any] = prop.value['max']
    min_el = SubElement(el, 'min')
    SubElement(min_el, 'X').text = _fmt_float(mn['X'])
    SubElement(min_el, 'Y').text = _fmt_float(mn['Y'])
    max_el = SubElement(el, 'max')
    SubElement(max_el, 'X').text = _fmt_float(mx['X'])
    SubElement(max_el, 'Y').text = _fmt_float(mx['Y'])


def _write_physical_properties(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'PhysicalProperties')
    el.set('name', prop.name)
    if prop.value is None:
        SubElement(el, 'CustomPhysics').text = 'false'
    else:
        SubElement(el, 'CustomPhysics').text = 'true'
        SubElement(el, 'Density').text = _fmt_float(prop.value['Density'])
        SubElement(el, 'Friction').text = _fmt_float(prop.value['Friction'])
        SubElement(el, 'Elasticity').text = _fmt_float(prop.value['Elasticity'])
        SubElement(el, 'FrictionWeight').text = _fmt_float(prop.value['FrictionWeight'])
        SubElement(el, 'ElasticityWeight').text = _fmt_float(
            prop.value['ElasticityWeight']
        )


def _write_color3uint8(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'Color3uint8')
    el.set('name', prop.name)
    # Packed as 0xFFRRGGBB
    r = prop.value['R']
    g = prop.value['G']
    b = prop.value['B']
    packed = 0xFF000000 | (r << 16) | (g << 8) | b
    el.text = str(packed)


def _write_shared_string(parent: Element, prop: RbxProperty) -> None:
    el = SubElement(parent, 'SharedString')
    el.set('name', prop.name)
    if isinstance(prop.value, bytes):
        # Compute MD5 hash of the raw content, base64-encoded (Studio requires this format)
        md5_b64 = base64.b64encode(hashlib.md5(prop.value).digest()).decode('ascii')  # noqa: S324
        b64_content = base64.b64encode(prop.value).decode('ascii')
        # Register in the shared string registry
        _shared_string_registry[md5_b64] = b64_content
        # Property value is the md5 reference, not the data
        el.text = md5_b64
    else:
        el.text = str(prop.value)


def _fmt_float(value: Any) -> str:
    """Format a float for XML output, avoiding unnecessary decimals."""
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f'{value:.8g}'
    return str(value)
