"""R15 <-> R6 Roblox animation converter (library module).

Conversion algorithms ported from:
  R15 -> R6: Drone's animation converter plugin
  R6 -> R15: andy_wirus's 6to15 plugin
"""

import base64
import math
import struct
import xml.etree.ElementTree as ET
import re


# CFrame math

IDENTITY = [0., 0., 0.,  1., 0., 0.,  0., 1., 0.,  0., 0., 1.]


def parse_cf(elem):
    d = {c.tag: float(c.text or '0') for c in elem}
    return [d.get('X',0.),  d.get('Y',0.),  d.get('Z',0.),
            d.get('R00',1.), d.get('R01',0.), d.get('R02',0.),
            d.get('R10',0.), d.get('R11',1.), d.get('R12',0.),
            d.get('R20',0.), d.get('R21',0.), d.get('R22',1.)]


def write_cf(elem, cf):
    for c in list(elem): elem.remove(c)
    for tag, v in zip(['X','Y','Z','R00','R01','R02','R10','R11','R12','R20','R21','R22'], cf):
        e = ET.SubElement(elem, tag)
        e.text = f'{v:.8g}'


def cf_mul(a, b):
    px = a[0] + a[3]*b[0] + a[4]*b[1] + a[5]*b[2]
    py = a[1] + a[6]*b[0] + a[7]*b[1] + a[8]*b[2]
    pz = a[2] + a[9]*b[0] + a[10]*b[1] + a[11]*b[2]
    ar = (a[3:6], a[6:9], a[9:12])
    br = (b[3:6], b[6:9], b[9:12])
    r = [[sum(ar[i][k]*br[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return [px, py, pz,
            r[0][0], r[0][1], r[0][2],
            r[1][0], r[1][1], r[1][2],
            r[2][0], r[2][1], r[2][2]]


def cf_inv(a):
    rx, ry, rz = (a[3],a[6],a[9]), (a[4],a[7],a[10]), (a[5],a[8],a[11])
    px = -(rx[0]*a[0] + rx[1]*a[1] + rx[2]*a[2])
    py = -(ry[0]*a[0] + ry[1]*a[1] + ry[2]*a[2])
    pz = -(rz[0]*a[0] + rz[1]*a[1] + rz[2]*a[2])
    return [px, py, pz,
            rx[0], rx[1], rx[2],
            ry[0], ry[1], ry[2],
            rz[0], rz[1], rz[2]]


def to_obj(a, b):
    """a:ToObjectSpace(b)  ==  cf_inv(a) * b"""
    return cf_mul(cf_inv(a), b)


# Core algorithm (ported from Drone's plugin)

R15_TRAVERSAL_ORDER = [
    'LowerTorso',
    'UpperTorso',
    'Head',
    'LeftUpperArm',  'LeftLowerArm',  'LeftHand',
    'RightUpperArm', 'RightLowerArm', 'RightHand',
    'LeftUpperLeg',  'LeftLowerLeg',  'LeftFoot',
    'RightUpperLeg', 'RightLowerLeg', 'RightFoot',
]

R6_TRAVERSAL_ORDER = ['Torso', 'Head', 'Left Arm', 'Right Arm', 'Left Leg', 'Right Leg']


def compute_world_cfs(poses, ref_parts, ref_joints, traversal_order):
    """Apply animation poses to a T-pose rig and return world CFrames."""
    world_cfs = {'HumanoidRootPart': list(ref_parts['HumanoidRootPart'])}
    for part_name in traversal_order:
        joint = ref_joints.get(part_name)
        if joint is None: continue
        parent_cf = world_cfs.get(joint['part0_name'])
        if parent_cf is None: continue
        pose_cf = poses.get(part_name, list(IDENTITY))
        world_cfs[part_name] = cf_mul(
            cf_mul(cf_mul(parent_cf, joint['c0']), pose_cf),
            cf_inv(joint['c1'])
        )
    return world_cfs


def _calculate_limb(limb_world_cf, motor_c0, motor_c1, torso_world_cf,
                    src_hrp_cf, dst_hrp_cf, offset=(0., 0., 0.)):
    mapped = cf_mul(dst_hrp_cf, cf_mul(cf_inv(src_hrp_cf), limb_world_cf))
    mapped = [mapped[0]+offset[0], mapped[1]+offset[1], mapped[2]+offset[2]] + list(mapped[3:])
    cf = cf_mul(cf_inv(cf_mul(torso_world_cf, motor_c0)), mapped)
    return cf_mul(cf, motor_c1), mapped


# Pose XML helpers

_ref_n = [0]


def _new_ref():
    _ref_n[0] += 1
    return f'RPOSE{_ref_n[0]:08d}'


def _get_name(elem):
    for s in elem.iter('string'):
        if s.get('name') == 'Name':
            return s.text or ''
    return ''


def _get_cf(pose):
    props = pose.find('Properties')
    if props is not None:
        cf_elem = props.find("CoordinateFrame[@name='CFrame']")
        if cf_elem is not None:
            return parse_cf(cf_elem)
    return list(IDENTITY)


def _get_easing(pose):
    props = pose.find('Properties')
    ed, es, w = '0', '0', '1'
    if props is not None:
        for t in props.findall('token'):
            n = t.get('name')
            if n == 'EasingDirection': ed = t.text or '0'
            elif n == 'EasingStyle':   es = t.text or '0'
        for f in props.findall('float'):
            if f.get('name') == 'Weight': w = f.text or '1'
    return ed, es, w


def _make_pose(name, cf, easing=('0', '0', '1')):
    item = ET.Element('Item')
    item.set('class', 'Pose')
    item.set('referent', _new_ref())
    props = ET.SubElement(item, 'Properties')
    n  = ET.SubElement(props, 'string');          n.set('name', 'Name');             n.text = name
    c  = ET.SubElement(props, 'CoordinateFrame'); c.set('name', 'CFrame');           write_cf(c, cf)
    ed = ET.SubElement(props, 'token');           ed.set('name', 'EasingDirection'); ed.text = easing[0]
    es = ET.SubElement(props, 'token');           es.set('name', 'EasingStyle');     es.text = easing[1]
    w  = ET.SubElement(props, 'float');           w.set('name',  'Weight');          w.text  = easing[2]
    return item


def _collect_poses(keyframe):
    poses, easings = {}, {}
    def walk(item):
        if item.get('class') != 'Pose': return
        name = _get_name(item)
        poses[name]  = _get_cf(item)
        easings[name] = _get_easing(item)
        for child in item: walk(child)
    for child in list(keyframe): walk(child)
    return poses, easings


def _clear_poses(keyframe):
    for child in list(keyframe):
        if child.get('class') == 'Pose': keyframe.remove(child)


# R15 -> R6

R6_FROM_R15 = {
    'Torso':     ('UpperTorso',    ['LowerTorso'],    (0., -0.2,   0.)),
    'Right Arm': ('RightLowerArm', ['RightUpperArm'], (0.,  0.224, 0.)),
    'Left Arm':  ('LeftLowerArm',  ['LeftUpperArm'],  (0.,  0.224, 0.)),
    'Right Leg': ('RightLowerLeg', ['RightUpperLeg'], (0.,  0.201, 0.)),
    'Left Leg':  ('LeftLowerLeg',  ['LeftUpperLeg'],  (0.,  0.201, 0.)),
    'Head':      ('Head',          [],                (0.,  0.,    0.)),
}


def convert_keyframe_r15_to_r6(keyframe, r6_parts, r6_joints, r15_parts, r15_joints):
    poses, easings = _collect_poses(keyframe)
    r15_world  = compute_world_cfs(poses, r15_parts, r15_joints, R15_TRAVERSAL_ORDER)
    r6_hrp_cf  = r6_parts['HumanoidRootPart']
    r15_hrp_cf = r15_parts['HumanoidRootPart']

    converted = {}
    converted['HumanoidRootPart'] = (list(IDENTITY), easings.get('HumanoidRootPart', ('0','0','1')))

    torso_world_cf = r6_parts['Torso']
    preferred, _, offset = R6_FROM_R15['Torso']
    if preferred in r15_world:
        motor = r6_joints['Torso']
        cf, torso_world_cf = _calculate_limb(
            r15_world[preferred], motor['c0'], motor['c1'],
            torso_world_cf, r15_hrp_cf, r6_hrp_cf, offset,
        )
        converted['Torso'] = (cf, easings.get('UpperTorso', easings.get('LowerTorso', ('0','0','1'))))

    for r6_name, (preferred, fallbacks, offset) in R6_FROM_R15.items():
        if r6_name == 'Torso': continue
        r15_name = preferred if preferred in r15_world else next(
            (fb for fb in fallbacks if fb in r15_world), None
        )
        if r15_name is None: continue
        motor = r6_joints.get(r6_name)
        if motor is None: continue
        cf, _ = _calculate_limb(
            r15_world[r15_name], motor['c0'], motor['c1'],
            torso_world_cf, r15_hrp_cf, r6_hrp_cf, offset,
        )
        converted[r6_name] = (cf, easings.get(r15_name, ('0','0','1')))

    _clear_poses(keyframe)

    def p(name):
        cf, easing = converted.get(name, (list(IDENTITY), ('0','0','1')))
        return _make_pose(name, cf, easing)

    hrp   = p('HumanoidRootPart')
    torso = p('Torso')
    hrp.append(torso)
    for limb in ('Head', 'Left Arm', 'Right Arm', 'Left Leg', 'Right Leg'):
        torso.append(p(limb))
    keyframe.append(hrp)


# R6 -> R15

R15_FROM_R6 = {
    'Torso':     'LowerTorso',
    'Head':      'Head',
    'Left Arm':  'LeftUpperArm',
    'Right Arm': 'RightUpperArm',
    'Left Leg':  'LeftUpperLeg',
    'Right Leg': 'RightUpperLeg',
}


def _get_new_transform(transform, from_joint, to_joint, r6_parts, r15_parts):
    hrp_r6  = r6_parts['HumanoidRootPart']
    hrp_r15 = r15_parts['HumanoidRootPart']

    from_limb = r6_parts[from_joint['part1_name']]
    to_limb   = r15_parts[to_joint['part1_name']]
    from_root = r6_parts[from_joint['part0_name']]
    to_root   = r15_parts[to_joint['part0_name']]

    from_offset = to_obj(hrp_r6,  from_limb)
    to_offset   = to_obj(hrp_r15, to_limb)

    final_cf = cf_mul(
        cf_mul(from_joint['c0'], cf_inv(cf_mul(from_joint['c1'], transform))),
        to_obj(from_offset, to_offset),
    )

    from_root_obj  = to_obj(hrp_r6,  from_root)
    to_root_obj    = to_obj(hrp_r15, to_root)
    to_root_offset = to_obj(from_root_obj, to_root_obj)

    return cf_mul(cf_inv(to_joint['c1']),
                  cf_mul(cf_inv(final_cf),
                         cf_mul(to_root_offset, to_joint['c0'])))


def convert_keyframe_r6_to_r15(keyframe, r6_parts, r6_joints, r15_parts, r15_joints):
    poses, easings = _collect_poses(keyframe)

    converted = {}
    converted['HumanoidRootPart'] = (list(IDENTITY), easings.get('HumanoidRootPart', ('0','0','1')))

    for r6_name, r15_name in R15_FROM_R6.items():
        if r6_name not in poses: continue
        from_j = r6_joints.get(r6_name)
        to_j   = r15_joints.get(r15_name)
        if from_j is None or to_j is None: continue
        cf = _get_new_transform(poses[r6_name], from_j, to_j, r6_parts, r15_parts)
        converted[r15_name] = (cf, easings.get(r6_name, ('0','0','1')))

    for part in ('LeftLowerArm', 'LeftHand', 'RightLowerArm', 'RightHand',
                 'LeftLowerLeg', 'LeftFoot', 'RightLowerLeg', 'RightFoot'):
        converted.setdefault(part, (list(IDENTITY), ('0','0','1')))

    _clear_poses(keyframe)

    def p(name):
        cf, easing = converted.get(name, (list(IDENTITY), ('0','0','1')))
        return _make_pose(name, cf, easing)

    hrp = p('HumanoidRootPart')
    lt  = p('LowerTorso')
    hrp.append(lt)

    ut = p('UpperTorso')
    lt.append(ut)
    ut.append(p('Head'))

    for side in ('Left', 'Right'):
        ua   = p(f'{side}UpperArm')
        la   = p(f'{side}LowerArm')
        hand = p(f'{side}Hand')
        la.append(hand)
        ua.append(la)
        ut.append(ua)

        ul   = p(f'{side}UpperLeg')
        ll   = p(f'{side}LowerLeg')
        foot = p(f'{side}Foot')
        ll.append(foot)
        ul.append(ll)
        lt.append(ul)

    keyframe.append(hrp)


# XML sanitize helper

def sanitize_xml(data: bytes) -> str:
    """Decode bytes and strip XML-illegal control characters."""
    text = data.decode('utf-8', errors='replace')
    return re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]', '', text)


# KeyframeSequence -> CurveAnimation

_CURVE_TICKS_PER_SEC = 14400  # ticks per second used by CurveAnimation


def _encode_float_curve(times_sec: list, values: list) -> bytes:
    """Encode (times, values) into the FloatCurve ValuesAndTimes binary format.

    Format (version 2):
      uint32 version=2, uint32 N
      N × [uint8 interp=1, uint8 flags=0, f32 value, f32 slope_l=0, f32 slope_r=0]
      uint32 section_type=1, uint32 N
      N × uint32 time_ticks  (at 14400 ticks/sec)
    """
    n = len(times_sec)
    buf = struct.pack('<II', 2, n)
    for v in values:
        buf += struct.pack('<BBfff', 1, 0, float(v), 0.0, 0.0)
    buf += struct.pack('<II', 1, n)
    for t in times_sec:
        buf += struct.pack('<I', max(0, int(round(t * _CURVE_TICKS_PER_SEC))))
    return buf


def _decompose_cf(cf: list) -> tuple:
    """Return (px, py, pz, euler_x, euler_y, euler_z) from a CFrame.

    Euler angles are in XYZ order (Roblox RotationOrder=0):
    R = Rx(rx) * Ry(ry) * Rz(rz)
    """
    px, py, pz = cf[0], cf[1], cf[2]
    R02 = cf[5]
    ry = math.asin(max(-1.0, min(1.0, R02)))
    if abs(math.cos(ry)) > 1e-6:
        rx = math.atan2(-cf[8], cf[11])
        rz = math.atan2(-cf[4], cf[3])
    else:
        rx = math.atan2(cf[7], cf[6])
        rz = 0.0
    return px, py, pz, rx, ry, rz


def _xml_escape(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def keyframe_to_curve_anim(xml_bytes: bytes) -> bytes:
    """Convert a KeyframeSequence RBXMX to a CurveAnimation RBXMX.

    Each Pose CFrame is split into a Position (Vector3Curve/FloatCurve X/Y/Z)
    and a Rotation (EulerRotationCurve/FloatCurve X/Y/Z, RotationOrder=0 XYZ).
    The Folder hierarchy mirrors the Pose parent-child structure.
    ValuesAndTimes is written as base64-encoded BinaryString.
    """
    root_elem = ET.fromstring(sanitize_xml(xml_bytes))

    ks = root_elem.find(".//Item[@class='KeyframeSequence']")
    if ks is None:
        return xml_bytes

    # Read top-level properties
    ksp = ks.find('Properties') or ET.Element('Properties')

    def _ptext(tag, name, default):
        e = ksp.find(f"{tag}[@name='{name}']")
        return (e.text or default).strip() if e is not None else default

    loop_val = _ptext('bool',   'Loop',     'false')
    prio_val = _ptext('token',  'Priority', '3')
    name_val = _xml_escape(_ptext('string', 'Name', 'Animation'))

    # Collect keyframe elements sorted by time
    def _kf_time(kf):
        e = kf.find("Properties/float[@name='Time']")
        return float(e.text or 0) if e is not None else 0.0

    kf_elems = sorted(
        [kf for kf in ks if kf.get('class') == 'Keyframe'],
        key=_kf_time,
    )
    if not kf_elems:
        return xml_bytes

    time_scale = 1.0 / 6.0

    # Build bone hierarchy from first keyframe
    parents_of: dict = {}   # bone -> parent_name or None
    children_of: dict = {}  # bone -> [children]
    roots_list: list = []

    def _parse_hierarchy(elem, parent):
        if elem.get('class') != 'Pose':
            return
        name = _get_name(elem)
        if not name:
            return
        parents_of[name] = parent
        children_of.setdefault(name, [])
        if parent:
            children_of.setdefault(parent, [])
            children_of[parent].append(name)
        else:
            roots_list.append(name)
        for child in elem:
            _parse_hierarchy(child, name)

    for child in kf_elems[0]:
        _parse_hierarchy(child, None)

    # Collect per-bone CFrames: bone_cfs[name] = [(time, cf), ...]
    bone_cfs: dict = {b: [] for b in parents_of}
    all_times = [_kf_time(kf) for kf in kf_elems]

    for kf in kf_elems:
        t = _kf_time(kf)

        def _walk_poses(elem):
            if elem.get('class') != 'Pose':
                return
            n = _get_name(elem)
            if n and n in bone_cfs:
                bone_cfs[n].append((t, _get_cf(elem)))
            for c in elem:
                _walk_poses(c)

        for child in kf:
            _walk_poses(child)

    # Unique referent counter (local to this call)
    _id = [0]

    def _ref():
        _id[0] += 1
        return f'RBX{_id[0]:032X}'

    def _fc_xml(axis, times, values):
        data = _encode_float_curve(times, values)
        b64 = base64.b64encode(data).decode()
        safe = _xml_escape(axis)
        return (
            f'<Item class="FloatCurve" referent="{_ref()}">'
            f'<Properties>'
            f'<string name="AttributesSerialize"/>'
            f'<bool name="DefinesCapabilities">false</bool>'
            f'<string name="Name">{safe}</string>'
            f'<int64 name="SourceAssetId">-1</int64>'
            f'<string name="Tags"/>'
            f'<BinaryString name="ValuesAndTimes">{b64}</BinaryString>'
            f'</Properties></Item>'
        )

    def _bone_xml(bone):
        data = bone_cfs.get(bone) or [(t, list(IDENTITY)) for t in all_times]
        times  = [d[0] * time_scale for d in data]
        decomp = [_decompose_cf(d[1]) for d in data]
        px = [d[0] for d in decomp]; py = [d[1] for d in decomp]; pz = [d[2] for d in decomp]
        rx = [d[3] for d in decomp]; ry = [d[4] for d in decomp]; rz = [d[5] for d in decomp]

        pos_item = (
            f'<Item class="Vector3Curve" referent="{_ref()}">'
            f'<Properties>'
            f'<string name="AttributesSerialize"/>'
            f'<bool name="DefinesCapabilities">false</bool>'
            f'<string name="Name">Position</string>'
            f'<int64 name="SourceAssetId">-1</int64>'
            f'<string name="Tags"/>'
            f'</Properties>'
            + _fc_xml('X', times, px)
            + _fc_xml('Y', times, py)
            + _fc_xml('Z', times, pz)
            + '</Item>'
        )
        rot_item = (
            f'<Item class="EulerRotationCurve" referent="{_ref()}">'
            f'<Properties>'
            f'<string name="AttributesSerialize"/>'
            f'<bool name="DefinesCapabilities">false</bool>'
            f'<string name="Name">Rotation</string>'
            f'<token name="RotationOrder">0</token>'
            f'<int64 name="SourceAssetId">-1</int64>'
            f'<string name="Tags"/>'
            f'</Properties>'
            + _fc_xml('X', times, rx)
            + _fc_xml('Y', times, ry)
            + _fc_xml('Z', times, rz)
            + '</Item>'
        )
        children = ''.join(_bone_xml(c) for c in children_of.get(bone, []))
        safe_name = _xml_escape(bone)
        return (
            f'<Item class="Folder" referent="{_ref()}">'
            f'<Properties>'
            f'<string name="AttributesSerialize"/>'
            f'<bool name="DefinesCapabilities">false</bool>'
            f'<string name="Name">{safe_name}</string>'
            f'<int64 name="SourceAssetId">-1</int64>'
            f'<string name="Tags"/>'
            f'</Properties>'
            f'{pos_item}{rot_item}{children}'
            f'</Item>'
        )

    bones_xml = ''.join(_bone_xml(r) for r in roots_list)

    out = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">'
        '<External>null</External><External>nil</External>'
        f'<Item class="CurveAnimation" referent="{_ref()}">'
        f'<Properties>'
        f'<string name="AttributesSerialize"/>'
        f'<bool name="DefinesCapabilities">false</bool>'
        f'<bool name="Loop">{loop_val}</bool>'
        f'<string name="Name">{name_val}</string>'
        f'<token name="Priority">{prio_val}</token>'
        f'<int64 name="SourceAssetId">-1</int64>'
        f'<string name="Tags"/>'
        f'</Properties>'
        f'{bones_xml}'
        f'</Item>'
        '</roblox>'
    )
    return out.encode('utf-8')


def curve_anim_to_keyframe_xml(anim_data: bytes) -> bytes:
    """Convert a CurveAnimation (binary RBXM or XML) to a KeyframeSequence RBXMX."""
    from ..utils.anim_converter import curve_anim_to_keyframe
    return curve_anim_to_keyframe(anim_data)

