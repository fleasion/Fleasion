"""Animation viewer widget using OpenGL for Python 3.14 compatibility."""

import math
import os
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
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel
)


# Data structures

@dataclass
class Vector3:
    """3D vector."""
    x: float
    y: float
    z: float

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Matrix4x4:
    """4x4 transformation matrix."""
    m: np.ndarray  # 4x4 numpy array

    @staticmethod
    def identity():
        return Matrix4x4(np.eye(4))

    @staticmethod
    def from_cframe(pos: Tuple[float, float, float], rot: List[float]):
        """Create matrix from position and rotation."""
        m = np.eye(4)
        m[0, 0:3] = rot[0:3]
        m[1, 0:3] = rot[3:6]
        m[2, 0:3] = rot[6:9]
        m[0:3, 3] = pos
        return Matrix4x4(m)

    def multiply(self, other: 'Matrix4x4') -> 'Matrix4x4':
        return Matrix4x4(np.matmul(self.m, other.m))

    def inverse(self) -> 'Matrix4x4':
        return Matrix4x4(np.linalg.inv(self.m))

    def get_translation(self) -> Tuple[float, float, float]:
        return tuple(self.m[0:3, 3])

    def lerp(self, other: 'Matrix4x4', t: float) -> 'Matrix4x4':
        """Linear interpolation between two matrices."""
        return Matrix4x4((1 - t) * self.m + t * other.m)


@dataclass
class Part:
    """Rig part."""
    referent: str
    name: str
    size: Tuple[float, float, float]
    cframe: Matrix4x4
    mesh_data: Optional[Dict] = None  # Vertices, faces, normals


@dataclass
class Motor6D:
    """Motor joint connecting two parts."""
    name: str
    part0_ref: str
    part1_ref: str
    c0: Matrix4x4
    c1: Matrix4x4


@dataclass
class Keyframe:
    """Animation keyframe."""
    time: float
    pose_by_part_name: Dict[str, Matrix4x4]


# XML parsing helpers

def _text(elem: Optional[ET.Element], default='') -> str:
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
            parts[ref] = Part(ref, name, size, Matrix4x4.from_cframe(pos, r))

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
                c0=Matrix4x4.from_cframe(pos0, r0),
                c1=Matrix4x4.from_cframe(pos1, r1),
            ))

    return parts, motors


def load_animation(anim_path: str) -> List[Keyframe]:
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

        poses: Dict[str, Matrix4x4] = {}
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
            poses[pname] = Matrix4x4.from_cframe(pos, r)

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


def load_obj_mesh(mesh_path: str) -> Dict:
    """Load OBJ mesh file."""
    vertices = []
    normals = []
    faces = []

    if not os.path.exists(mesh_path):
        # Return cube fallback
        return create_cube_mesh(1, 1, 1)

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
                    # Parse face (supports v, v/vt, v/vt/vn, v//vn)
                    face_verts = []
                    for vertex_str in parts[1:]:
                        indices = vertex_str.split('/')
                        v_idx = int(indices[0]) - 1
                        face_verts.append(v_idx)
                    faces.append(face_verts)

        return {'vertices': np.array(vertices), 'faces': faces, 'normals': np.array(normals) if normals else None}

    except Exception as e:
        print(f'Error loading mesh {mesh_path}: {e}')
        return create_cube_mesh(1, 1, 1)


def create_cube_mesh(sx: float, sy: float, sz: float) -> Dict:
    """Create a simple cube mesh."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    vertices = np.array([
        [-hx, -hy, -hz], [hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz],
        [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]
    ])
    faces = [
        [0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
        [2, 3, 7, 6], [0, 3, 7, 4], [1, 2, 6, 5]
    ]
    return {'vertices': vertices, 'faces': faces, 'normals': None}


# OpenGL viewer widget

class AnimationViewerWidget(QOpenGLWidget):
    """OpenGL widget for displaying animated rigs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parts: Dict[str, Part] = {}
        self.motors: List[Motor6D] = []
        self.keyframes: List[Keyframe] = []
        self.current_time = 0.0
        self.duration = 0.0

        # Camera
        self.rotation_x = 20
        self.rotation_y = 45
        self.zoom = 10
        self.last_pos = None

    def load_animation_data(self, rig_path: str, anim_path: str, mesh_dir: Optional[str] = None):
        """Load rig and animation data."""
        try:
            self.parts, self.motors = load_rig(rig_path)
            self.keyframes = load_animation(anim_path)

            if self.keyframes:
                self.duration = self.keyframes[-1].time

            # Load meshes for parts if mesh_dir provided
            if mesh_dir:
                rig_type = self._detect_rig_type()
                for part in self.parts.values():
                    mesh_path = os.path.join(mesh_dir, f'{rig_type}{part.name}.obj')
                    part.mesh_data = load_obj_mesh(mesh_path)
            else:
                # Use cube fallback for all parts
                for part in self.parts.values():
                    part.mesh_data = create_cube_mesh(*part.size)

            self.update()

        except Exception as e:
            print(f'Error loading animation: {e}')

    def _detect_rig_type(self) -> str:
        """Detect if rig is R6 or R15."""
        names = {p.name for p in self.parts.values()}
        if 'Torso' in names and 'UpperTorso' not in names:
            return 'R6'
        return 'R15'

    def initializeGL(self):
        """Initialize OpenGL settings."""
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)

        glLightfv(GL_LIGHT0, GL_POSITION, [1, 1, 1, 0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.3, 0.3, 0.3, 1])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.7, 0.7, 0.7, 1])

        glClearColor(0.2, 0.2, 0.2, 1.0)

    def resizeGL(self, w, h):
        """Handle resize."""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, w / h if h > 0 else 1, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)

    def paintGL(self):
        """Render the animation."""
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        # Camera
        gluLookAt(0, 0, self.zoom, 0, 0, 0, 0, 1, 0)
        glRotatef(self.rotation_x, 1, 0, 0)
        glRotatef(self.rotation_y, 0, 1, 0)

        # Get current pose
        if self.keyframes:
            kf_a, kf_b, t = sample_keyframes(self.keyframes, self.current_time)
            current_poses = self._interpolate_poses(kf_a, kf_b, t)
        else:
            current_poses = {}

        # Render parts
        for part in self.parts.values():
            if not part.mesh_data:
                continue

            glPushMatrix()

            # Apply part transformation
            cframe = current_poses.get(part.name, part.cframe)
            self._apply_matrix(cframe)

            # Draw mesh
            glColor3f(0.7, 0.7, 0.9)
            self._draw_mesh(part.mesh_data)

            glPopMatrix()

    def _interpolate_poses(self, kf_a: Keyframe, kf_b: Keyframe, t: float) -> Dict[str, Matrix4x4]:
        """Interpolate between two keyframes."""
        result = {}
        all_names = set(kf_a.pose_by_part_name.keys()) | set(kf_b.pose_by_part_name.keys())

        for name in all_names:
            ma = kf_a.pose_by_part_name.get(name, Matrix4x4.identity())
            mb = kf_b.pose_by_part_name.get(name, Matrix4x4.identity())
            result[name] = ma.lerp(mb, t)

        return result

    def _apply_matrix(self, matrix: Matrix4x4):
        """Apply 4x4 matrix to OpenGL."""
        # Transpose for OpenGL column-major format
        m = matrix.m.T.flatten()
        glMultMatrixf(m)

    def _draw_mesh(self, mesh_data: Dict):
        """Draw mesh data."""
        vertices = mesh_data['vertices']
        faces = mesh_data['faces']

        for face in faces:
            glBegin(GL_POLYGON)
            for idx in face:
                if 0 <= idx < len(vertices):
                    v = vertices[idx]
                    glVertex3f(v[0], v[1], v[2])
            glEnd()

    def mousePressEvent(self, event):
        """Handle mouse press."""
        self.last_pos = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse drag for rotation."""
        if self.last_pos:
            dx = event.pos().x() - self.last_pos.x()
            dy = event.pos().y() - self.last_pos.y()

            self.rotation_y += dx * 0.5
            self.rotation_x += dy * 0.5

            self.last_pos = event.pos()
            self.update()

    def wheelEvent(self, event):
        """Handle mouse wheel for zoom."""
        delta = event.angleDelta().y()
        self.zoom -= delta * 0.01
        self.zoom = max(1, min(50, self.zoom))
        self.update()

    def set_time(self, time: float):
        """Set animation time."""
        self.current_time = max(0, min(time, self.duration))
        self.update()


# Full animation viewer widget with controls

class AnimationViewerPanel(QWidget):
    """Animation viewer with playback controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewer = AnimationViewerWidget()
        self.is_playing = False

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Viewer
        layout.addWidget(self.viewer)

        # Controls
        controls_layout = QHBoxLayout()

        self.play_pause_btn = QPushButton('Play')
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.play_pause_btn.setFixedWidth(80)
        controls_layout.addWidget(self.play_pause_btn)

        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1000)
        self.time_slider.setValue(0)
        self.time_slider.sliderPressed.connect(self._on_slider_press)
        self.time_slider.sliderReleased.connect(self._on_slider_release)
        self.time_slider.valueChanged.connect(self._on_slider_changed)
        controls_layout.addWidget(self.time_slider)

        self.time_label = QLabel('0.00s / 0.00s')
        controls_layout.addWidget(self.time_label)

        layout.addLayout(controls_layout)

        self.setLayout(layout)

        # Playback timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_playback)
        self.slider_pressed = False

    def load_animation(self, rig_path: str, anim_path: str, mesh_dir: Optional[str] = None):
        """Load animation data."""
        self.viewer.load_animation_data(rig_path, anim_path, mesh_dir)
        self._update_time_label()

    def _toggle_play_pause(self):
        """Toggle playback."""
        if self.is_playing:
            self.is_playing = False
            self.play_pause_btn.setText('Play')
            self.timer.stop()
        else:
            self.is_playing = True
            self.play_pause_btn.setText('Pause')
            self.timer.start(16)  # ~60 FPS

    def _update_playback(self):
        """Update playback position."""
        if not self.slider_pressed:
            new_time = self.viewer.current_time + 0.016  # Add 16ms
            if new_time >= self.viewer.duration:
                new_time = 0  # Loop
            self.viewer.set_time(new_time)

            # Update slider
            if self.viewer.duration > 0:
                slider_val = int((new_time / self.viewer.duration) * 1000)
                self.time_slider.setValue(slider_val)

            self._update_time_label()

    def _on_slider_press(self):
        """Handle slider press."""
        self.slider_pressed = True

    def _on_slider_release(self):
        """Handle slider release."""
        self.slider_pressed = False

    def _on_slider_changed(self, value):
        """Handle slider change."""
        if self.viewer.duration > 0:
            new_time = (value / 1000.0) * self.viewer.duration
            self.viewer.set_time(new_time)
            self._update_time_label()

    def _update_time_label(self):
        """Update time display."""
        current = self.viewer.current_time
        duration = self.viewer.duration
        self.time_label.setText(f'{current:.2f}s / {duration:.2f}s')

    def clear(self):
        """Clear animation data."""
        self.is_playing = False
        self.timer.stop()
        self.play_pause_btn.setText('Play')
        self.viewer.parts = {}
        self.viewer.motors = []
        self.viewer.keyframes = []
        self.viewer.current_time = 0
        self.viewer.duration = 0
        self.viewer.update()
