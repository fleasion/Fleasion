from __future__ import annotations

import math
import pathlib
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, override

from defusedxml import ElementTree
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

type _Vec3 = tuple[float, float, float]
type _Mat3 = list[list[float]]
type _Quaternion = tuple[float, float, float, float]
type _Bounds = tuple[float, float, float, float, float, float]


class _Matrix4x4(Protocol):
    pass


class _Matrix4x4Factory(Protocol):
    def __call__(self) -> _Matrix4x4: ...


class _Light(Protocol):
    pass


class _LightFactory(Protocol):
    def __call__(self) -> _Light: ...


class _VtkModule(Protocol):
    pass


class _Mesh(Protocol):
    def triangulate(self) -> _Mesh: ...

    def clean(self) -> _Mesh: ...

    def compute_normals(
        self,
        *,
        cell_normals: bool,
        point_normals: bool,
        split_vertices: bool,
        auto_orient_normals: bool,
        consistent_normals: bool,
    ) -> _Mesh: ...


class _PyVistaModule(Protocol):
    def read(self, path: str) -> _Mesh: ...


class _Actor(Protocol):
    pass


class _Renderer(Protocol):
    pass


class _Camera(Protocol):
    focal_point: _Vec3
    position: _Vec3
    up: _Vec3
    view_angle: float


class _Plotter(Protocol):
    interactor: QWidget
    renderer: _Renderer
    bounds: _Bounds
    camera: _Camera

    def enable_anti_aliasing(self, mode: str) -> None: ...

    def set_background(self, color: _Vec3) -> None: ...

    def add_axes(self) -> object: ...

    def add_mesh(
        self,
        mesh: _Mesh,
        *,
        opacity: float,
        smooth_shading: bool,
        color: _Vec3,
        ambient: float,
        diffuse: float,
        specular: float,
        specular_power: float,
    ) -> _Actor: ...

    def render(self) -> None: ...

    def update(self) -> None: ...

    def close(self) -> None: ...


class _QtInteractorFactory(Protocol):
    def __call__(self, parent: QWidget) -> _Plotter: ...


if TYPE_CHECKING:
    import xml.etree.ElementTree as ET
    from collections.abc import Callable

    from PySide6.QtGui import QCloseEvent

    def _pyvista_type_adapter() -> _PyVistaModule:
        raise AssertionError

    def _vtk_type_adapter() -> _VtkModule:
        raise AssertionError

    pv = _pyvista_type_adapter()
    vtk = _vtk_type_adapter()
else:
    import pyvista as pv
    import vtk

if TYPE_CHECKING:

    def _qt_interactor_type_adapter() -> _QtInteractorFactory:
        raise AssertionError

    QtInteractor = _qt_interactor_type_adapter()
else:
    from pyvistaqt import QtInteractor

try:
    from fleasion.utils import log_buffer
except ImportError:
    log_buffer = None


_VTK_MATRIX_FACTORY = 'vtkMatrix4x4'
_VTK_LIGHT_FACTORY = 'vtkLight'
_MATRIX_IDENTITY = 'Identity'
_MATRIX_SET_ELEMENT = 'SetElement'
_MATRIX_GET_ELEMENT = 'GetElement'
_MATRIX_MULTIPLY = 'Multiply4x4'
_MATRIX_INVERT = 'Invert'
_LIGHT_HEADLIGHT = 'SetLightTypeToHeadlight'
_LIGHT_SCENE = 'SetLightTypeToSceneLight'
_LIGHT_INTENSITY = 'SetIntensity'
_LIGHT_POSITION = 'SetPosition'
_LIGHT_FOCAL_POINT = 'SetFocalPoint'
_PYVISTA_CUBE = 'Cube'
_ACTOR_USER_MATRIX = 'SetUserMatrix'
_RENDERER_REMOVE_LIGHTS = 'RemoveAllLights'
_RENDERER_ADD_LIGHT = 'AddLight'
_CAMERA_CLIPPING_RANGE = 'SetClippingRange'
_CAMERA_AZIMUTH = 'Azimuth'


def _call_void(target: object, method_name: str, *args: object) -> None:
    method = cast('Callable[..., None]', getattr(target, method_name))
    method(*args)


def _call_float(target: object, method_name: str, *args: object) -> float:
    method = cast('Callable[..., float]', getattr(target, method_name))
    return method(*args)


def _vtk_matrix_factory() -> _Matrix4x4Factory:
    return cast('_Matrix4x4Factory', getattr(vtk, _VTK_MATRIX_FACTORY))


def _vtk_light_factory() -> _LightFactory:
    return cast('_LightFactory', getattr(vtk, _VTK_LIGHT_FACTORY))


def _new_matrix() -> _Matrix4x4:
    return _vtk_matrix_factory()()


def _new_light() -> _Light:
    return _vtk_light_factory()()


def _cube(
    *, center: _Vec3, x_length: float, y_length: float, z_length: float
) -> _Mesh:
    factory = cast('Callable[..., _Mesh]', getattr(pv, _PYVISTA_CUBE))
    return factory(
        center=center,
        x_length=x_length,
        y_length=y_length,
        z_length=z_length,
    )


# XML helpers


def _text(elem: ET.Element | None, default: str = '') -> str:
    return elem.text if elem is not None and elem.text is not None else default


def find_prop(props: ET.Element, tag: str, names: list[str]) -> ET.Element | None:
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


def parse_vector3(elem: ET.Element) -> _Vec3:
    return (
        float(_text(elem.find('X'), '0')),
        float(_text(elem.find('Y'), '0')),
        float(_text(elem.find('Z'), '0')),
    )


def parse_cframe(elem: ET.Element) -> tuple[_Vec3, list[float]]:
    x = float(_text(elem.find('X'), '0'))
    y = float(_text(elem.find('Y'), '0'))
    z = float(_text(elem.find('Z'), '0'))
    r: list[float] = []
    for k in ('R00', 'R01', 'R02', 'R10', 'R11', 'R12', 'R20', 'R21', 'R22'):
        if k in {'R00', 'R11', 'R22'}:
            r.append(float(_text(elem.find(k), '1')))
        else:
            r.append(float(_text(elem.find(k), '0')))
    return (x, y, z), r


def vtk_matrix_from_cframe(pos: _Vec3, r: list[float]) -> _Matrix4x4:
    m = _new_matrix()
    _call_void(m, _MATRIX_IDENTITY)
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 0, r[0])
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 1, r[1])
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 2, r[2])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 0, r[3])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 1, r[4])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 2, r[5])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 0, r[6])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 1, r[7])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 2, r[8])
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 3, pos[0])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 3, pos[1])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 3, pos[2])
    return m


def mat_mul(a: _Matrix4x4, b: _Matrix4x4) -> _Matrix4x4:
    out = _new_matrix()
    _call_void(_vtk_matrix_factory(), _MATRIX_MULTIPLY, a, b, out)
    return out


def mat_inv(a: _Matrix4x4) -> _Matrix4x4:
    out = _new_matrix()
    _call_void(_vtk_matrix_factory(), _MATRIX_INVERT, a, out)
    return out


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# Some thing


def mat_get_translation(m: _Matrix4x4) -> _Vec3:
    return (_call_float(m, _MATRIX_GET_ELEMENT, 0, 3), _call_float(m, _MATRIX_GET_ELEMENT, 1, 3), _call_float(m, _MATRIX_GET_ELEMENT, 2, 3))


def mat_set_translation(m: _Matrix4x4, t: _Vec3) -> None:
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 3, t[0])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 3, t[1])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 3, t[2])


def mat_get_rot3(m: _Matrix4x4) -> _Mat3:
    return [
        [_call_float(m, _MATRIX_GET_ELEMENT, 0, 0), _call_float(m, _MATRIX_GET_ELEMENT, 0, 1), _call_float(m, _MATRIX_GET_ELEMENT, 0, 2)],
        [_call_float(m, _MATRIX_GET_ELEMENT, 1, 0), _call_float(m, _MATRIX_GET_ELEMENT, 1, 1), _call_float(m, _MATRIX_GET_ELEMENT, 1, 2)],
        [_call_float(m, _MATRIX_GET_ELEMENT, 2, 0), _call_float(m, _MATRIX_GET_ELEMENT, 2, 1), _call_float(m, _MATRIX_GET_ELEMENT, 2, 2)],
    ]


def mat_set_rot3(m: _Matrix4x4, r: _Mat3) -> None:
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 0, r[0][0])
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 1, r[0][1])
    _call_void(m, _MATRIX_SET_ELEMENT, 0, 2, r[0][2])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 0, r[1][0])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 1, r[1][1])
    _call_void(m, _MATRIX_SET_ELEMENT, 1, 2, r[1][2])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 0, r[2][0])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 1, r[2][1])
    _call_void(m, _MATRIX_SET_ELEMENT, 2, 2, r[2][2])


def quat_from_rot3(r: _Mat3) -> _Quaternion:
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


def rot3_from_quat(q: _Quaternion) -> _Mat3:
    w, x, y, z = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]


def quat_slerp(q0: _Quaternion, q1: _Quaternion, t: float) -> _Quaternion:
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


def matrix_trs_lerp(m0: _Matrix4x4, m1: _Matrix4x4, t: float) -> _Matrix4x4:
    t0 = mat_get_translation(m0)
    t1 = mat_get_translation(m1)
    tt = (lerp(t0[0], t1[0], t), lerp(t0[1], t1[1], t), lerp(t0[2], t1[2], t))
    q0 = quat_from_rot3(mat_get_rot3(m0))
    q1 = quat_from_rot3(mat_get_rot3(m1))
    qt = quat_slerp(q0, q1, t)
    rt = rot3_from_quat(qt)
    out = _new_matrix()
    _call_void(out, _MATRIX_IDENTITY)
    mat_set_rot3(out, rt)
    mat_set_translation(out, tt)
    return out


# Data models


@dataclass
class Part:
    referent: str
    name: str
    size: tuple[float, float, float]
    cframe: _Matrix4x4


@dataclass
class Motor6D:
    name: str
    part0_ref: str
    part1_ref: str
    c0: _Matrix4x4
    c1: _Matrix4x4


@dataclass
class Keyframe:
    time: float
    pose_by_part_name: dict[str, _Matrix4x4]


# Parse rig


def load_rig(rig_path: str) -> tuple[dict[str, Part], list[Motor6D]]:
    tree = ElementTree.parse(rig_path)
    root = cast('ET.Element', tree.getroot())

    parts: dict[str, Part] = {}
    motors: list[Motor6D] = []

    for item in root.iter('Item'):
        cls = item.attrib.get('class', '')
        ref = item.attrib.get('referent', '')
        props = item.find('Properties')
        if props is None:
            continue

        size_elem = find_prop(props, 'Vector3', ['size', 'Size', 'InitialSize'])
        cf_elem = find_prop(props, 'CoordinateFrame', ['CFrame']) or find_prop(
            props, 'CFrame', ['CFrame']
        )

        if size_elem is not None and cf_elem is not None:
            name = _text(find_prop(props, 'string', ['Name']), cls)
            size = parse_vector3(size_elem)
            pos, r = parse_cframe(cf_elem)
            parts[ref] = Part(ref, name, size, vtk_matrix_from_cframe(pos, r))

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

            motors.append(
                Motor6D(
                    name=name,
                    part0_ref=_text(p0),
                    part1_ref=_text(p1),
                    c0=vtk_matrix_from_cframe(pos0, r0),
                    c1=vtk_matrix_from_cframe(pos1, r1),
                )
            )

    return parts, motors


# Parse anim


def load_animation(anim_path: str) -> list[Keyframe]:
    tree = ElementTree.parse(anim_path)
    root = cast('ET.Element', tree.getroot())

    keys: list[Keyframe] = []
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

        poses: dict[str, _Matrix4x4] = {}
        for pose_item in item.iter('Item'):
            if pose_item.attrib.get('class') != 'Pose':
                continue
            pprops = pose_item.find('Properties')
            if pprops is None:
                continue

            pname = _text(find_prop(pprops, 'string', ['Name']))
            cf = find_prop(pprops, 'CoordinateFrame', ['CFrame']) or find_prop(
                pprops, 'CFrame', ['CFrame']
            )
            if not pname or cf is None:
                continue

            pos, r = parse_cframe(cf)
            poses[pname] = vtk_matrix_from_cframe(pos, r)

        keys.append(Keyframe(t, poses))

    keys.sort(key=lambda k: k.time)
    return keys


def sample_keys(keys: list[Keyframe], t: float) -> tuple[Keyframe, Keyframe, float]:
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


# Root picking


def pick_root_ref(parts: dict[str, Part]) -> str:
    preferred = ('HumanoidRootPart', 'LowerTorso', 'Torso', 'UpperTorso', 'Head')
    for want in preferred:
        for ref, p in parts.items():
            if p.name == want:
                return ref
    return next(iter(parts.keys()))


# OBJ loading


def detect_rig_prefix(parts: dict[str, Part]) -> str:
    # R6 has these part names; R15 has UpperTorso/LowerTorso etc.
    names = {p.name for p in parts.values()}
    if 'Torso' in names and 'UpperTorso' not in names:
        return 'R6'
    return 'R15'


def obj_path_for_part(mesh_dir: str, prefix: str, part_name: str) -> str:
    return str(pathlib.Path(mesh_dir) / f'{prefix}{part_name}.obj')


def load_obj_mesh(
    mesh_dir: str,
    prefix: str,
    part_name: str,
    fallback_size: _Vec3,
) -> _Mesh:

    candidates = [obj_path_for_part(mesh_dir, prefix, part_name)]

    other = 'R15' if prefix == 'R6' else 'R6'
    candidates.append(obj_path_for_part(mesh_dir, other, part_name))

    for path in candidates:
        if pathlib.Path(path).exists():
            try:
                mesh = pv.read(path).triangulate().clean()
                return mesh.compute_normals(
                    cell_normals=False,
                    point_normals=True,
                    split_vertices=False,
                    auto_orient_normals=True,
                    consistent_normals=True,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                if log_buffer is not None:
                    message = f'Failed to read {path}: {exc}'
                    log_buffer.log('AnimPreview', message)

    # Fallback cube if missing
    return _cube(
        center=(0, 0, 0),
        x_length=fallback_size[0],
        y_length=fallback_size[1],
        z_length=fallback_size[2],
    )


# Qt + VTK viewport player


class AnimPreviewWidget(QWidget):
    def __init__(
        self,
        rig_path: str,
        anim_path: str,
        mesh_dir: str = 'R15AndR6Parts',
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.plotter: _Plotter = QtInteractor(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plotter.interactor)

        # AA + background + axes first
        try:
            self.plotter.enable_anti_aliasing('ssaa')
        except RuntimeError, ValueError:
            self.plotter.enable_anti_aliasing('fxaa')

        self.plotter.set_background((0.95, 0.95, 0.95))
        self.plotter.add_axes()

        # Lights
        ren = self.plotter.renderer
        _call_void(ren, _RENDERER_REMOVE_LIGHTS)

        head = _new_light()
        _call_void(head, _LIGHT_HEADLIGHT)
        _call_void(head, _LIGHT_INTENSITY, 0.95)
        _call_void(ren, _RENDERER_ADD_LIGHT, head)

        fill = _new_light()
        _call_void(fill, _LIGHT_SCENE)
        _call_void(fill, _LIGHT_POSITION, -6, 6, 10)
        _call_void(fill, _LIGHT_FOCAL_POINT, 0, 0, 0)
        _call_void(fill, _LIGHT_INTENSITY, 0.35)
        _call_void(ren, _RENDERER_ADD_LIGHT, fill)

        rim = _new_light()
        _call_void(rim, _LIGHT_SCENE)
        _call_void(rim, _LIGHT_POSITION, 0, -8, 6)
        _call_void(rim, _LIGHT_FOCAL_POINT, 0, 0, 0)
        _call_void(rim, _LIGHT_INTENSITY, 0.20)
        _call_void(ren, _RENDERER_ADD_LIGHT, rim)

        # Load data
        self.parts, self.motors = load_rig(rig_path)
        self.prefix = detect_rig_prefix(self.parts)
        self.keys = load_animation(anim_path)

        if not self.parts:
            msg = 'Loaded 0 parts from rig. Wrong rig file or unexpected format.'
            raise RuntimeError(msg)
        if not self.keys:
            msg = (
                'Loaded 0 keyframes from animation. output.rbxmx must be a KeyframeSequence export.'
            )
            raise RuntimeError(msg)

        # Actors
        self.actors_by_part_ref: dict[str, _Actor] = {}
        for ref, p in self.parts.items():
            mesh = load_obj_mesh(mesh_dir, self.prefix, p.name, p.size)
            is_hrp = p.name.lower() == 'humanoidrootpart'
            actor = self.plotter.add_mesh(
                mesh,
                opacity=0.5 if is_hrp else 1.0,
                smooth_shading=True,
                color=(1.0, 0.2, 0.2) if is_hrp else (0.82, 0.82, 0.84),
                ambient=0.35,
                diffuse=0.65,
                specular=0.08,
                specular_power=20,
            )
            _call_void(actor, _ACTOR_USER_MATRIX, p.cframe)
            self.actors_by_part_ref[ref] = actor

        xmin, xmax, ymin, ymax, zmin, zmax = self.plotter.bounds
        cx = (xmin + xmax) / 2
        cy = (ymin + ymax) / 2
        cz = (zmin + zmax) / 2

        size = max(xmax - xmin, ymax - ymin, zmax - zmin)
        dist = size * 2.5 if size > 0 else 10.0

        cam = self.plotter.camera
        cam.focal_point = (cx, cy, cz)
        cam.position = (cx, cy, cz + dist)
        cam.up = (0, 1, 0)
        cam.view_angle = 30
        _call_void(cam, _CAMERA_CLIPPING_RANGE, 0.001, dist * 50)
        _call_void(cam, _CAMERA_AZIMUTH, 205)
        self.plotter.render()
        # Root + timing
        self.root_ref = pick_root_ref(self.parts)
        self.root_name = self.parts[self.root_ref].name
        self.base_root_world = self.parts[self.root_ref].cframe

        self.time = 0.0
        self.duration = max(self.keys[-1].time, 1e-6)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def tick(self) -> None:
        self.time += 0.016
        if self.time > self.duration:
            self.time = 0.0

        k0, k1, alpha = sample_keys(self.keys, self.time)

        pose: dict[str, _Matrix4x4] = {}
        names = set(k0.pose_by_part_name.keys()) | set(k1.pose_by_part_name.keys())
        ident = _new_matrix()
        _call_void(ident, _MATRIX_IDENTITY)

        for n in names:
            a = k0.pose_by_part_name.get(n)
            b = k1.pose_by_part_name.get(n)
            if a is None:
                pose[n] = cast('_Matrix4x4', b)
            elif b is None:
                pose[n] = a
            else:
                pose[n] = matrix_trs_lerp(a, b, alpha)

        root_pose = pose.get(self.root_name, ident)
        world: dict[str, _Matrix4x4] = {self.root_ref: mat_mul(self.base_root_world, root_pose)}

        for _ in range(25):
            changed = False
            for m in self.motors:
                if m.part0_ref not in world:
                    continue
                child = self.parts.get(m.part1_ref)
                if child is None:
                    continue

                part_pose = pose.get(child.name, ident)
                part1 = mat_mul(
                    mat_mul(mat_mul(world[m.part0_ref], m.c0), part_pose), mat_inv(m.c1)
                )

                world[m.part1_ref] = part1
                changed = True
            if not changed:
                break

        for ref, actor in self.actors_by_part_ref.items():
            w = world.get(ref)
            if w is not None:
                _call_void(actor, _ACTOR_USER_MATRIX, w)

        self.plotter.update()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.timer.stop()
        except RuntimeError:
            pass
        try:
            self.plotter.close()
        except RuntimeError, ValueError:
            pass
        super().closeEvent(event)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python animpreview.py <RIG.rbxmx> <ANIM.rbxmx>')
        sys.exit(2)

    app = QApplication(sys.argv)
    w = AnimPreviewWidget(sys.argv[1], sys.argv[2], mesh_dir='R15AndR6Parts')
    w.resize(1100, 800)
    w.show()
    sys.exit(app.exec())
