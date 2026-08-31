"""RBXMX (XML) writer for Roblox model files.

Converts an in-memory RbxDocument to the Roblox XML model format (.rbxmx).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import TYPE_CHECKING, Any

from defusedxml import ElementTree as DefusedElementTree

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

from .types import (
    PROPERTY_FORMAT_TO_XML_TAG,
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxProperty,
)

log = logging.getLogger(__name__)

_XML_ELEMENT_TEMPLATE = DefusedElementTree.fromstring('<_ />')


def _xml_element(tag: str) -> ET.Element:
    """Create an Element without exposing the unsafe stdlib parser."""
    return _XML_ELEMENT_TEMPLATE.makeelement(tag, {})


def _xml_sub_element(parent: ET.Element, tag: str) -> ET.Element:
    child = _xml_element(tag)
    parent.append(child)
    return child


def _indent_xml(root: ET.Element, *, space: str) -> None:
    """Match ElementTree.indent output without importing its parser at runtime."""
    if not len(root):
        return

    indentations = ['\n']

    def _indent_children(element: ET.Element, level: int) -> None:
        child_level = level + 1
        if child_level >= len(indentations):
            indentations.append(indentations[level] + space)
        child_indentation = indentations[child_level]

        if not element.text or not element.text.strip():
            element.text = child_indentation
        for child in element:
            if len(child):
                _indent_children(child, child_level)
            if not child.tail or not child.tail.strip():
                child.tail = child_indentation
        last_child = element[-1]
        if not last_child.tail or not last_child.tail.strip():
            last_child.tail = indentations[level]

    _indent_children(root, 0)


# Collector for shared strings during XML writing.
# Maps md5 hash -> base64-encoded content.
_shared_string_registry: dict[str, str] = {}

# RBXM uses one binary STRING property format for both textual strings and raw
# byte strings.  These engine properties must retain their BinaryString XML
# type even when their payload is empty or happens to be valid UTF-8.
_BINARY_STRING_PROPERTIES = frozenset(
    {
        'AttributesSerialize',
        'GuidBinaryString',
        'Tags',
    }
)


def write_rbxmx(doc: RbxDocument) -> bytes:
    """Convert an RbxDocument to RBXMX (XML) bytes."""
    # Reset shared string registry for this file
    _shared_string_registry.clear()

    root = _xml_element('roblox')
    root.set('xmlns:xmime', 'http://www.w3.org/2005/05/xmlmime')
    root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    root.set(
        'xsi:noNamespaceSchemaLocation',
        'http://www.roblox.com/roblox.xsd',
    )
    root.set('version', '4')

    # External declarations (standard for RBXMX)
    ext1 = _xml_sub_element(root, 'External')
    ext1.text = 'null'
    ext2 = _xml_sub_element(root, 'External')
    ext2.text = 'nil'

    # Write metadata as Meta tags
    for key, value in doc.metadata.entries.items():
        meta_el = _xml_sub_element(root, 'Meta')
        meta_el.set('name', key)
        meta_el.text = value

    # Write instance tree
    for inst in doc.roots:
        _write_instance(root, inst, doc)

    # Write SharedStrings section if any shared strings were collected
    if _shared_string_registry:
        ss_section = _xml_sub_element(root, 'SharedStrings')
        for md5_hash, b64_content in _shared_string_registry.items():
            ss_el = _xml_sub_element(ss_section, 'SharedString')
            ss_el.set('md5', md5_hash)
            ss_el.text = b64_content

    _indent_xml(root, space='\t')
    xml_bytes = DefusedElementTree.tostring(root, encoding='unicode', xml_declaration=False)
    header = '<?xml version="1.0" encoding="utf-8"?>\n'
    return (header + xml_bytes).encode('utf-8')


def _write_instance(
    parent_el: ET.Element,
    inst: RbxInstance,
    doc: RbxDocument,
) -> None:
    """Write a single instance and its children as XML."""
    item = _xml_sub_element(parent_el, 'Item')
    item.set('class', inst.class_name)
    item.set('referent', f'RBX{inst.referent:032X}')

    props_el = _xml_sub_element(item, 'Properties')

    for prop in sorted(inst.properties.values(), key=lambda p: p.name):
        _write_property(props_el, prop)

    for child in inst.children:
        _write_instance(item, child, doc)


def _write_property(props_el: ET.Element, prop: RbxProperty) -> None:
    """Write a single property value as XML."""
    xml_tag = PROPERTY_FORMAT_TO_XML_TAG.get(prop.fmt, 'string')

    match prop.fmt:
        case PropertyFormat.STRING:
            _write_string_prop(props_el, xml_tag, prop)
        case PropertyFormat.BOOL:
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = 'true' if prop.value else 'false'
        case PropertyFormat.INT | PropertyFormat.ENUM | PropertyFormat.BRICK_COLOR:
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = str(prop.value)
        case PropertyFormat.FLOAT:
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = _fmt_float(prop.value)
        case PropertyFormat.DOUBLE:
            el = _xml_sub_element(props_el, xml_tag)
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
        case PropertyFormat.OPTIONAL_CFRAME:
            _write_optional_cframe(props_el, prop)
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
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = str(prop.value)
        case PropertyFormat.BYTECODE:
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            if isinstance(prop.value, bytes):
                el.text = base64.b64encode(prop.value).decode('ascii')
            else:
                el.text = base64.b64encode(str(prop.value).encode('utf-8')).decode('ascii')
        case PropertyFormat.UNIQUE_ID:
            _write_unique_id(props_el, prop)
        case PropertyFormat.FONT:
            _write_font(props_el, prop)
        case PropertyFormat.SECURITY_CAPABILITIES:
            el = _xml_sub_element(props_el, xml_tag)
            el.set('name', prop.name)
            el.text = str(prop.value)
        case PropertyFormat.CONTENT:
            _write_content(props_el, prop)
        case PropertyFormat.SHARED_STRING:
            _write_shared_string(props_el, prop)
        case _:
            log.warning('Skipping unhandled property format: %s', prop.fmt)


# --- Individual property writers ---
def _has_invalid_xml_chars(s: str) -> bool:
    """Return True if the string contains characters not allowed in XML 1.0."""
    for ch in s:
        codepoint = ord(ch)
        if codepoint < 0x20 and ch not in '\t\n\r':
            return True
        if 0xD800 <= codepoint <= 0xDFFF:
            return True
        if codepoint in {0xFFFE, 0xFFFF}:
            return True
    return False


def _write_string_prop(parent: ET.Element, tag: str, prop: RbxProperty) -> None:
    val = prop.value
    if isinstance(val, bytes) or prop.name in _BINARY_STRING_PROPERTIES:
        # Binary data -> base64 encode it
        raw = val if isinstance(val, bytes) else str(val).encode('utf-8')
        el = _xml_sub_element(parent, 'BinaryString')
        el.set('name', prop.name)
        el.text = base64.b64encode(raw).decode('ascii')
        return

    if not isinstance(val, str):
        val = '' if val is None else str(val)

    if _has_invalid_xml_chars(val):
        # Preserve payload as bytes when text contains XML-illegal code points.
        el = _xml_sub_element(parent, 'BinaryString')
        el.set('name', prop.name)
        el.text = base64.b64encode(val.encode('utf-8', errors='surrogatepass')).decode('ascii')
        return

    if prop.name in {'Source', 'LinkedSource'}:
        el = _xml_sub_element(parent, 'ProtectedString')
        el.set('name', prop.name)
        el.text = val
    # Check if it looks like a content URL
    elif _is_content_url(val, prop.name):
        el = _xml_sub_element(parent, 'Content')
        el.set('name', prop.name)
        if val:
            url_el = _xml_sub_element(el, 'url')
            url_el.text = val
        else:
            _xml_sub_element(el, 'null')
    else:
        el = _xml_sub_element(parent, tag)
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


def _write_udim(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'UDim')
    el.set('name', prop.name)
    _xml_sub_element(el, 'S').text = _fmt_float(prop.value['S'])
    _xml_sub_element(el, 'O').text = str(prop.value['O'])


def _write_udim2(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'UDim2')
    el.set('name', prop.name)
    _xml_sub_element(el, 'XS').text = _fmt_float(prop.value['XS'])
    _xml_sub_element(el, 'XO').text = str(prop.value['XO'])
    _xml_sub_element(el, 'YS').text = _fmt_float(prop.value['YS'])
    _xml_sub_element(el, 'YO').text = str(prop.value['YO'])


def _write_ray(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Ray')
    el.set('name', prop.name)
    origin = _xml_sub_element(el, 'origin')
    _xml_sub_element(origin, 'X').text = _fmt_float(prop.value['origin']['X'])
    _xml_sub_element(origin, 'Y').text = _fmt_float(prop.value['origin']['Y'])
    _xml_sub_element(origin, 'Z').text = _fmt_float(prop.value['origin']['Z'])
    direction = _xml_sub_element(el, 'direction')
    _xml_sub_element(direction, 'X').text = _fmt_float(prop.value['direction']['X'])
    _xml_sub_element(direction, 'Y').text = _fmt_float(prop.value['direction']['Y'])
    _xml_sub_element(direction, 'Z').text = _fmt_float(prop.value['direction']['Z'])


def _write_faces(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Faces')
    el.set('name', prop.name)
    mask = prop.value
    faces: list[str] = []
    face_names = ['Right', 'Top', 'Back', 'Left', 'Bottom', 'Front']
    for i, name in enumerate(face_names):
        if mask & (1 << i):
            faces.append(name)
    el.text = ', '.join(faces) if faces else ''


def _write_axes(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Axes')
    el.set('name', prop.name)
    mask = prop.value
    axes: list[str] = []
    axis_names = ['X', 'Y', 'Z']
    for i, name in enumerate(axis_names):
        if mask & (1 << i):
            axes.append(name)
    el.text = ', '.join(axes) if axes else ''


def _write_color3(parent: ET.Element, tag: str, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, tag)
    el.set('name', prop.name)
    _xml_sub_element(el, 'R').text = _fmt_float(prop.value['R'])
    _xml_sub_element(el, 'G').text = _fmt_float(prop.value['G'])
    _xml_sub_element(el, 'B').text = _fmt_float(prop.value['B'])


def _write_vector2(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Vector2')
    el.set('name', prop.name)
    _xml_sub_element(el, 'X').text = _fmt_float(prop.value['X'])
    _xml_sub_element(el, 'Y').text = _fmt_float(prop.value['Y'])


def _write_vector3(parent: ET.Element, tag: str, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, tag)
    el.set('name', prop.name)
    _xml_sub_element(el, 'X').text = _fmt_float(prop.value['X'])
    _xml_sub_element(el, 'Y').text = _fmt_float(prop.value['Y'])
    _xml_sub_element(el, 'Z').text = _fmt_float(prop.value['Z'])


def _write_vector_int(parent: ET.Element, tag: str, prop: RbxProperty, axes: tuple[str, ...]) -> None:
    el = _xml_sub_element(parent, tag)
    el.set('name', prop.name)
    for axis in axes:
        _xml_sub_element(el, axis).text = str(prop.value[axis])


def _write_cframe(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'CoordinateFrame')
    el.set('name', prop.name)
    cf: dict[str, float] = prop.value
    _xml_sub_element(el, 'X').text = _fmt_float(cf['X'])
    _xml_sub_element(el, 'Y').text = _fmt_float(cf['Y'])
    _xml_sub_element(el, 'Z').text = _fmt_float(cf['Z'])
    for row in range(3):
        for col in range(3):
            key = f'R{row}{col}'
            _xml_sub_element(el, key).text = _fmt_float(cf[key])


def _write_cframe_fields(parent: ET.Element, cf: dict[str, float]) -> None:
    _xml_sub_element(parent, 'X').text = _fmt_float(cf['X'])
    _xml_sub_element(parent, 'Y').text = _fmt_float(cf['Y'])
    _xml_sub_element(parent, 'Z').text = _fmt_float(cf['Z'])
    for row in range(3):
        for col in range(3):
            key = f'R{row}{col}'
            _xml_sub_element(parent, key).text = _fmt_float(cf[key])


def _write_optional_cframe(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'OptionalCoordinateFrame')
    el.set('name', prop.name)
    if prop.value is None:
        return
    cf_el = _xml_sub_element(el, 'CFrame')
    _write_cframe_fields(cf_el, prop.value)


def _write_ref(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Ref')
    el.set('name', prop.name)
    if prop.value is None:
        el.text = 'null'
    else:
        el.text = f'RBX{prop.value:032X}'


def _write_number_sequence(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'NumberSequence')
    el.set('name', prop.name)
    parts: list[str] = [
        f'{_fmt_float(key["Time"])} {_fmt_float(key["Value"])} {_fmt_float(key["Envelope"])}'
        for key in prop.value
    ]
    el.text = ' '.join(parts)


def _write_color_sequence(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'ColorSequence')
    el.set('name', prop.name)
    parts: list[str] = [
        f'{_fmt_float(key["Time"])} {_fmt_float(key["R"])} '
        f'{_fmt_float(key["G"])} {_fmt_float(key["B"])} 0'
        for key in prop.value
    ]
    el.text = ' '.join(parts)


def _write_number_range(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'NumberRange')
    el.set('name', prop.name)
    el.text = f'{_fmt_float(prop.value["Min"])} {_fmt_float(prop.value["Max"])}'


def _write_rect2d(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Rect2D')
    el.set('name', prop.name)
    mn: dict[str, Any] = prop.value['min']
    mx: dict[str, Any] = prop.value['max']
    min_el = _xml_sub_element(el, 'min')
    _xml_sub_element(min_el, 'X').text = _fmt_float(mn['X'])
    _xml_sub_element(min_el, 'Y').text = _fmt_float(mn['Y'])
    max_el = _xml_sub_element(el, 'max')
    _xml_sub_element(max_el, 'X').text = _fmt_float(mx['X'])
    _xml_sub_element(max_el, 'Y').text = _fmt_float(mx['Y'])


def _write_physical_properties(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'PhysicalProperties')
    el.set('name', prop.name)
    if prop.value is None or not prop.value.get('CustomPhysics', True):
        _xml_sub_element(el, 'CustomPhysics').text = 'false'
    else:
        _xml_sub_element(el, 'CustomPhysics').text = 'true'
        _xml_sub_element(el, 'Density').text = _fmt_float(prop.value['Density'])
        _xml_sub_element(el, 'Friction').text = _fmt_float(prop.value['Friction'])
        _xml_sub_element(el, 'Elasticity').text = _fmt_float(prop.value['Elasticity'])
        _xml_sub_element(el, 'FrictionWeight').text = _fmt_float(prop.value['FrictionWeight'])
        _xml_sub_element(el, 'ElasticityWeight').text = _fmt_float(prop.value['ElasticityWeight'])
        _xml_sub_element(el, 'AcousticAbsorption').text = _fmt_float(
            prop.value.get('AcousticAbsorption', 1.0)
        )


def _write_color3uint8(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Color3uint8')
    el.set('name', prop.name)
    # Packed as 0xFFRRGGBB
    r = prop.value['R']
    g = prop.value['G']
    b = prop.value['B']
    packed = 0xFF000000 | (r << 16) | (g << 8) | b
    el.text = str(packed)


def _write_shared_string(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'SharedString')
    el.set('name', prop.name)
    if isinstance(prop.value, bytes):
        # Compute MD5 hash of the raw content, base64-encoded (Studio requires this format)
        md5_b64 = base64.b64encode(hashlib.md5(prop.value, usedforsecurity=False).digest()).decode(
            'ascii'
        )
        b64_content = base64.b64encode(prop.value).decode('ascii')
        # Register in the shared string registry
        _shared_string_registry[md5_b64] = b64_content
        # Property value is the md5 reference, not the data
        el.text = md5_b64
    else:
        el.text = str(prop.value)


def _write_unique_id(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'UniqueId')
    el.set('name', prop.name)
    if isinstance(prop.value, bytes):
        el.text = prop.value.hex()
        return

    random_bits = int(prop.value.get('Random', 0)) & 0xFFFF_FFFF_FFFF_FFFF
    xml_random = ((random_bits << 1) & 0xFFFF_FFFF_FFFF_FFFF) | (random_bits >> 63)
    time = int(prop.value.get('Time', 0)) & 0xFFFF_FFFF
    index = int(prop.value.get('Index', 0)) & 0xFFFF_FFFF
    el.text = f'{xml_random:016x}{time:08x}{index:08x}'


def _write_font(parent: ET.Element, prop: RbxProperty) -> None:
    style_names = {0: 'Normal', 1: 'Italic'}
    el = _xml_sub_element(parent, 'Font')
    el.set('name', prop.name)
    family = _xml_sub_element(el, 'Family')
    _write_content_value(family, prop.value.get('Family', ''))
    _xml_sub_element(el, 'Weight').text = str(prop.value.get('Weight', 400))
    style = prop.value.get('Style', 0)
    _xml_sub_element(el, 'Style').text = style_names.get(style, str(style))
    cached_face_id = prop.value.get('CachedFaceId', '')
    if cached_face_id:
        cached = _xml_sub_element(el, 'CachedFaceId')
        _write_content_value(cached, cached_face_id)


def _write_content(parent: ET.Element, prop: RbxProperty) -> None:
    el = _xml_sub_element(parent, 'Content')
    el.set('name', prop.name)
    _write_content_value(el, prop.value)


def _write_content_value(parent: ET.Element, value: Any) -> None:
    if value is None:
        _xml_sub_element(parent, 'null')
    elif isinstance(value, str):
        if value:
            uri = _xml_sub_element(parent, 'uri')
            uri.text = value
        else:
            _xml_sub_element(parent, 'null')
    elif value.get('SourceType') == 'Uri':
        uri = _xml_sub_element(parent, 'uri')
        uri.text = str(value.get('Uri', ''))
    elif value.get('SourceType') == 'Object':
        ref = _xml_sub_element(parent, 'Ref')
        ref_value = value.get('Ref')
        ref.text = 'null' if ref_value is None else f'RBX{int(ref_value):032X}'
    else:
        _xml_sub_element(parent, 'null')


def _fmt_float(value: Any) -> str:
    """Format a float for XML output, avoiding unnecessary decimals."""
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f'{value:.8g}'
    return str(value)
