"""Data types for the Roblox binary model format."""

import enum
from dataclasses import dataclass, field
from typing import Any


class PropertyFormat(enum.IntEnum):
    """Binary property type IDs from the RBXM format."""

    UNKNOWN = 0
    STRING = 1
    BOOL = 2
    INT = 3
    FLOAT = 4
    DOUBLE = 5
    UDIM = 6
    UDIM2 = 7
    RAY = 8
    FACES = 9
    AXES = 10
    BRICK_COLOR = 11
    COLOR3 = 12
    VECTOR2 = 13
    VECTOR3 = 14
    VECTOR2INT16 = 15
    CFRAME_MATRIX = 16
    CFRAME_QUAT = 17
    ENUM = 18
    REF = 19
    VECTOR3INT16 = 20
    NUMBER_SEQUENCE = 21
    COLOR_SEQUENCE = 22
    NUMBER_RANGE = 23
    RECT2D = 24
    PHYSICAL_PROPERTIES = 25
    COLOR3UINT8 = 26
    INT64 = 27
    SHARED_STRING = 28


# Maps PropertyFormat to the XML tag name used in RBXMX files.
PROPERTY_FORMAT_TO_XML_TAG: dict[PropertyFormat, str] = {
    PropertyFormat.STRING: 'string',
    PropertyFormat.BOOL: 'bool',
    PropertyFormat.INT: 'int',
    PropertyFormat.FLOAT: 'float',
    PropertyFormat.DOUBLE: 'double',
    PropertyFormat.UDIM: 'UDim',
    PropertyFormat.UDIM2: 'UDim2',
    PropertyFormat.RAY: 'Ray',
    PropertyFormat.FACES: 'Faces',
    PropertyFormat.AXES: 'Axes',
    PropertyFormat.BRICK_COLOR: 'BrickColor',
    PropertyFormat.COLOR3: 'Color3',
    PropertyFormat.VECTOR2: 'Vector2',
    PropertyFormat.VECTOR3: 'Vector3',
    PropertyFormat.VECTOR2INT16: 'Vector2int16',
    PropertyFormat.CFRAME_MATRIX: 'CoordinateFrame',
    PropertyFormat.CFRAME_QUAT: 'CoordinateFrame',
    PropertyFormat.ENUM: 'token',
    PropertyFormat.REF: 'Ref',
    PropertyFormat.VECTOR3INT16: 'Vector3int16',
    PropertyFormat.NUMBER_SEQUENCE: 'NumberSequence',
    PropertyFormat.COLOR_SEQUENCE: 'ColorSequence',
    PropertyFormat.NUMBER_RANGE: 'NumberRange',
    PropertyFormat.RECT2D: 'Rect2D',
    PropertyFormat.PHYSICAL_PROPERTIES: 'PhysicalProperties',
    PropertyFormat.COLOR3UINT8: 'Color3uint8',
    PropertyFormat.INT64: 'int64',
    PropertyFormat.SHARED_STRING: 'SharedString',
}


@dataclass
class RbxProperty:
    """A single property on an instance."""

    name: str
    fmt: PropertyFormat
    value: Any


@dataclass
class RbxInstance:
    """A Roblox instance (object) with properties and children."""

    class_name: str
    referent: int
    properties: dict[str, RbxProperty] = field(default_factory=dict[str, RbxProperty])
    children: list['RbxInstance'] = field(default_factory=list['RbxInstance'])  # noqa: UP037
    is_service: bool = False


@dataclass
class RbxMetadata:
    """Metadata from the META chunk."""

    entries: dict[str, str] = field(default_factory=dict[str, str])


@dataclass
class RbxTypeInfo:
    """Info about an instance class from the INST chunk."""

    type_index: int
    class_name: str
    is_service: bool
    instance_ids: list[int]


@dataclass
class RbxDocument:
    """A parsed Roblox binary model file."""

    version: int
    type_count: int
    object_count: int
    metadata: RbxMetadata
    instances: dict[int, RbxInstance]  # id -> instance
    roots: list[RbxInstance]  # top-level instances (no parent)
    shared_strings: list[bytes] = field(default_factory=list[bytes])
