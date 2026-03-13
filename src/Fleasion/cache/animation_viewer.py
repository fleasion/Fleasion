"""Animation viewer widget using OpenGL for Python 3.14 compatibility.

This implementation properly handles motor joint hierarchies and quaternion
interpolation, matching the Reference pyvista/vtk implementation.
"""

import math
import time
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
from OpenGL.GL import *
from OpenGL.GLU import *
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QMessageBox, QMenu, QWidgetAction
)
from PyQt6.QtGui import QAction, QSurfaceFormat, QGuiApplication


from ..utils import log_buffer

# Math helpers

def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation."""
    return a + (b - a) * t


def quat_from_rot3(r: List[List[float]]) -> Tuple[float, float, float, float]:
    """Convert 3x3 rotation matrix to quaternion (w, x, y, z)."""
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (r[2][1] - r[1][2]) / s
        y = (r[0][2] - r[2][0]) / s
        z = (r[1][0] - r[0][1]) / s
    elif (r[0][0] > r[1][1]) and (r[0][0] > r[2][2]):
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        w = (r[2][1] - r[1][2]) / s
        x = 0.25 * s
        y = (r[0][1] + r[1][0]) / s
        z = (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        w = (r[0][2] - r[2][0]) / s
        x = (r[0][1] + r[1][0]) / s
        y = 0.25 * s
        z = (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
        w = (r[1][0] - r[0][1]) / s
        x = (r[0][2] + r[2][0]) / s
        y = (r[1][2] + r[2][1]) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    return (w / n, x / n, y / n, z / n)


def rot3_from_quat(q: Tuple[float, float, float, float]) -> List[List[float]]:
    """Convert quaternion to 3x3 rotation matrix."""
    w, x, y, z = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]


def quat_slerp(
    q0: Tuple[float, float, float, float],
    q1: Tuple[float, float, float, float],
    t: float
) -> Tuple[float, float, float, float]:
    """Spherical linear interpolation between quaternions."""
    w0, x0, y0, z0 = q0
    w1, x1, y1, z1 = q1
    dot = w0 * w1 + x0 * x1 + y0 * y1 + z0 * z1
    if dot < 0.0:
        dot = -dot
        w1, x1, y1, z1 = -w1, -x1, -y1, -z1
    if dot > 0.9995:
        w = w0 + (w1 - w0) * t
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        z = z0 + (z1 - z0) * t
        n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
        return (w / n, x / n, y / n, z / n)
    theta_0 = math.acos(max(-1.0, min(1.0, dot)))
    sin_0 = math.sin(theta_0) or 1e-8
    theta = theta_0 * t
    s0 = math.sin(theta_0 - theta) / sin_0
    s1 = math.sin(theta) / sin_0
    return (w0 * s0 + w1 * s1, x0 * s0 + x1 * s1, y0 * s0 + y1 * s1, z0 * s0 + z1 * s1)


# Matrix operations using numpy

def mat_identity() -> np.ndarray:
    """Create identity 4x4 matrix."""
    return np.eye(4, dtype=np.float32)


def mat_from_cframe(pos: Tuple[float, float, float], r: List[float]) -> np.ndarray:
    """Create 4x4 matrix from CFrame position and rotation values."""
    m = np.eye(4, dtype=np.float32)
    m[0, 0:3] = r[0:3]
    m[1, 0:3] = r[3:6]
    m[2, 0:3] = r[6:9]
    m[0, 3] = pos[0]
    m[1, 3] = pos[1]
    m[2, 3] = pos[2]
    return m


def mat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Multiply two 4x4 matrices."""
    return np.matmul(a, b)


def mat_inv(a: np.ndarray) -> np.ndarray:
    """Invert 4x4 matrix."""
    return np.linalg.inv(a)


def mat_get_translation(m: np.ndarray) -> Tuple[float, float, float]:
    """Get translation from 4x4 matrix."""
    return (float(m[0, 3]), float(m[1, 3]), float(m[2, 3]))


def mat_set_translation(m: np.ndarray, t: Tuple[float, float, float]) -> None:
    """Set translation in 4x4 matrix."""
    m[0, 3] = t[0]
    m[1, 3] = t[1]
    m[2, 3] = t[2]


def mat_get_rot3(m: np.ndarray) -> List[List[float]]:
    """Get 3x3 rotation from 4x4 matrix."""
    return [
        [float(m[0, 0]), float(m[0, 1]), float(m[0, 2])],
        [float(m[1, 0]), float(m[1, 1]), float(m[1, 2])],
        [float(m[2, 0]), float(m[2, 1]), float(m[2, 2])],
    ]


def mat_set_rot3(m: np.ndarray, r: List[List[float]]) -> None:
    """Set 3x3 rotation in 4x4 matrix."""
    m[0, 0:3] = r[0]
    m[1, 0:3] = r[1]
    m[2, 0:3] = r[2]


def matrix_trs_lerp(m0: np.ndarray, m1: np.ndarray, t: float) -> np.ndarray:
    """Interpolate between two matrices using TRS decomposition and slerp."""
    t0 = mat_get_translation(m0)
    t1 = mat_get_translation(m1)
    tt = (lerp(t0[0], t1[0], t), lerp(t0[1], t1[1], t), lerp(t0[2], t1[2], t))

    q0 = quat_from_rot3(mat_get_rot3(m0))
    q1 = quat_from_rot3(mat_get_rot3(m1))
    qt = quat_slerp(q0, q1, t)
    rt = rot3_from_quat(qt)

    out = mat_identity()
    mat_set_rot3(out, rt)
    mat_set_translation(out, tt)
    return out


# Data structures

@dataclass
class Part:
    """Rig part."""
    referent: str
    name: str
    size: Tuple[float, float, float]
    cframe: np.ndarray
    mesh_data: Optional[Dict] = None


@dataclass
class Motor6D:
    """Motor joint connecting two parts."""
    name: str
    part0_ref: str
    part1_ref: str
    c0: np.ndarray
    c1: np.ndarray
    c1_inv: np.ndarray = None  # Cached inverse of c1

    def __post_init__(self):
        """Cache the inverse of c1 for performance."""
        if self.c1_inv is None:
            self.c1_inv = mat_inv(self.c1)


@dataclass
class Keyframe:
    """Animation keyframe."""
    time: float
    pose_by_part_name: Dict[str, np.ndarray]


# XML parsing helpers

def _text(elem: Optional[ET.Element], default: str = '') -> str:
    """Get text from XML element."""
    return elem.text if elem is not None and elem.text is not None else default


def find_prop(props: ET.Element, tag: str, names: List[str]) -> Optional[ET.Element]:
    """Find property element by tag and name."""
    for n in names:
        e = props.find(f"{tag}[@name='{n}']")
        if e is not None:
            return e
    for child in props:
        if child.tag != tag:
            continue
        nm = child.attrib.get('name', '')
        for n in names:
            if nm.lower() == n.lower():
                return child
    return None


def parse_vector3(elem: ET.Element) -> Tuple[float, float, float]:
    """Parse Vector3 from XML."""
    return (
        float(_text(elem.find('X'), '0')),
        float(_text(elem.find('Y'), '0')),
        float(_text(elem.find('Z'), '0')),
    )


def parse_cframe(elem: ET.Element) -> Tuple[Tuple[float, float, float], List[float]]:
    """Parse CFrame from XML."""
    x = float(_text(elem.find('X'), '0'))
    y = float(_text(elem.find('Y'), '0'))
    z = float(_text(elem.find('Z'), '0'))
    r = []
    for k in ('R00', 'R01', 'R02', 'R10', 'R11', 'R12', 'R20', 'R21', 'R22'):
        if k in ('R00', 'R11', 'R22'):
            r.append(float(_text(elem.find(k), '1')))
        else:
            r.append(float(_text(elem.find(k), '0')))
    return (x, y, z), r


# Rig and animation loading

def load_rig(rig_path: str) -> Tuple[Dict[str, Part], List[Motor6D]]:
    """Load rig from XML file."""
    tree = ET.parse(rig_path)
    root = tree.getroot()

    parts: Dict[str, Part] = {}
    motors: List[Motor6D] = []

    for item in root.iter('Item'):
        cls = item.attrib.get('class', '')
        ref = item.attrib.get('referent', '')
        props = item.find('Properties')
        if props is None:
            continue

        size_elem = find_prop(props, 'Vector3', ['size', 'Size', 'InitialSize'])
        cf_elem = find_prop(props, 'CoordinateFrame', ['CFrame']) or find_prop(props, 'CFrame', ['CFrame'])

        if size_elem is not None and cf_elem is not None:
            name = _text(find_prop(props, 'string', ['Name']), cls)
            size = parse_vector3(size_elem)
            pos, r = parse_cframe(cf_elem)
            parts[ref] = Part(ref, name, size, mat_from_cframe(pos, r))

        if cls == 'Motor6D':
            name = _text(find_prop(props, 'string', ['Name']))
            p0 = find_prop(props, 'Ref', ['Part0'])
            p1 = find_prop(props, 'Ref', ['Part1'])
            c0e = find_prop(props, 'CoordinateFrame', ['C0']) or find_prop(props, 'CFrame', ['C0'])
            c1e = find_prop(props, 'CoordinateFrame', ['C1']) or find_prop(props, 'CFrame', ['C1'])
            if p0 is None or p1 is None or c0e is None or c1e is None:
                continue

            pos0, r0 = parse_cframe(c0e)
            pos1, r1 = parse_cframe(c1e)

            motors.append(Motor6D(
                name=name,
                part0_ref=_text(p0),
                part1_ref=_text(p1),
                c0=mat_from_cframe(pos0, r0),
                c1=mat_from_cframe(pos1, r1),
            ))

    return parts, motors


def load_animation_from_xml(anim_data: bytes) -> List[Keyframe]:
    """Load animation from XML bytes (RBXMX format)."""
    try:
        root = ET.fromstring(anim_data)
    except ET.ParseError:
        return []

    keys: List[Keyframe] = []
    for item in root.iter('Item'):
        if item.attrib.get('class') != 'Keyframe':
            continue
        props = item.find('Properties')
        if props is None:
            continue

        t_elem = find_prop(props, 'float', ['Time'])
        if t_elem is None:
            continue
        t = float(_text(t_elem, '0'))

        poses: Dict[str, np.ndarray] = {}
        for pose_item in item.iter('Item'):
            if pose_item.attrib.get('class') != 'Pose':
                continue
            pprops = pose_item.find('Properties')
            if pprops is None:
                continue

            pname = _text(find_prop(pprops, 'string', ['Name']))
            cf = find_prop(pprops, 'CoordinateFrame', ['CFrame']) or find_prop(pprops, 'CFrame', ['CFrame'])
            if not pname or cf is None:
                continue

            pos, r = parse_cframe(cf)
            poses[pname] = mat_from_cframe(pos, r)

        keys.append(Keyframe(t, poses))

    keys.sort(key=lambda k: k.time)
    return keys


def load_animation_from_rbxm(anim_data: bytes) -> List[Keyframe]:
    """Load animation from binary RBXM format."""
    try:
        from .rbxm_parser import parse_rbxm, find_by_class
    except ImportError:
        log_buffer.log('AnimationViewer', 'RBXM parser not available')
        return []

    try:
        instances = parse_rbxm(anim_data)

        # Find all Keyframe instances
        keyframe_instances = find_by_class(instances, 'Keyframe')

        if not keyframe_instances:
            log_buffer.log('AnimationViewer', 'No Keyframe instances found in RBXM')
            return []

        keys: List[Keyframe] = []

        for kf_inst in keyframe_instances:
            # Get keyframe time
            time_val = kf_inst.properties.get('Time', 0.0)
            if isinstance(time_val, (int, float)):
                t = float(time_val)
            else:
                t = 0.0

            # Find all Pose children (recursively)
            poses: Dict[str, np.ndarray] = {}
            _collect_poses(kf_inst, poses)

            if poses:
                keys.append(Keyframe(t, poses))

        keys.sort(key=lambda k: k.time)
        return keys

    except Exception as e:
        log_buffer.log('AnimationViewer', f'Error parsing RBXM animation: {e}')
        import traceback
        log_buffer.log('AnimationViewer', traceback.format_exc())
        return []


def _collect_poses(instance, poses: Dict[str, np.ndarray]):
    """Recursively collect Pose instances from a Keyframe."""
    for child in instance.children:
        if child.class_name == 'Pose':
            name = child.properties.get('Name', '')
            cframe = child.properties.get('CFrame')

            if name and cframe:
                # CFrame is a dict with 'position' and 'rotation'
                pos = cframe.get('position', (0, 0, 0))
                rot = cframe.get('rotation', [1, 0, 0, 0, 1, 0, 0, 0, 1])
                poses[name] = mat_from_cframe(pos, rot)

            # Recursively check for nested poses
            _collect_poses(child, poses)


def load_animation_data(anim_data: bytes) -> List[Keyframe]:
    """Load animation from either XML or binary RBXM format."""
    # Try to detect format
    if anim_data.startswith(b'<roblox!'):
        # Binary RBXM format
        return load_animation_from_rbxm(anim_data)
    elif anim_data.strip().startswith(b'<'):
        # XML format
        return load_animation_from_xml(anim_data)
    else:
        # Try binary first, then XML
        keys = load_animation_from_rbxm(anim_data)
        if keys:
            return keys
        return load_animation_from_xml(anim_data)


def load_animation_from_file(anim_path: str) -> List[Keyframe]:
    """Load animation from XML file."""
    tree = ET.parse(anim_path)
    root = tree.getroot()

    keys: List[Keyframe] = []
    for item in root.iter('Item'):
        if item.attrib.get('class') != 'Keyframe':
            continue
        props = item.find('Properties')
        if props is None:
            continue

        t_elem = find_prop(props, 'float', ['Time'])
        if t_elem is None:
            continue
        t = float(_text(t_elem, '0'))

        poses: Dict[str, np.ndarray] = {}
        for pose_item in item.iter('Item'):
            if pose_item.attrib.get('class') != 'Pose':
                continue
            pprops = pose_item.find('Properties')
            if pprops is None:
                continue

            pname = _text(find_prop(pprops, 'string', ['Name']))
            cf = find_prop(pprops, 'CoordinateFrame', ['CFrame']) or find_prop(pprops, 'CFrame', ['CFrame'])
            if not pname or cf is None:
                continue

            pos, r = parse_cframe(cf)
            poses[pname] = mat_from_cframe(pos, r)

        keys.append(Keyframe(t, poses))

    keys.sort(key=lambda k: k.time)
    return keys


def sample_keyframes(keys: List[Keyframe], t: float) -> Tuple[Keyframe, Keyframe, float]:
    """Sample animation at time t, returning interpolation data."""
    if not keys:
        return Keyframe(0, {}), Keyframe(0, {}), 0.0
    if t <= keys[0].time:
        return keys[0], keys[0], 0.0
    if t >= keys[-1].time:
        return keys[-1], keys[-1], 0.0
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a.time <= t <= b.time:
            span = (b.time - a.time) or 1e-6
            return a, b, (t - a.time) / span
    return keys[-1], keys[-1], 0.0


def pick_root_ref(parts: Dict[str, Part]) -> str:
    """Pick the root part reference."""
    preferred = ('HumanoidRootPart', 'LowerTorso', 'Torso', 'UpperTorso', 'Head')
    for want in preferred:
        for ref, p in parts.items():
            if p.name == want:
                return ref
    return next(iter(parts.keys()))


def detect_rig_type(parts: Dict[str, Part]) -> str:
    """Detect if rig is R6 or R15."""
    names = {p.name for p in parts.values()}
    if 'Torso' in names and 'UpperTorso' not in names:
        return 'R6'
    return 'R15'


# Mesh loading

def load_obj_mesh(mesh_path: str) -> Optional[Dict]:
    """Load OBJ mesh file."""
    if not os.path.exists(mesh_path):
        return None

    vertices = []
    normals = []
    faces = []

    try:
        with open(mesh_path, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue

                if parts[0] == 'v':
                    vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
                elif parts[0] == 'vn':
                    normals.append([float(parts[1]), float(parts[2]), float(parts[3])])
                elif parts[0] == 'f':
                    face_verts = []
                    face_norms = []
                    for vertex_str in parts[1:]:
                        indices = vertex_str.split('/')
                        v_idx = int(indices[0]) - 1
                        face_verts.append(v_idx)
                        if len(indices) >= 3 and indices[2]:
                            n_idx = int(indices[2]) - 1
                            face_norms.append(n_idx)
                    faces.append({'v': face_verts, 'n': face_norms if face_norms else None})

        return {
            'vertices': np.array(vertices, dtype=np.float32),
            'normals': np.array(normals, dtype=np.float32) if normals else None,
            'faces': faces
        }
    except Exception as e:
        log_buffer.log('AnimationViewer', f'Error loading mesh {mesh_path}: {e}')
        return None


def create_cube_mesh(sx: float, sy: float, sz: float) -> Dict:
    """Create a simple cube mesh."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    vertices = np.array([
        [-hx, -hy, -hz], [hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz],
        [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]
    ], dtype=np.float32)

    # Face normals
    normals = np.array([
        [0, 0, -1], [0, 0, 1], [0, -1, 0], [0, 1, 0], [-1, 0, 0], [1, 0, 0]
    ], dtype=np.float32)

    faces = [
        {'v': [0, 1, 2, 3], 'n': [0, 0, 0, 0]},  # Front
        {'v': [5, 4, 7, 6], 'n': [1, 1, 1, 1]},  # Back
        {'v': [0, 1, 5, 4], 'n': [2, 2, 2, 2]},  # Bottom
        {'v': [3, 2, 6, 7], 'n': [3, 3, 3, 3]},  # Top
        {'v': [0, 3, 7, 4], 'n': [4, 4, 4, 4]},  # Left
        {'v': [1, 2, 6, 5], 'n': [5, 5, 5, 5]},  # Right
    ]
    return {'vertices': vertices, 'normals': normals, 'faces': faces}


def get_animpreview_dir() -> Path:
    """Get the animpreview tools directory."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    return base / 'tools' / 'animpreview'


def get_mesh_dir() -> Path:
    """Get the mesh parts directory."""
    return get_animpreview_dir() / 'R15AndR6Parts'


def get_rig_path(rig_type: str) -> Path:
    """Get the rig file path."""
    return get_animpreview_dir() / f'{rig_type}RIG.rbxmx'


# OpenGL viewer widget

class AnimationGLWidget(QOpenGLWidget):
    """OpenGL widget for displaying animated rigs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parts: Dict[str, Part] = {}
        self.motors: List[Motor6D] = []
        self.keyframes: List[Keyframe] = []
        self.current_time = 0.0
        self.duration = 0.0

        # Root tracking
        self.root_ref: Optional[str] = None
        self.root_name: str = ''
        self.base_root_world: Optional[np.ndarray] = None

        # World transforms for each part
        self.world_transforms: Dict[str, np.ndarray] = {}

        # Camera
        self.rotation_x = 20
        self.rotation_y = 205
        self.zoom = 20
        self.camera_target = (0, 2, 0)
        self.last_pos = None

        # Rig type
        self.rig_type = 'R15'

        # Display lists for mesh caching (major performance boost)
        self.display_lists: Dict[str, int] = {}
        self.grid_display_list: int = 0
        
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Slightly larger minimum so viewers are usable on small panels
        self.setMinimumSize(120, 120)
        
        # Camera state
        self.camera_mode = 'orbit'
        self.cam_pos = np.array([0.0, 0.0, 0.0], dtype=float)
        self.cam_yaw = 0.0
        self.cam_pitch = 0.0
        self.base_speed = 0.1
        
        self.auto_rotate = False
        self.keys_pressed = set()
        self.last_tick_time = time.time()
        self.show_grid = True
        
        # Main update tick: use monitor refresh rate where possible
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_tick)
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            refresh = float(screen.refreshRate()) if screen is not None else 60.0
            if not refresh or refresh <= 0:
                refresh = 60.0
        except Exception:
            refresh = 60.0
        interval_ms = max(1, int(round(1000.0 / refresh)))
        self.timer.start(interval_ms)

    def get_refresh_interval_ms(self) -> int:
        """Return a safe refresh interval (ms) based on the current screen or primary screen."""
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            refresh = float(screen.refreshRate()) if screen is not None else 60.0
            if not refresh or refresh <= 0:
                refresh = 60.0
        except Exception:
            refresh = 60.0
        return max(1, int(round(1000.0 / refresh)))

    def _create_placeholder_rig(self, pose_names: set) -> Tuple[Dict[int, 'Part'], Dict[str, 'Motor']]:
        """Create placeholder rig with simple cubes for unsupported animation types."""
        parts = {}
        motors = {}

        # Create a cube for each unique part in the animation
        for idx, part_name in enumerate(sorted(pose_names)):
            # Create part with a small cube
            part = Part(
                referent=str(idx),
                name=part_name,
                size=(0.5, 0.5, 0.5),
                cframe=np.eye(4, dtype=np.float32)
            )
            # Create a simple cube mesh
            part.mesh_data = create_cube_mesh(0.5, 0.5, 0.5)
            parts[idx] = part

            # Position blocks in a grid
            grid_x = idx % 5
            grid_z = idx // 5
            part.cframe[0, 3] = grid_x * 1.5 - 3.0  # x offset
            part.cframe[2, 3] = grid_z * 1.5  # z offset

        return parts, motors

    def load_animation_data(self, anim_data: bytes) -> bool:
        """Load animation from raw bytes and setup rig."""
        try:
            # Parse animation (handles both XML and binary RBXM)
            self.keyframes = load_animation_data(anim_data)
            if not self.keyframes:
                log_buffer.log('AnimationViewer', 'No keyframes found in animation data')
                return False

            # Retain angles between meshes, but cleanly exit FPS mode & reset distance
            if self.camera_mode == 'fps':
                self.camera_mode = 'orbit'
                self.rotation_x = self.cam_pitch
                self.rotation_y = self.cam_yaw
            self.zoom = 20.0

            self.duration = max(kf.time for kf in self.keyframes) if self.keyframes else 0

            # Detect rig type from animation pose names
            all_pose_names: set = set()
            for kf in self.keyframes:
                all_pose_names.update(kf.pose_by_part_name.keys())

            # R6 uses Torso, R15 uses UpperTorso/LowerTorso
            if 'Torso' in all_pose_names and 'UpperTorso' not in all_pose_names:
                self.rig_type = 'R6'
            elif 'UpperTorso' in all_pose_names or 'LowerTorso' in all_pose_names:
                self.rig_type = 'R15'
            else:
                # Unsupported rig type - use placeholder blocks
                log_buffer.log('AnimationViewer', 'Unsupported animation rig type, using placeholder blocks')
                self.rig_type = 'PLACEHOLDER'
                self.parts, self.motors = self._create_placeholder_rig(all_pose_names)
                self.duration = max(kf.time for kf in self.keyframes) if self.keyframes else 0
                self.root_ref = list(self.parts.keys())[0] if self.parts else 0
                self.root_name = self.parts[self.root_ref].name if self.parts else 'Root'
                self.base_root_world = self.parts[self.root_ref].cframe.copy() if self.parts else np.eye(4)
                self.current_time = 0
                self.update()
                return True

            # Load rig
            rig_path = get_rig_path(self.rig_type)
            if not rig_path.exists():
                log_buffer.log('AnimationViewer', f'Rig file not found: {rig_path}')
                return False

            self.parts, self.motors = load_rig(str(rig_path))
            if not self.parts:
                log_buffer.log('AnimationViewer', 'No parts found in rig')
                return False

            # Load meshes
            mesh_dir = get_mesh_dir()
            for part in self.parts.values():
                # Try exact name first
                mesh_path = mesh_dir / f'{self.rig_type}{part.name}.obj'
                mesh = load_obj_mesh(str(mesh_path))
                if mesh is None:
                    # R6 parts have spaces (e.g., "Left Arm" -> "R6Left Arm.obj")
                    mesh_path = mesh_dir / f'{self.rig_type}{part.name.replace("_", " ")}.obj'
                    mesh = load_obj_mesh(str(mesh_path))
                if mesh is None:
                    # Try without any prefix manipulation
                    for file in mesh_dir.glob(f'{self.rig_type}*.obj'):
                        if part.name.lower().replace(' ', '') in file.stem.lower().replace(' ', ''):
                            mesh = load_obj_mesh(str(file))
                            if mesh:
                                break
                if mesh is None:
                    mesh = create_cube_mesh(*part.size)
                part.mesh_data = mesh

            # Setup root
            self.root_ref = pick_root_ref(self.parts)
            self.root_name = self.parts[self.root_ref].name
            self.base_root_world = self.parts[self.root_ref].cframe.copy()

            self.current_time = 0
            self.update()
            return True

        except Exception as e:
            log_buffer.log('AnimationViewer', f'Error loading animation: {e}')
            import traceback
            log_buffer.log('AnimationViewer', traceback.format_exc())
            return False

    def initializeGL(self):
        """Initialize OpenGL settings."""
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_LIGHT1)
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_NORMALIZE)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)

        # Main light
        glLightfv(GL_LIGHT0, GL_POSITION, [1, 1, 1, 0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.3, 0.3, 0.3, 1])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.8, 0.8, 0.8, 1])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [0.2, 0.2, 0.2, 1])

        # Fill light
        glLightfv(GL_LIGHT1, GL_POSITION, [-1, 0.5, -1, 0])
        glLightfv(GL_LIGHT1, GL_DIFFUSE, [0.3, 0.3, 0.3, 1])

        glClearColor(0.15, 0.15, 0.18, 1.0)

    def resizeGL(self, w: int, h: int):
        """Handle resize."""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = w / h if h > 0 else 1
        gluPerspective(30, aspect, 0.1, 500.0)
        glMatrixMode(GL_MODELVIEW)

    def paintGL(self):
        """Render the animation frame."""
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        # Camera Transform setup
        if self.camera_mode == 'orbit':
            glTranslatef(0.0, 0.0, -self.zoom) # Invert zoom to match obj_viewer math
            glRotatef(self.rotation_x, 1.0, 0.0, 0.0)
            glRotatef(self.rotation_y, 0.0, 1.0, 0.0)
            glTranslatef(-self.camera_target[0], -self.camera_target[1], -self.camera_target[2])
        else:
            # FPS Look & Move transform
            glRotatef(self.cam_pitch, 1.0, 0.0, 0.0)
            glRotatef(self.cam_yaw, 0.0, 1.0, 0.0)
            glTranslatef(-self.cam_pos[0], -self.cam_pos[1], -self.cam_pos[2])

        # Update world transforms
        self._update_world_transforms()

        # Render parts using cached display lists
        for ref, part in self.parts.items():
            if not part.mesh_data:
                continue

            world_mat = self.world_transforms.get(ref)
            if world_mat is None:
                world_mat = part.cframe

            glPushMatrix()

            # Apply world transform (transpose for OpenGL column-major)
            gl_mat = world_mat.T.flatten().tolist()
            glMultMatrixf(gl_mat)

            # Color based on part
            if part.name.lower() == 'humanoidrootpart':
                glColor4f(1.0, 0.2, 0.2, 0.5)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            else:
                glColor3f(0.82, 0.82, 0.84)
                glDisable(GL_BLEND)

            # Use display list for fast rendering
            dl = self._get_or_compile_display_list(ref, part.mesh_data)
            glCallList(dl)

            glPopMatrix()

        if self.show_grid:
            self._draw_grid()

        # Draw XYZ axis indicator
        self._draw_axis_indicator()

    def _update_world_transforms(self):
        """Update world transforms for all parts based on current animation frame."""
        if not self.keyframes or self.root_ref is None:
            return

        # Handle placeholder rigs (no hierarchy, just animate blocks independently)
        if self.rig_type == 'PLACEHOLDER':
            kf_a, kf_b, alpha = sample_keyframes(self.keyframes, self.current_time)

            # Interpolate poses
            pose: Dict[str, np.ndarray] = {}
            all_names = set(kf_a.pose_by_part_name.keys()) | set(kf_b.pose_by_part_name.keys())
            ident = mat_identity()

            for name in all_names:
                a = kf_a.pose_by_part_name.get(name)
                b = kf_b.pose_by_part_name.get(name)
                if a is None:
                    pose[name] = b if b is not None else ident
                elif b is None:
                    pose[name] = a
                else:
                    pose[name] = matrix_trs_lerp(a, b, alpha)

            # Apply pose to each part independently (no hierarchy)
            world: Dict[str, np.ndarray] = {}
            for ref, part in self.parts.items():
                part_pose = pose.get(part.name, ident)
                world[ref] = mat_mul(part.cframe, part_pose)

            self.world_transforms = world
            return

        # Sample keyframes
        kf_a, kf_b, alpha = sample_keyframes(self.keyframes, self.current_time)

        # Interpolate poses
        pose: Dict[str, np.ndarray] = {}
        all_names = set(kf_a.pose_by_part_name.keys()) | set(kf_b.pose_by_part_name.keys())
        ident = mat_identity()

        for name in all_names:
            a = kf_a.pose_by_part_name.get(name)
            b = kf_b.pose_by_part_name.get(name)
            if a is None:
                pose[name] = b if b is not None else ident
            elif b is None:
                pose[name] = a
            else:
                pose[name] = matrix_trs_lerp(a, b, alpha)

        # Start with root
        root_pose = pose.get(self.root_name, ident)
        world: Dict[str, np.ndarray] = {}
        if self.base_root_world is not None:
            world[self.root_ref] = mat_mul(self.base_root_world, root_pose)
        else:
            world[self.root_ref] = root_pose

        # Propagate through motor hierarchy (limited passes)
        num_motors = len(self.motors)
        max_passes = min(num_motors + 2, 15)  # Limit iterations

        for _ in range(max_passes):
            changed = False
            for motor in self.motors:
                if motor.part0_ref not in world:
                    continue
                if motor.part1_ref in world:
                    continue  # Already computed
                child = self.parts.get(motor.part1_ref)
                if child is None:
                    continue

                # Get child pose transform
                T = pose.get(child.name, ident)

                # Calculate world transform: parent_world * C0 * pose * inv(C1)
                # Use cached c1_inv for performance
                part1_world = mat_mul(
                    mat_mul(mat_mul(world[motor.part0_ref], motor.c0), T),
                    motor.c1_inv
                )

                world[motor.part1_ref] = part1_world
                changed = True

            if not changed:
                break

        self.world_transforms = world

    def _compile_mesh_display_list(self, part_ref: str, mesh_data: Dict) -> int:
        """Compile mesh into a display list for fast rendering."""
        dl = glGenLists(1)
        glNewList(dl, GL_COMPILE)

        vertices = mesh_data['vertices']
        normals = mesh_data.get('normals')
        faces = mesh_data['faces']

        for face in faces:
            v_indices = face['v']
            n_indices = face.get('n')

            glBegin(GL_POLYGON)
            for i, v_idx in enumerate(v_indices):
                if 0 <= v_idx < len(vertices):
                    if normals is not None and n_indices and i < len(n_indices):
                        n_idx = n_indices[i]
                        if 0 <= n_idx < len(normals):
                            n = normals[n_idx]
                            glNormal3f(n[0], n[1], n[2])
                    v = vertices[v_idx]
                    glVertex3f(v[0], v[1], v[2])
            glEnd()

        glEndList()
        return dl

    def _get_or_compile_display_list(self, part_ref: str, mesh_data: Dict) -> int:
        """Get cached display list or compile a new one."""
        if part_ref not in self.display_lists:
            self.display_lists[part_ref] = self._compile_mesh_display_list(part_ref, mesh_data)
        return self.display_lists[part_ref]

    def _draw_grid(self):
        """Draw a subtle floor grid to provide spatial context."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        glColor4f(1.0, 1.0, 1.0, 0.08) # Very subtle white lines
        glLineWidth(1.0)
        
        grid_size = 10.0
        grid_step = 0.5
        
        bottom_y = 0.0
        
        glBegin(GL_LINES)
        val = -grid_size
        while val <= grid_size + 0.001:
            if abs(val) < 0.01:
                glColor4f(1.0, 0.2, 0.2, 0.15) # X axis highlight
            else:
                glColor4f(1.0, 1.0, 1.0, 0.08)
            glVertex3f(val, bottom_y, -grid_size)
            glVertex3f(val, bottom_y, grid_size)
            
            if abs(val) < 0.01:
                glColor4f(0.2, 0.4, 1.0, 0.15) # Z axis highlight
            else:
                glColor4f(1.0, 1.0, 1.0, 0.08)
            glVertex3f(-grid_size, bottom_y, val)
            glVertex3f(grid_size, bottom_y, val)
            
            val += grid_step
        glEnd()
        
        glPopAttrib()

    def _draw_axis_indicator(self):
        """Draw XYZ axis indicator in bottom left corner."""
        # Save current state
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glPushMatrix()

        # Setup viewport for axis indicator (bottom left corner)
        w, h = self.width(), self.height()
        indicator_size = 80  # pixels
        margin = 10

        glViewport(margin, margin, indicator_size, indicator_size)

        # Setup orthographic projection for indicator
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(-2, 2, -2, 2, -10, 10)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Apply same rotation as main model
        glRotatef(self.rotation_x, 1.0, 0.0, 0.0)
        glRotatef(self.rotation_y, 0.0, 1.0, 0.0)

        # Disable lighting for axes
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glLineWidth(2.0)

        axis_length = 1.5

        # Draw X axis (red)
        glColor3f(1.0, 0.2, 0.2)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(axis_length, 0, 0)
        glEnd()

        # Draw Y axis (green)
        glColor3f(0.2, 1.0, 0.2)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(0, axis_length, 0)
        glEnd()

        # Draw Z axis (blue)
        glColor3f(0.2, 0.4, 1.0)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(0, 0, axis_length)
        glEnd()

        # Restore projection matrix
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()

        # Restore modelview and attributes
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glPopAttrib()

        # Restore viewport
        glViewport(0, 0, w, h)

    def mousePressEvent(self, event):
        """Handle mouse press."""
        self.last_pos = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse drag."""
        if self.last_pos is None:
            return

        dx = event.pos().x() - self.last_pos.x()
        dy = event.pos().y() - self.last_pos.y()

        if event.buttons() & Qt.MouseButton.LeftButton:
            if self.camera_mode == 'orbit':
                self.rotation_x += dy * 0.5
                self.rotation_y += dx * 0.5
            else:
                self.cam_pitch += dy * 0.5
                self.cam_yaw += dx * 0.5
            self.update()

        self.last_pos = event.pos()

    def wheelEvent(self, event):
        """Handle mouse wheel."""
        delta = event.angleDelta().y()
        if self.camera_mode == 'orbit':
            self.zoom -= delta * 0.01
            self.zoom = max(2, min(200, self.zoom))
        else:
            speed = self.base_speed * 5.0 * (delta / 120.0)
            yaw = math.radians(self.cam_yaw)
            pitch = math.radians(self.cam_pitch)
            
            forward = np.array([
                math.cos(pitch) * math.sin(yaw), 
                -math.sin(pitch), 
                -math.cos(pitch) * math.cos(yaw)
            ])
            self.cam_pos += forward * speed
            
        self.update()

    def set_time(self, time: float):
        """Set animation time."""
        self.current_time = max(0, min(time, self.duration))
        self.update()


    # Physical scan codes
    _SCAN_W = 0x11
    _SCAN_A = 0x1E
    _SCAN_S = 0x1F
    _SCAN_D = 0x20
    _SCAN_Q = 0x10
    _SCAN_E = 0x12
    _SCAN_SPACE = 0x39
    _SCAN_LSHIFT = 0x2A
    _SCAN_WASD = {_SCAN_W, _SCAN_A, _SCAN_S, _SCAN_D}

    def _is_scan_pressed(self, scan_code):
        return scan_code in self.keys_pressed

    def keyPressEvent(self, event):
        scan = event.nativeScanCode()
        if self.camera_mode == 'orbit':
            if scan in self._SCAN_WASD:
                self._transition_to_fps()
            elif event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Shift}:
                return
        self.keys_pressed.add(scan)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        scan = event.nativeScanCode()
        self.keys_pressed.discard(scan)
        super().keyReleaseEvent(event)
        
    def focusOutEvent(self, event):
        self.keys_pressed.clear()
        super().focusOutEvent(event)

    def set_auto_rotate(self, enabled: bool):
        self.auto_rotate = enabled
        if enabled and self.camera_mode == 'fps':
            self.reset_view()

    def _transition_to_fps(self):
        if self.camera_mode == 'fps': return
        self.camera_mode = 'fps'
        self.auto_rotate = False
        
        pitch = self.rotation_x % 360.0
        if pitch > 180.0: pitch -= 360.0
        yaw = self.rotation_y % 360.0
        if yaw > 180.0: yaw -= 360.0

        self.cam_pitch = pitch
        self.cam_yaw = yaw

        rx = math.radians(pitch)
        ry = math.radians(yaw)

        px = -self.zoom * math.cos(rx) * math.sin(ry) + self.camera_target[0]
        py = self.zoom * math.sin(rx) + self.camera_target[1]
        pz = self.zoom * math.cos(rx) * math.cos(ry) + self.camera_target[2]

        self.cam_pos = np.array([px, py, pz], dtype=float)

    def _update_tick(self):
        needs_update = False
        current_time = time.time()
        dt = current_time - self.last_tick_time
        self.last_tick_time = current_time
        if dt > 0.1: dt = 0.016

        if self.camera_mode == 'orbit':
            if self.auto_rotate:
                self.rotation_y += 62.5 * dt
                needs_update = True
                
        elif self.camera_mode == 'fps':
            speed = self.base_speed * (dt * 62.5)
            
            if self._is_scan_pressed(self._SCAN_E): speed *= 3.0
            if self._is_scan_pressed(self._SCAN_Q): speed *= 0.33

            moved = False
            yaw = math.radians(self.cam_yaw)
            pitch = math.radians(self.cam_pitch)

            forward = np.array([math.cos(pitch) * math.sin(yaw), -math.sin(pitch), -math.cos(pitch) * math.cos(yaw)])
            right = np.array([math.cos(yaw), 0.0, math.sin(yaw)])
            up = np.array([0.0, 1.0, 0.0])

            if self._is_scan_pressed(self._SCAN_W): self.cam_pos += forward * speed; moved = True
            if self._is_scan_pressed(self._SCAN_S): self.cam_pos -= forward * speed; moved = True
            if self._is_scan_pressed(self._SCAN_A): self.cam_pos -= right * speed; moved = True
            if self._is_scan_pressed(self._SCAN_D): self.cam_pos += right * speed; moved = True
            if self._is_scan_pressed(self._SCAN_SPACE): self.cam_pos += up * speed; moved = True
            if self._is_scan_pressed(self._SCAN_LSHIFT):
                mods = QGuiApplication.keyboardModifiers()
                if not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
                    self.cam_pos -= up * speed
                    moved = True

            if moved: needs_update = True

        if needs_update: self.update()

    def reset_view(self):
        self.camera_mode = 'orbit'
        self.rotation_x = 20.0
        self.rotation_y = 205.0
        self.zoom = 20.0
        self.update()

    def toggle_grid(self, enabled: bool):
        self.show_grid = enabled
        self.update()

# Full animation viewer widget with controls

class AnimationViewerPanel(QWidget):
    """Animation viewer with playback controls."""

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.gl_widget = AnimationGLWidget()
        
        if self.config_manager:
            updated = False
            if 'obj_show_grid' not in self.config_manager.settings:
                self.config_manager.settings['obj_show_grid'] = True
                updated = True
            if updated:
                self.config_manager.save()
            self.gl_widget.show_grid = self.config_manager.settings.get('obj_show_grid', True)

        self.is_playing = False
        self.is_loaded = False
        self.timescale = 1.0

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # OpenGL viewer
        layout.addWidget(self.gl_widget, stretch=1)

        # Controls
        controls_layout = QHBoxLayout()

        self.play_pause_btn = QPushButton('Play')
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.play_pause_btn.setFixedWidth(80)
        self.play_pause_btn.setEnabled(False)
        controls_layout.addWidget(self.play_pause_btn)

        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1000)
        self.time_slider.setValue(0)
        self.time_slider.sliderPressed.connect(self._on_slider_press)
        self.time_slider.sliderReleased.connect(self._on_slider_release)
        self.time_slider.valueChanged.connect(self._on_slider_changed)
        self.time_slider.setEnabled(False)
        controls_layout.addWidget(self.time_slider)

        self.time_label = QLabel('0.00s / 0.00s')
        controls_layout.addWidget(self.time_label)
        
        controls_layout.addStretch()
        
        self.options_btn = QPushButton('Options')
        self.options_menu = QMenu(self)

        self.action_auto_rotate = self.options_menu.addAction('Auto Rotate')
        self.action_auto_rotate.setCheckable(True)
        self.action_auto_rotate.toggled.connect(self.gl_widget.set_auto_rotate)

        self.action_grid = self.options_menu.addAction('Grid')
        self.action_grid.setCheckable(True)
        self.action_grid.setChecked(self.gl_widget.show_grid)
        self.action_grid.toggled.connect(self._toggle_grid_and_save)

        # Timescale Slider in Menu
        self.options_menu.addSeparator()
        
        ts_container = QWidget()
        ts_layout = QVBoxLayout(ts_container)
        ts_layout.setContentsMargins(10, 2, 10, 2)
        ts_layout.setSpacing(0) # Tighten gap

        self.ts_label = QLabel("Timescale: 1.0x")
        self.ts_label.setStyleSheet("color: white; font-weight: normal;")
        ts_layout.addWidget(self.ts_label)

        ts_slider = QSlider(Qt.Orientation.Horizontal)
        ts_slider.setRange(1, 80) # 0.1 to 8.0
        ts_slider.setValue(10)
        ts_slider.setFixedWidth(120)
        ts_slider.valueChanged.connect(self._on_timescale_changed)
        ts_layout.addWidget(ts_slider)
        
        ts_action = QWidgetAction(self)
        ts_action.setDefaultWidget(ts_container)
        self.options_menu.addAction(ts_action)

        self.options_btn.setMenu(self.options_menu)
        controls_layout.addWidget(self.options_btn)

        self.help_btn = QPushButton('?')
        self.help_btn.setMaximumWidth(30)
        self.help_btn.setToolTip("View Camera Controls")
        self.help_btn.clicked.connect(self.show_help)
        controls_layout.addWidget(self.help_btn)

        layout.addLayout(controls_layout)

        self.setLayout(layout)

        # Playback timer with elapsed time tracking
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_playback)
        self.slider_pressed = False
        self.last_tick_time: Optional[float] = None
    def _toggle_grid_and_save(self, enabled: bool):
        self.gl_widget.toggle_grid(enabled)
        if self.config_manager:
            self.config_manager.settings['obj_show_grid'] = enabled
            self.config_manager.save()

    def show_help(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Camera Controls")
        msg.setText(
            "<h3>Orbit Mode (Default)</h3>"
            "<ul>"
            "<li><b>Click + Drag:</b> Rotate model</li>"
            "<li><b>Scroll Wheel:</b> Zoom in/out</li>"
            "</ul>"
            "<h3>FPS Mode</h3>"
            "<p><i>Pressing WASD at any time smoothly transitions you into FPS Mode.</i></p>"
            "<ul>"
            "<li><b>W/A/S/D:</b> Move forward/left/back/right</li>"
            "<li><b>Space / Shift:</b> Move Up / Down</li>"
            "<li><b>Click + Drag:</b> Look around freely</li>"
            "<li><b>Scroll Wheel:</b> Move in/out</li>"
            "</ul>"
        )
        msg.exec()

    def load_animation(self, anim_data: bytes) -> bool:
        """Load animation from raw bytes."""
        # Clear display lists before loading new animation
        self.gl_widget.display_lists.clear()
        self.gl_widget.grid_display_list = 0

        success = self.gl_widget.load_animation_data(anim_data)
        self.is_loaded = success

        if success:
            self.play_pause_btn.setEnabled(True)
            self.time_slider.setEnabled(True)
            self._update_time_label()
            if not self.is_playing:
                self._toggle_play_pause()
        else:
            self.play_pause_btn.setEnabled(False)
            self.time_slider.setEnabled(False)

        return success

    def _toggle_play_pause(self):
        """Toggle playback."""
        if self.is_playing:
            self.is_playing = False
            self.play_pause_btn.setText('Play')
            self.timer.stop()
            self.last_tick_time = None
        else:
            self.is_playing = True
            self.play_pause_btn.setText('Pause')
            self.last_tick_time = None
            # Start playback timer at monitor refresh rate for smooth sync
            try:
                interval = self.gl_widget.get_refresh_interval_ms()
            except Exception:
                interval = 33
            self.timer.start(interval)

    def _update_playback(self):
        """Update playback position using actual elapsed time."""
        import time as time_module

        if not self.slider_pressed and self.is_loaded:
            current_tick = time_module.perf_counter()

            if self.last_tick_time is not None:
                # Use actual elapsed time for accurate playback speed
                delta = current_tick - self.last_tick_time
                # Clamp delta to prevent huge jumps
                delta = min(delta, 0.1)
            else:
                delta = 0.033  # Default ~30fps

            self.last_tick_time = current_tick

            new_time = self.gl_widget.current_time + (delta * self.timescale)
            if new_time >= self.gl_widget.duration:
                new_time = 0  # Loop
                self.last_tick_time = None  # Reset on loop
            self.gl_widget.set_time(new_time)

            # Update slider
            if self.gl_widget.duration > 0:
                slider_val = int((new_time / self.gl_widget.duration) * 1000)
                self.time_slider.blockSignals(True)
                self.time_slider.setValue(slider_val)
                self.time_slider.blockSignals(False)

            self._update_time_label()

    def _on_slider_press(self):
        """Handle slider press."""
        self.slider_pressed = True

    def _on_slider_release(self):
        """Handle slider release."""
        self.slider_pressed = False

    def _on_slider_changed(self, value: int):
        """Handle slider change."""
        if self.gl_widget.duration > 0:
            new_time = (value / 1000.0) * self.gl_widget.duration
            self.gl_widget.set_time(new_time)
            self._update_time_label()

    def _update_time_label(self):
        """Update time display."""
        current = self.gl_widget.current_time
        duration = self.gl_widget.duration
        self.time_label.setText(f'{current:.2f}s / {duration:.2f}s')

    def _on_timescale_changed(self, value: int):
        self.timescale = value / 10.0
        if hasattr(self, 'ts_label'):
            self.ts_label.setText(f"Timescale: {self.timescale:.1f}x")

    def clear(self):
        """Clear animation data."""
        self.is_playing = False
        self.is_loaded = False
        self.timer.stop()
        self.last_tick_time = None
        self.play_pause_btn.setText('Play')
        self.play_pause_btn.setEnabled(False)
        self.time_slider.setEnabled(False)
        self.time_slider.setValue(0)
        self.gl_widget.parts = {}
        self.gl_widget.motors = []
        self.gl_widget.keyframes = []
        self.gl_widget.current_time = 0
        self.gl_widget.duration = 0
        self.gl_widget.world_transforms = {}
        self.gl_widget.display_lists.clear()
        self.gl_widget.grid_display_list = 0
        self.gl_widget.update()

    def stop(self):
        """Stop playback."""
        self.is_playing = False
        self.timer.stop()
        self.play_pause_btn.setText('Play')
