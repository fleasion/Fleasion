"""Pure R6 and R15 animation conversion workflow helpers."""

from __future__ import annotations

import io
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .anim_converter import curve_anim_to_keyframe, detect_rig, is_curve_animation, rbxm_to_rbxmx
from .r15_to_r6 import (
    convert_keyframe_r6_to_r15,
    convert_keyframe_r15_to_r6,
    sanitize_xml,
)
from .rig_data import R6_JOINTS, R6_PARTS, R15_JOINTS, R15_PARTS

type AnimationRig = Literal['R6', 'R15']


@dataclass(frozen=True, slots=True)
class PreparedAnimation:
    """Animation source normalized to editable RBXMX bytes."""

    source_path: Path
    xml_bytes: bytes
    detected_rig: str
    converted_from_binary: bool


def prepare_animation_source(path: Path) -> PreparedAnimation:
    """Read an animation and normalize binary RBXM input to RBXMX.

    Parameters
    ----------
    path
        Local ``.rbxm`` or ``.rbxmx`` animation source.

    Returns
    -------
    PreparedAnimation
        Original rig detection plus XML bytes ready for conversion.
    """
    data = path.read_bytes()
    if not data:
        raise ValueError('The animation file is empty.')
    detected_rig = detect_rig(data)
    converted_from_binary = path.suffix.casefold() == '.rbxm'
    xml_bytes = rbxm_to_rbxmx(data) if converted_from_binary else data
    return PreparedAnimation(
        source_path=path,
        xml_bytes=xml_bytes,
        detected_rig=detected_rig,
        converted_from_binary=converted_from_binary,
    )


def convert_animation_rig(data: bytes, target: AnimationRig) -> bytes:
    """Convert all keyframes in the first animation sequence to a target rig.

    Parameters
    ----------
    data
        RBXMX animation bytes, optionally containing a ``CurveAnimation``.
    target
        Destination player rig.

    Returns
    -------
    bytes
        UTF-8 RBXMX with an XML declaration.

    Raises
    ------
    ValueError
        If the target or animation document is unsupported.
    """
    if target not in {'R6', 'R15'}:
        raise ValueError('Animation target must be R6 or R15.')
    xml_bytes = curve_anim_to_keyframe(data) if is_curve_animation(data) else data
    try:
        root = ET.fromstring(sanitize_xml(xml_bytes))
    except ET.ParseError as exc:
        raise ValueError('The animation is not valid RBXMX XML.') from exc
    sequence = root.find("Item[@class='KeyframeSequence']")
    if sequence is None:
        raise ValueError('No KeyframeSequence was found in the animation.')
    keyframes = sequence.findall("Item[@class='Keyframe']")
    if not keyframes:
        raise ValueError('No Keyframes were found in the animation.')

    for keyframe in keyframes:
        if target == 'R6':
            convert_keyframe_r15_to_r6(
                keyframe,
                R6_PARTS,
                R6_JOINTS,
                R15_PARTS,
                R15_JOINTS,
            )
        else:
            convert_keyframe_r6_to_r15(
                keyframe,
                R6_PARTS,
                R6_JOINTS,
                R15_PARTS,
                R15_JOINTS,
            )

    stream = io.BytesIO()
    ET.ElementTree(root).write(stream, encoding='utf-8', xml_declaration=True)
    return stream.getvalue()


def save_animation_conversion(data: bytes, destination: Path) -> Path:
    """Atomically save converted animation bytes to a local path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = [
    'AnimationRig',
    'PreparedAnimation',
    'convert_animation_rig',
    'prepare_animation_source',
    'save_animation_conversion',
]
