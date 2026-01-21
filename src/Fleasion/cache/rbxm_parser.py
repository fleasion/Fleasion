"""RBXM binary format parser for Roblox animation files.

Based on the specification at http://dom.rojo.space/binary.html
"""

import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
import lz4.block


# Magic header for RBXM files
RBXM_MAGIC = b'<roblox!'
RBXM_SIGNATURE = bytes([0x89, 0xFF, 0x0D, 0x0A, 0x1A, 0x0A])


@dataclass
class RbxmInstance:
    """Represents a Roblox instance."""
    class_name: str
    referent: int
    properties: Dict[str, Any] = field(default_factory=dict)
    children: List['RbxmInstance'] = field(default_factory=list)
    parent: Optional['RbxmInstance'] = None


def decode_interleaved_i32(data: bytes, count: int) -> List[int]:
    """Decode interleaved 32-bit integers."""
    if len(data) < count * 4:
        return []

    result = []
    for i in range(count):
        # De-interleave: bytes are stored column-wise
        b0 = data[i]
        b1 = data[count + i]
        b2 = data[count * 2 + i]
        b3 = data[count * 3 + i]

        # Reconstruct big-endian value
        value = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3

        # Decode transformed integer (rotate right by 1, then negate if odd)
        if value & 1:
            value = -((value >> 1) + 1)
        else:
            value = value >> 1

        result.append(value)

    return result


def decode_interleaved_f32(data: bytes, count: int) -> List[float]:
    """Decode interleaved 32-bit floats with Roblox's custom encoding."""
    if len(data) < count * 4:
        return []

    result = []
    for i in range(count):
        # De-interleave
        b0 = data[i]
        b1 = data[count + i]
        b2 = data[count * 2 + i]
        b3 = data[count * 3 + i]

        # Reconstruct and convert from Roblox format to IEEE-754
        # Roblox uses a rotated format where sign bit is at LSB
        raw = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3

        # Rotate right by 1 to get standard IEEE-754 format
        ieee = ((raw >> 1) | ((raw & 1) << 31)) & 0xFFFFFFFF

        # Convert to float
        result.append(struct.unpack('<f', struct.pack('<I', ieee))[0])

    return result


def read_string(data: bytes, offset: int) -> Tuple[str, int]:
    """Read a length-prefixed string."""
    length = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    value = data[offset:offset + length].decode('utf-8', errors='replace')
    return value, offset + length


def decompress_chunk(data: bytes, compressed_size: int, uncompressed_size: int) -> bytes:
    """Decompress a chunk using LZ4."""
    if compressed_size == 0:
        return data[:uncompressed_size]

    try:
        return lz4.block.decompress(data[:compressed_size], uncompressed_size=uncompressed_size)
    except Exception:
        # Try without size hint
        try:
            return lz4.block.decompress(data[:compressed_size])
        except Exception:
            # Return raw data if decompression fails
            return data[:compressed_size]


# Predefined CFrame rotation matrices (IDs 0x02-0x17)
CFRAME_ROTATIONS = {
    0x02: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    0x03: [1, 0, 0, 0, 0, -1, 0, 1, 0],
    0x04: [1, 0, 0, 0, -1, 0, 0, 0, -1],
    0x05: [1, 0, 0, 0, 0, 1, 0, -1, 0],
    0x06: [0, 1, 0, 1, 0, 0, 0, 0, -1],
    0x07: [0, 0, 1, 1, 0, 0, 0, 1, 0],
    0x08: [0, -1, 0, 1, 0, 0, 0, 0, 1],
    0x09: [0, 0, -1, 1, 0, 0, 0, -1, 0],
    0x0A: [0, 1, 0, 0, 0, 1, 1, 0, 0],
    0x0B: [0, 0, -1, 0, 1, 0, 1, 0, 0],
    0x0C: [0, -1, 0, 0, 0, -1, 1, 0, 0],
    0x0D: [0, 0, 1, 0, -1, 0, 1, 0, 0],
    0x0E: [-1, 0, 0, 0, 1, 0, 0, 0, -1],
    0x0F: [-1, 0, 0, 0, 0, 1, 0, 1, 0],
    0x10: [-1, 0, 0, 0, -1, 0, 0, 0, 1],
    0x11: [-1, 0, 0, 0, 0, -1, 0, -1, 0],
    0x12: [0, 1, 0, -1, 0, 0, 0, 0, 1],
    0x13: [0, 0, -1, -1, 0, 0, 0, 1, 0],
    0x14: [0, -1, 0, -1, 0, 0, 0, 0, -1],
    0x15: [0, 0, 1, -1, 0, 0, 0, -1, 0],
    0x16: [0, 1, 0, 0, 0, -1, -1, 0, 0],
    0x17: [0, 0, 1, 0, 1, 0, -1, 0, 0],
    0x18: [0, -1, 0, 0, 0, 1, -1, 0, 0],
    0x19: [0, 0, -1, 0, -1, 0, -1, 0, 0],
}


def parse_rbxm(data: bytes) -> Dict[int, RbxmInstance]:
    """
    Parse RBXM binary data.

    Returns a dictionary mapping referents to instances.
    """
    if len(data) < 32:
        raise ValueError('File too small to be valid RBXM')

    # Check magic header
    if not data.startswith(RBXM_MAGIC):
        raise ValueError('Invalid RBXM magic header')

    # Parse header
    offset = 8
    signature = data[offset:offset + 6]
    offset += 6

    version = struct.unpack_from('<H', data, offset)[0]
    offset += 2

    class_count = struct.unpack_from('<i', data, offset)[0]
    offset += 4

    instance_count = struct.unpack_from('<i', data, offset)[0]
    offset += 4

    # Skip reserved bytes
    offset += 8

    # Storage for parsing
    class_info: Dict[int, Tuple[str, List[int]]] = {}  # class_id -> (class_name, referents)
    instances: Dict[int, RbxmInstance] = {}  # referent -> instance
    parent_refs: Dict[int, int] = {}  # child_ref -> parent_ref

    # Parse chunks
    while offset < len(data):
        if offset + 16 > len(data):
            break

        # Read chunk header
        chunk_name = data[offset:offset + 4].decode('ascii', errors='replace').rstrip('\x00')
        offset += 4

        compressed_size = struct.unpack_from('<I', data, offset)[0]
        offset += 4

        uncompressed_size = struct.unpack_from('<I', data, offset)[0]
        offset += 4

        reserved = struct.unpack_from('<I', data, offset)[0]
        offset += 4

        # Get chunk data
        if compressed_size == 0:
            chunk_data = data[offset:offset + uncompressed_size]
            offset += uncompressed_size
        else:
            chunk_data = decompress_chunk(data[offset:], compressed_size, uncompressed_size)
            offset += compressed_size

        # Process chunk
        if chunk_name == 'INST':
            _parse_inst_chunk(chunk_data, class_info, instances)
        elif chunk_name == 'PROP':
            _parse_prop_chunk(chunk_data, class_info, instances)
        elif chunk_name == 'PRNT':
            _parse_prnt_chunk(chunk_data, instances, parent_refs)
        elif chunk_name == 'END\x00' or chunk_name == 'END':
            break

    # Build parent-child relationships
    for child_ref, parent_ref in parent_refs.items():
        if child_ref in instances:
            child = instances[child_ref]
            if parent_ref >= 0 and parent_ref in instances:
                parent = instances[parent_ref]
                parent.children.append(child)
                child.parent = parent

    return instances


def _parse_inst_chunk(data: bytes, class_info: Dict, instances: Dict):
    """Parse an INST chunk."""
    offset = 0

    class_id = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    class_name, offset = read_string(data, offset)

    object_format = data[offset]
    offset += 1

    instance_count = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    # Read referents (interleaved i32)
    referents_data = data[offset:offset + instance_count * 4]
    referent_deltas = decode_interleaved_i32(referents_data, instance_count)

    # Convert deltas to absolute referents
    referents = []
    current = 0
    for delta in referent_deltas:
        current += delta
        referents.append(current)

    # Store class info and create instances
    class_info[class_id] = (class_name, referents)

    for ref in referents:
        instances[ref] = RbxmInstance(class_name=class_name, referent=ref)


def _parse_prop_chunk(data: bytes, class_info: Dict, instances: Dict):
    """Parse a PROP chunk."""
    offset = 0

    class_id = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    prop_name, offset = read_string(data, offset)

    type_id = data[offset]
    offset += 1

    if class_id not in class_info:
        return

    class_name, referents = class_info[class_id]
    count = len(referents)

    if count == 0:
        return

    # Parse values based on type
    values = _parse_prop_values(data[offset:], type_id, count)

    # Assign values to instances
    for i, ref in enumerate(referents):
        if ref in instances and i < len(values):
            instances[ref].properties[prop_name] = values[i]


def _parse_prop_values(data: bytes, type_id: int, count: int) -> List[Any]:
    """Parse property values based on type ID."""
    values = []

    if type_id == 0x01:  # String
        offset = 0
        for _ in range(count):
            if offset >= len(data):
                values.append('')
                continue
            s, offset = read_string(data, offset)
            values.append(s)

    elif type_id == 0x02:  # Bool
        for i in range(count):
            if i < len(data):
                values.append(bool(data[i]))
            else:
                values.append(False)

    elif type_id == 0x03:  # Int32
        values = decode_interleaved_i32(data, count)

    elif type_id == 0x04:  # Float32
        values = decode_interleaved_f32(data, count)

    elif type_id == 0x05:  # Float64
        for i in range(count):
            offset = i * 8
            if offset + 8 <= len(data):
                values.append(struct.unpack_from('<d', data, offset)[0])
            else:
                values.append(0.0)

    elif type_id == 0x10:  # CFrame
        values = _parse_cframes(data, count)

    else:
        # Unknown type, return empty values
        values = [None] * count

    return values


def _parse_cframes(data: bytes, count: int) -> List[Dict]:
    """Parse CFrame values."""
    offset = 0
    cframes = []
    rotation_data = []

    # First, read rotation IDs and custom rotations
    for _ in range(count):
        if offset >= len(data):
            rotation_data.append((0x02, CFRAME_ROTATIONS[0x02]))
            continue

        rot_id = data[offset]
        offset += 1

        if rot_id == 0x00:
            # Custom rotation matrix (9 floats)
            if offset + 36 <= len(data):
                rot = list(struct.unpack_from('<9f', data, offset))
                offset += 36
            else:
                rot = [1, 0, 0, 0, 1, 0, 0, 0, 1]
            rotation_data.append((rot_id, rot))
        else:
            # Predefined rotation
            rot = CFRAME_ROTATIONS.get(rot_id, [1, 0, 0, 0, 1, 0, 0, 0, 1])
            rotation_data.append((rot_id, rot))

    # Now read positions (interleaved Vector3s = 3 * count floats)
    positions_x = decode_interleaved_f32(data[offset:], count)
    offset += count * 4
    positions_y = decode_interleaved_f32(data[offset:], count)
    offset += count * 4
    positions_z = decode_interleaved_f32(data[offset:], count)

    # Build CFrame dictionaries
    for i in range(count):
        rot_id, rot = rotation_data[i] if i < len(rotation_data) else (0x02, CFRAME_ROTATIONS[0x02])
        x = positions_x[i] if i < len(positions_x) else 0.0
        y = positions_y[i] if i < len(positions_y) else 0.0
        z = positions_z[i] if i < len(positions_z) else 0.0

        cframes.append({
            'position': (x, y, z),
            'rotation': rot
        })

    return cframes


def _parse_prnt_chunk(data: bytes, instances: Dict, parent_refs: Dict):
    """Parse a PRNT chunk."""
    offset = 0

    # Skip version byte
    offset += 1

    count = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    # Read child referents
    children = decode_interleaved_i32(data[offset:], count)
    offset += count * 4

    # Read parent referents
    parents = decode_interleaved_i32(data[offset:], count)

    # Convert deltas to absolute values
    child_refs = []
    parent_ref_list = []

    child_current = 0
    parent_current = 0

    for i in range(min(len(children), len(parents))):
        child_current += children[i]
        parent_current += parents[i]
        child_refs.append(child_current)
        parent_ref_list.append(parent_current)

    # Store parent relationships
    for i in range(len(child_refs)):
        parent_refs[child_refs[i]] = parent_ref_list[i]


def get_root_instances(instances: Dict[int, RbxmInstance]) -> List[RbxmInstance]:
    """Get all root instances (those without parents)."""
    return [inst for inst in instances.values() if inst.parent is None]


def find_by_class(instances: Dict[int, RbxmInstance], class_name: str) -> List[RbxmInstance]:
    """Find all instances of a given class."""
    return [inst for inst in instances.values() if inst.class_name == class_name]
