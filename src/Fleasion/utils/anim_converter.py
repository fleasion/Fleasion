"""Roblox animation utilities: rig detection, binary-to-XML conversion,
and CurveAnimation -> KeyframeSequence conversion.

The curve-to-keyframe logic is ported from curve_to_keyframe.py and supports
SharedStrings, markers, FaceControls / NumberPose, and full pose interpolation.
"""

from __future__ import annotations

import base64
import io
import math
import struct
import uuid
import xml.etree.ElementTree as ET

# Signature sets used for rig auto-detection
_R6_SIGNS  = {'Left Arm', 'Right Arm', 'Left Leg', 'Right Leg', 'Torso'}
_R15_SIGNS = {'LeftUpperArm', 'RightUpperArm', 'LowerTorso', 'UpperTorso', 'LeftUpperLeg'}

# Complete set of valid player body part names for both rigs.
# Any pose name outside this set indicates a non-player / mixed animation.
_ALL_PLAYER_PARTS: frozenset[str] = frozenset({
    # shared
    'HumanoidRootPart', 'Head',
    # R6
    'Torso', 'Left Arm', 'Right Arm', 'Left Leg', 'Right Leg',
    # R15
    'UpperTorso', 'LowerTorso',
    'LeftUpperArm', 'LeftLowerArm', 'LeftHand',
    'RightUpperArm', 'RightLowerArm', 'RightHand',
    'LeftUpperLeg', 'LeftLowerLeg', 'LeftFoot',
    'RightUpperLeg', 'RightLowerLeg', 'RightFoot',
})

# Byte-level signatures for fast rig detection (work in both XML and binary RBXM)
_R15_BYTES = (b'LeftUpperArm', b'LowerTorso', b'UpperTorso', b'LeftUpperLeg')

def detect_rig_fast(data: bytes) -> str:
    if any(b in data for b in _R15_BYTES):
        return 'R15'
    # After the R15 check, b'Torso' can only match the standalone R6 'Torso' part.
    if b'Torso' in data:
        return 'R6'
    return 'unknown'


def is_curve_animation(data: bytes) -> bool:
    """Return True if the bytes represent a CurveAnimation asset (binary or XML)."""
    return b'CurveAnimation' in data


def detect_rig(data: bytes) -> str:
    """Return 'R6', 'R15', or 'unknown' by fully parsing the animation.

    Conservative: returns 'unknown' if any pose name falls outside the standard
    player body part set.  This keeps mixed/tool animations (e.g. a gun that
    also moves Left Arm) from matching R6Animation / R15Animation virtual
    replacement filters.  Use detect_player_rig() when you need rig info for
    conversion even on mixed animations.
    """
    try:
        from ..cache.animation_viewer import load_animation_data
        keyframes = load_animation_data(data)
        if not keyframes:
            return 'unknown'
        names: set[str] = set()
        for kf in keyframes:
            names.update(kf.pose_by_part_name.keys())
        if names - _ALL_PLAYER_PARTS:
            return 'unknown'
        if names & _R6_SIGNS:
            return 'R6'
        if names & _R15_SIGNS:
            return 'R15'
    except Exception:
        pass
    return 'unknown'


def detect_player_rig(data: bytes) -> str:
    """Like detect_rig but returns R6/R15 even for mixed animations.

    Use this when you need to know which player rig a non-player animation
    targets (e.g. a gun animation that moves Left Arm -> R6) so the correct
    version of a replacement can be served.  Returns 'unknown' only when no
    player body part names are present at all.
    """
    try:
        from ..cache.animation_viewer import load_animation_data
        keyframes = load_animation_data(data)
        if not keyframes:
            return 'unknown'
        names: set[str] = set()
        for kf in keyframes:
            names.update(kf.pose_by_part_name.keys())
        if names & _R6_SIGNS:
            return 'R6'
        if names & _R15_SIGNS:
            return 'R15'
    except Exception:
        pass
    return 'unknown'


def rbxm_to_rbxmx(data: bytes) -> bytes:
    """Convert binary .rbxm bytes to .rbxmx XML bytes."""
    from ..cache.tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer
    from ..cache.tools.solidmodel_converter.rbxm.xml_writer import write_rbxmx as _write
    return _write(RbxmDeserializer().deserialize(data))


# Lightweight instance model (ported from curve_to_keyframe.py)

class _Instance:
    __slots__ = ('class_name', 'name', 'properties', 'children', 'parent')

    def __init__(self, class_name: str):
        self.class_name: str = class_name
        self.name: str = ''
        self.properties: dict = {}
        self.children: list = []
        self.parent = None

    def add_child(self, child: '_Instance'):
        child.parent = self
        self.children.append(child)

    def get_descendants(self) -> list:
        result = []
        stack = list(self.children)
        while stack:
            inst = stack.pop()
            result.append(inst)
            stack.extend(inst.children)
        return result

    def find_first_child(self, name: str):
        for c in self.children:
            if c.name == name:
                return c
        return None


# Binary key parsing

def _parse_float_curve_binary(data: bytes) -> list:
    """Decode binary FloatCurve ValuesAndTimes -> list of {'Time': float, 'Value': float}."""
    HDR        = 8
    KEY_STRIDE = 14
    TIME_HDR   = 8
    TIME_SCALE = 14400.0

    if len(data) < HDR:
        return []
    key_count = struct.unpack_from('<I', data, 4)[0]
    if key_count == 0:
        return []

    times_base = HDR + key_count * KEY_STRIDE + TIME_HDR
    if times_base + key_count * 4 > len(data):
        return []

    keys = []
    for i in range(key_count):
        k_off = HDR + i * KEY_STRIDE
        t_off = times_base + i * 4
        value     = struct.unpack_from('<f', data, k_off + 2)[0]
        time_unit = struct.unpack_from('<I', data, t_off)[0]
        keys.append({'Time': time_unit / TIME_SCALE, 'Value': value})
    return keys


# .rbxmx parser (in-memory)

def _load_rbxmx_instances(source) -> tuple:
    """Parse .rbxmx XML bytes or filepath into (list[_Instance], shared_strings dict)."""
    if isinstance(source, (bytes, bytearray)):
        tree = ET.parse(io.BytesIO(source))
    else:
        tree = ET.parse(source)
    root = tree.getroot()

    shared_strings: dict = {}
    ss_root = root.find('SharedStrings')
    if ss_root is not None:
        for ss in ss_root.findall('SharedString'):
            md5 = ss.get('md5', '')
            text = (ss.text or '').strip()
            if md5 and text:
                try:
                    shared_strings[md5] = base64.b64decode(text)
                except Exception:
                    pass

    def parse_props(props_elem, inst: _Instance):
        for prop in props_elem:
            pname = prop.get('name', '')
            tag   = prop.tag
            text  = (prop.text or '').strip()

            if tag == 'string':
                inst.properties[pname] = prop.text or ''
                if pname == 'Name':
                    inst.name = prop.text or ''
            elif tag == 'bool':
                inst.properties[pname] = text.lower() == 'true'
            elif tag in ('int', 'token'):
                try:
                    inst.properties[pname] = int(text)
                except ValueError:
                    inst.properties[pname] = 0
            elif tag in ('float', 'double'):
                try:
                    inst.properties[pname] = float(text)
                except ValueError:
                    inst.properties[pname] = 0.0
            elif tag == 'BinaryString':
                try:
                    inst.properties[pname] = base64.b64decode(text) if text else b''
                except Exception:
                    inst.properties[pname] = b''
            elif tag == 'SharedString':
                inst.properties[pname] = shared_strings.get(text, b'')
            elif tag == 'CoordinateFrame':
                cf = {}
                for child in prop:
                    try:
                        cf[child.tag] = float(child.text or '0')
                    except ValueError:
                        cf[child.tag] = 0.0
                inst.properties[pname] = cf
            else:
                inst.properties[pname] = prop.text

    def parse_item(xml_item) -> _Instance:
        inst = _Instance(xml_item.get('class', 'Instance'))
        props = xml_item.find('Properties')
        if props is not None:
            parse_props(props, inst)
        for child_xml in xml_item.findall('Item'):
            inst.add_child(parse_item(child_xml))
        return inst

    return [parse_item(item) for item in root.findall('Item')], shared_strings


# Key helpers

def _get_float_curve_keys(inst: _Instance) -> list:
    data = inst.properties.get('ValuesAndTimes', b'')
    if isinstance(data, bytes) and data:
        return _parse_float_curve_binary(data)
    return []


def _get_axis_keys(curve_inst: _Instance, axis: str) -> list:
    child = curve_inst.find_first_child(axis)
    if child is not None and child.class_name == 'FloatCurve':
        return _get_float_curve_keys(child)
    return []


# Pose mapping

def _map_poses(curve_anim: _Instance):
    pose_map: dict         = {}
    face_control_map: dict = {}
    times: set             = set()

    for curve in curve_anim.get_descendants():
        cls = curve.class_name
        if cls == 'Vector3Curve':
            curve_type = 'Position'
        elif cls == 'EulerRotationCurve':
            curve_type = 'Rotation'
        elif cls == 'FloatCurve':
            curve_type = 'FaceControl'
        else:
            continue

        pose_name = curve.parent.name if curve.parent else ''

        if curve_type in ('Position', 'Rotation'):
            pose_map.setdefault(pose_name, {})
            for axis in ('X', 'Y', 'Z'):
                for key in _get_axis_keys(curve, axis):
                    t, v = key['Time'], key['Value']
                    times.add(t)
                    pose_map[pose_name].setdefault(t, {'Position': {}, 'Rotation': {}})
                    pose_map[pose_name][t][curve_type][axis] = v
        else:  # FaceControl
            fc_name = curve.name
            face_control_map.setdefault(fc_name, {})
            for key in _get_float_curve_keys(curve):
                t, v = key['Time'], key['Value']
                times.add(t)
                face_control_map[fc_name][t] = v

    return pose_map, sorted(times), face_control_map


# Interpolation

def _interpolate_values(final_values: dict, pose_name: str,
                        pose_time: float, pose_map: dict, key_times: list):
    def doit(value_type: str, axis: str):
        prefix = 'P' if value_type == 'Position' else 'R'
        k = prefix + axis
        if final_values.get(k) is not None:
            return

        prev_t = next_t = None
        for t in key_times:
            if t < pose_time:
                if (pose_name in pose_map
                        and t in pose_map[pose_name]
                        and axis in pose_map[pose_name][t].get(value_type, {})):
                    prev_t = t
            elif t > pose_time:
                if (pose_name in pose_map
                        and t in pose_map[pose_name]
                        and axis in pose_map[pose_name][t].get(value_type, {})):
                    next_t = t
                    break

        if prev_t is None and next_t is None:
            final_values[k] = 0.0
        elif prev_t is not None and next_t is not None:
            pv = pose_map[pose_name][prev_t][value_type][axis]
            nv = pose_map[pose_name][next_t][value_type][axis]
            p  = (pose_time - prev_t) / (next_t - prev_t)
            final_values[k] = pv + (nv - pv) * p
        elif prev_t is not None:
            final_values[k] = pose_map[pose_name][prev_t][value_type][axis]
        else:
            final_values[k] = pose_map[pose_name][next_t][value_type][axis]

    for vt in ('Position', 'Rotation'):
        for ax in ('X', 'Y', 'Z'):
            doit(vt, ax)


# CFrame math

def _euler_xyz_to_rotation_matrix(rx: float, ry: float, rz: float) -> list:
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return [
         cy*cz,              -cy*sz,              sy,
         sx*sy*cz + cx*sz,   -sx*sy*sz + cx*cz,   -sx*cy,
        -cx*sy*cz + sx*sz,    cx*sy*sz + sx*cz,    cx*cy,
    ]


# Marker parsing

def _parse_markers(data: bytes, curve_name: str) -> list:
    markers = []
    offset  = 0
    try:
        count = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        if count > 10_000:
            return []
        for _ in range(count):
            if offset + 8 > len(data):
                break
            t       = struct.unpack_from('<f', data, offset)[0];  offset += 4
            str_len = struct.unpack_from('<I', data, offset)[0];  offset += 4
            if offset + str_len > len(data):
                break
            value   = data[offset:offset + str_len].decode('utf-8', errors='replace')
            offset += str_len
            markers.append((t, curve_name, value))
    except (struct.error, UnicodeDecodeError):
        pass
    return markers


def _handle_markers(kf_seq: _Instance, curve_anim: _Instance, kf_by_time: dict):
    for mc in curve_anim.get_descendants():
        if mc.class_name != 'MarkerCurve':
            continue
        data = mc.properties.get('Markers', b'')
        if not isinstance(data, bytes) or len(data) < 4:
            continue
        for t, name, value in _parse_markers(data, mc.name):
            kf = kf_by_time.get(t)
            if kf is None:
                kf = _make_keyframe(t)
                kf_seq.add_child(kf)
                kf_by_time[t] = kf
            marker = _Instance('KeyframeMarker')
            marker.name = name
            marker.properties['Name']  = name
            marker.properties['Value'] = value
            kf.add_child(marker)


# Conversion

def _make_keyframe(t: float) -> _Instance:
    kf = _Instance('Keyframe')
    kf.name               = 'Keyframe'
    kf.properties['Name'] = 'Keyframe'
    kf.properties['Time'] = t
    return kf


def _convert_curve_anim(curve_anim: _Instance) -> _Instance:
    """Convert a CurveAnimation _Instance to a KeyframeSequence _Instance."""
    kf_seq = _Instance('KeyframeSequence')
    kf_seq.name                   = curve_anim.name
    kf_seq.properties['Name']     = curve_anim.name
    kf_seq.properties['Loop']     = curve_anim.properties.get('Loop', False)
    kf_seq.properties['Priority'] = curve_anim.properties.get('Priority', 2)

    pose_map, key_times, face_control_map = _map_poses(curve_anim)

    kf_by_time:      dict = {}
    name_pose_pairs: dict = {}

    for t in key_times:
        kf = _make_keyframe(t)
        kf_seq.add_child(kf)
        kf_by_time[t] = kf

    def _is_face_ctrl_curve(folder: _Instance) -> bool:
        return (folder.parent is not None
                and folder.parent.name == 'FaceControls'
                and folder.class_name == 'FloatCurve')

    def build_hierarchy(t: float, folder: _Instance, parent: _Instance):
        if not (folder.class_name == 'Folder' or _is_face_ctrl_curve(folder)):
            return

        if folder.name == 'FaceControls':
            pose = _Instance('Folder')
            pose.name               = 'FaceControls'
            pose.properties['Name'] = 'FaceControls'
        elif _is_face_ctrl_curve(folder):
            pose = _Instance('NumberPose')
            pose.name                = folder.name
            pose.properties['Name']  = folder.name
            pose.properties['Weight']= 0.0
            pose.properties['Value'] = 0.0
        else:
            pose = _Instance('Pose')
            pose.name                = folder.name
            pose.properties['Name']  = folder.name
            pose.properties['Weight']= 0.0
            pose.properties['CFrame']= {
                'X': 0, 'Y': 0, 'Z': 0,
                'R00': 1, 'R01': 0, 'R02': 0,
                'R10': 0, 'R11': 1, 'R12': 0,
                'R20': 0, 'R21': 0, 'R22': 1,
            }

        parent.add_child(pose)
        name_pose_pairs.setdefault(folder.name, {})[t] = pose

        for child in folder.children:
            build_hierarchy(t, child, pose)

    for t in key_times:
        for folder in curve_anim.children:
            build_hierarchy(t, folder, kf_by_time[t])

    for t in key_times:
        for pose_name, pt in name_pose_pairs.items():
            if t not in pt:
                continue
            pose = pt[t]

            if pose_name in face_control_map:
                if t in face_control_map[pose_name]:
                    pose.properties['Value']  = face_control_map[pose_name][t]
                    pose.properties['Weight'] = 1.0
                continue

            if pose_name not in pose_map or t not in pose_map[pose_name]:
                continue

            entry = pose_map[pose_name][t]
            pos   = entry.get('Position', {})
            rot   = entry.get('Rotation', {})

            fv = {
                'PX': pos.get('X'), 'PY': pos.get('Y'), 'PZ': pos.get('Z'),
                'RX': rot.get('X'), 'RY': rot.get('Y'), 'RZ': rot.get('Z'),
            }
            _interpolate_values(fv, pose_name, t, pose_map, key_times)

            for k in fv:
                if fv[k] is None:
                    fv[k] = 0.0

            rm = _euler_xyz_to_rotation_matrix(fv['RX'], fv['RY'], fv['RZ'])
            pose.properties['Weight'] = 1.0
            pose.properties['CFrame'] = {
                'X':   fv['PX'], 'Y':   fv['PY'], 'Z':   fv['PZ'],
                'R00': rm[0], 'R01': rm[1], 'R02': rm[2],
                'R10': rm[3], 'R11': rm[4], 'R12': rm[5],
                'R20': rm[6], 'R21': rm[7], 'R22': rm[8],
            }

    _handle_markers(kf_seq, curve_anim, kf_by_time)
    return kf_seq


# .rbxmx writer (in-memory)

def _new_ref() -> str:
    return 'RBX' + uuid.uuid4().hex.upper()


def _instance_to_xml(inst: _Instance, parent_elem: ET.Element):
    item = ET.SubElement(parent_elem, 'Item')
    item.set('class', inst.class_name)
    item.set('referent', _new_ref())

    props = ET.SubElement(item, 'Properties')

    for pname, val in inst.properties.items():
        if pname == 'Name':
            ET.SubElement(props, 'string',  attrib={'name': 'Name'}).text  = str(val)
        elif pname == 'Loop':
            ET.SubElement(props, 'bool',    attrib={'name': 'Loop'}).text  = 'true' if val else 'false'
        elif pname == 'Priority':
            ET.SubElement(props, 'token',   attrib={'name': 'Priority'}).text = str(val)
        elif pname == 'Time' and inst.class_name == 'Keyframe':
            ET.SubElement(props, 'float',   attrib={'name': 'Time'}).text  = str(val)
        elif pname == 'Weight':
            ET.SubElement(props, 'float',   attrib={'name': 'Weight'}).text= str(val)
        elif pname == 'Value' and inst.class_name == 'NumberPose':
            ET.SubElement(props, 'float',   attrib={'name': 'Value'}).text = str(val)
        elif pname == 'Value' and inst.class_name == 'KeyframeMarker':
            ET.SubElement(props, 'string',  attrib={'name': 'Value'}).text = str(val)
        elif pname == 'CFrame' and isinstance(val, dict):
            cf = ET.SubElement(props, 'CoordinateFrame', attrib={'name': 'CFrame'})
            for comp in ('X', 'Y', 'Z',
                         'R00', 'R01', 'R02',
                         'R10', 'R11', 'R12',
                         'R20', 'R21', 'R22'):
                ET.SubElement(cf, comp).text = str(val.get(comp, 0))

    for child in inst.children:
        _instance_to_xml(child, item)


def _instances_to_rbxmx_bytes(instances: list) -> bytes:
    root = ET.Element('roblox')
    root.set('xmlns:xmime', 'http://www.w3.org/2005/05/xmlmime')
    root.set('xmlns:xsi',   'http://www.w3.org/2001/XMLSchema-instance')
    root.set('xsi:noNamespaceSchemaLocation', 'http://www.roblox.com/roblox.xsd')
    root.set('version', '4')

    for inst in instances:
        _instance_to_xml(inst, root)

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space='  ')
    except AttributeError:
        pass

    buf = io.StringIO()
    tree.write(buf, encoding='unicode', xml_declaration=True)
    return buf.getvalue().encode('utf-8')


# Public entry point

def curve_anim_to_keyframe(data: bytes) -> bytes:
    """Convert a CurveAnimation (binary RBXM or XML bytes) to KeyframeSequence RBXMX bytes.

    Handles SharedStrings, FaceControls/NumberPose, KeyframeMarkers, and full
    pose interpolation for missing axes.
    """
    # Convert binary RBXM → XML first if needed
    if data[:10].startswith(b'<roblox!\x89\xff'):
        data = rbxm_to_rbxmx(data)

    instances, _ = _load_rbxmx_instances(data)

    output = []
    found  = 0
    for inst in instances:
        if inst.class_name == 'CurveAnimation':
            output.append(_convert_curve_anim(inst))
            found += 1
        else:
            output.append(inst)

    if found == 0:
        raise ValueError('No CurveAnimation found in data')

    return _instances_to_rbxmx_bytes(output)
