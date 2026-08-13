"""Qt-free Roblox animation parsing and sparse-track interpolation."""

from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

Matrix: TypeAlias = NDArray[np.float32]
Quaternion: TypeAlias = tuple[float, float, float, float]


@dataclass(slots=True)
class AnimationKeyframe:
    """A sampled animation time and its transforms, keyed by part name."""

    time: float
    pose_by_part_name: dict[str, Matrix]


def _matrix_from_cframe(position: tuple[float, float, float], rotation: list[float]) -> Matrix:
    matrix = np.eye(4, dtype=np.float32)
    matrix[0, 0:3] = rotation[0:3]
    matrix[1, 0:3] = rotation[3:6]
    matrix[2, 0:3] = rotation[6:9]
    matrix[0, 3], matrix[1, 3], matrix[2, 3] = position
    return matrix


def _quaternion_from_rotation(rotation: Matrix) -> Quaternion:
    trace = float(rotation[0, 0] + rotation[1, 1] + rotation[2, 2])
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = (
            0.25 * scale,
            float(rotation[2, 1] - rotation[1, 2]) / scale,
            float(rotation[0, 2] - rotation[2, 0]) / scale,
            float(rotation[1, 0] - rotation[0, 1]) / scale,
        )
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math.sqrt(1.0 + float(rotation[0, 0] - rotation[1, 1] - rotation[2, 2])) * 2.0
        quaternion = (
            float(rotation[2, 1] - rotation[1, 2]) / scale,
            0.25 * scale,
            float(rotation[0, 1] + rotation[1, 0]) / scale,
            float(rotation[0, 2] + rotation[2, 0]) / scale,
        )
    elif rotation[1, 1] > rotation[2, 2]:
        scale = math.sqrt(1.0 + float(rotation[1, 1] - rotation[0, 0] - rotation[2, 2])) * 2.0
        quaternion = (
            float(rotation[0, 2] - rotation[2, 0]) / scale,
            float(rotation[0, 1] + rotation[1, 0]) / scale,
            0.25 * scale,
            float(rotation[1, 2] + rotation[2, 1]) / scale,
        )
    else:
        scale = math.sqrt(1.0 + float(rotation[2, 2] - rotation[0, 0] - rotation[1, 1])) * 2.0
        quaternion = (
            float(rotation[1, 0] - rotation[0, 1]) / scale,
            float(rotation[0, 2] + rotation[2, 0]) / scale,
            float(rotation[1, 2] + rotation[2, 1]) / scale,
            0.25 * scale,
        )
    length = math.sqrt(sum(component * component for component in quaternion)) or 1.0
    return (
        quaternion[0] / length,
        quaternion[1] / length,
        quaternion[2] / length,
        quaternion[3] / length,
    )


def _rotation_from_quaternion(quaternion: Quaternion) -> Matrix:
    w, x, y, z = quaternion
    return np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


def _slerp(
    first: Quaternion,
    second: Quaternion,
    alpha: float,
) -> Quaternion:
    dot = sum(left * right for left, right in zip(first, second, strict=True))
    if dot < 0.0:
        dot = -dot
        second = (-second[0], -second[1], -second[2], -second[3])
    if dot > 0.9995:
        result = tuple(
            left + (right - left) * alpha
            for left, right in zip(first, second, strict=True)
        )
        length = math.sqrt(sum(component * component for component in result)) or 1.0
        return (result[0] / length, result[1] / length, result[2] / length, result[3] / length)

    theta = math.acos(max(-1.0, min(1.0, dot)))
    sine = math.sin(theta) or 1e-8
    first_scale = math.sin(theta * (1.0 - alpha)) / sine
    second_scale = math.sin(theta * alpha) / sine
    result = tuple(
        left * first_scale + right * second_scale
        for left, right in zip(first, second, strict=True)
    )
    return result[0], result[1], result[2], result[3]


def _interpolate_matrix(first: Matrix, second: Matrix, alpha: float) -> Matrix:
    result = np.eye(4, dtype=np.float32)
    result[0:3, 3] = first[0:3, 3] + (second[0:3, 3] - first[0:3, 3]) * alpha
    result[0:3, 0:3] = _rotation_from_quaternion(
        _slerp(
            _quaternion_from_rotation(first[0:3, 0:3]),
            _quaternion_from_rotation(second[0:3, 0:3]),
            alpha,
        )
    )
    return result


def _fill_sparse_tracks(keyframes: list[AnimationKeyframe]) -> list[AnimationKeyframe]:
    if len(keyframes) < 2:
        return keyframes

    tracks: dict[str, list[tuple[int, Matrix]]] = {}
    for index, keyframe in enumerate(keyframes):
        for name, transform in keyframe.pose_by_part_name.items():
            tracks.setdefault(name, []).append((index, transform))

    for name, samples in tracks.items():
        first_index, first_transform = samples[0]
        for index in range(first_index):
            keyframes[index].pose_by_part_name[name] = first_transform

        for (left_index, left), (right_index, right) in zip(samples, samples[1:]):
            if right_index == left_index + 1:
                continue
            start = keyframes[left_index].time
            span = keyframes[right_index].time - start
            for index in range(left_index + 1, right_index):
                alpha = (keyframes[index].time - start) / span if span > 0 else 0.0
                keyframes[index].pose_by_part_name[name] = _interpolate_matrix(
                    left,
                    right,
                    alpha,
                )

        last_index, last_transform = samples[-1]
        for index in range(last_index + 1, len(keyframes)):
            keyframes[index].pose_by_part_name[name] = last_transform

    return keyframes


def _text(element: ET.Element | None, default: str = '') -> str:
    return element.text if element is not None and element.text is not None else default


def _find_property(
    properties: ET.Element,
    tag: str,
    names: tuple[str, ...],
) -> ET.Element | None:
    folded_names = {name.casefold() for name in names}
    return next(
        (
            child
            for child in properties
            if child.tag == tag and child.attrib.get('name', '').casefold() in folded_names
        ),
        None,
    )


def _parse_cframe(element: ET.Element) -> tuple[tuple[float, float, float], list[float]]:
    position = (
        float(_text(element.find('X'), '0')),
        float(_text(element.find('Y'), '0')),
        float(_text(element.find('Z'), '0')),
    )
    rotation = [
        float(_text(element.find(name), '1' if name in {'R00', 'R11', 'R22'} else '0'))
        for name in ('R00', 'R01', 'R02', 'R10', 'R11', 'R12', 'R20', 'R21', 'R22')
    ]
    return position, rotation


def _load_xml_keyframes(data: bytes) -> list[AnimationKeyframe]:
    text = data.decode('utf-8-sig', errors='replace')
    root = ET.fromstring(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text))
    keyframes: list[AnimationKeyframe] = []
    for item in root.iter('Item'):
        if item.attrib.get('class') != 'Keyframe' or (properties := item.find('Properties')) is None:
            continue
        time_element = _find_property(properties, 'float', ('Time',))
        if time_element is None:
            continue
        poses: dict[str, Matrix] = {}
        for pose in item.iter('Item'):
            if pose.attrib.get('class') != 'Pose' or (pose_properties := pose.find('Properties')) is None:
                continue
            name = _text(_find_property(pose_properties, 'string', ('Name',)))
            weight = float(_text(_find_property(pose_properties, 'float', ('Weight',)), '1'))
            cframe = _find_property(pose_properties, 'CoordinateFrame', ('CFrame',))
            if cframe is None:
                cframe = _find_property(pose_properties, 'CFrame', ('CFrame',))
            if not name or cframe is None or weight <= 0:
                continue
            position, rotation = _parse_cframe(cframe)
            poses[name] = _matrix_from_cframe(position, rotation)
        keyframes.append(AnimationKeyframe(float(_text(time_element, '0')), poses))
    return sorted(keyframes, key=lambda keyframe: keyframe.time)


def _collect_binary_poses(instance: Any, poses: dict[str, Matrix]) -> None:
    for child in instance.children:
        if child.class_name == 'Pose':
            name = child.properties.get('Name', '')
            cframe = child.properties.get('CFrame')
            weight = child.properties.get('Weight', 1.0)
            if (
                isinstance(name, str)
                and name
                and isinstance(cframe, dict)
                and isinstance(weight, (int, float))
                and weight > 0
            ):
                position = cframe.get('position', (0.0, 0.0, 0.0))
                rotation = cframe.get(
                    'rotation',
                    [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                )
                poses[name] = _matrix_from_cframe(tuple(position), list(rotation))
            _collect_binary_poses(child, poses)


def _load_binary_keyframes(data: bytes) -> list[AnimationKeyframe]:
    from .rbxm_parser import find_by_class, parse_rbxm

    keyframes: list[AnimationKeyframe] = []
    for instance in find_by_class(parse_rbxm(data), 'Keyframe'):
        raw_time = instance.properties.get('Time', 0.0)
        time = float(raw_time) if isinstance(raw_time, (int, float)) else 0.0
        poses: dict[str, Matrix] = {}
        _collect_binary_poses(instance, poses)
        keyframes.append(AnimationKeyframe(time, poses))
    return sorted(keyframes, key=lambda keyframe: keyframe.time)


def load_animation_data(data: bytes) -> list[AnimationKeyframe]:
    """Parse XML, binary, or CurveAnimation data without importing presentation code."""
    if b'CurveAnimation' in data:
        try:
            from ..utils.anim_converter import curve_anim_to_keyframe

            data = curve_anim_to_keyframe(data)
        except (TypeError, ValueError):
            log.debug('Could not convert CurveAnimation data', exc_info=True)

    detected = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
    try:
        keyframes = (
            _load_binary_keyframes(data)
            if detected.startswith(b'<roblox!')
            else _load_xml_keyframes(data)
        )
    except (ET.ParseError, UnicodeError, ValueError, IndexError):
        log.debug('Could not parse animation document', exc_info=True)
        return []
    return _fill_sparse_tracks(keyframes)
