"""Qt Quick 3D geometry for cached Roblox meshes."""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

from PySide6.QtGui import QVector3D
from PySide6.QtQuick3D import QQuick3DGeometry

from ..cache.mesh_processing import convert

if TYPE_CHECKING:
    from collections.abc import Iterable


def _normal(a: QVector3D, b: QVector3D, c: QVector3D) -> QVector3D:
    value = QVector3D.crossProduct(b - a, c - a)
    return value.normalized() if not value.isNull() else QVector3D(0, 1, 0)


def _obj_triangles(content: str) -> list[tuple[QVector3D, QVector3D]]:
    positions: list[QVector3D] = []
    normals: list[QVector3D] = []
    triangles: list[tuple[QVector3D, QVector3D]] = []
    for raw_line in content.splitlines():
        parts = raw_line.strip().split()
        if len(parts) >= 4 and parts[0] == 'v':
            positions.append(QVector3D(float(parts[1]), float(parts[2]), float(parts[3])))
        elif len(parts) >= 4 and parts[0] == 'vn':
            normals.append(QVector3D(float(parts[1]), float(parts[2]), float(parts[3])))
        elif len(parts) >= 4 and parts[0] == 'f':
            vertices: list[tuple[int, int | None]] = []
            for token in parts[1:]:
                components = token.split('/')
                vertex_index = int(components[0]) - 1
                normal_index = (
                    int(components[2]) - 1 if len(components) > 2 and components[2] else None
                )
                vertices.append((vertex_index, normal_index))
            for index in range(1, len(vertices) - 1):
                face = (vertices[0], vertices[index], vertices[index + 1])
                points = [positions[vertex_index] for vertex_index, _ in face]
                face_normal = _normal(*points)
                for point, (_, normal_index) in zip(points, face, strict=True):
                    normal = (
                        normals[normal_index]
                        if normal_index is not None and 0 <= normal_index < len(normals)
                        else face_normal
                    )
                    triangles.append((point, normal))
    return triangles


def _bounds(points: Iterable[QVector3D]) -> tuple[QVector3D, QVector3D]:
    values = list(points)
    if not values:
        return QVector3D(), QVector3D()
    return (
        QVector3D(
            min(point.x() for point in values),
            min(point.y() for point in values),
            min(point.z() for point in values),
        ),
        QVector3D(
            max(point.x() for point in values),
            max(point.y() for point in values),
            max(point.z() for point in values),
        ),
    )


class MeshGeometry(QQuick3DGeometry):
    """Create normalized triangle geometry from Roblox mesh bytes."""

    def load(self, payload: bytes) -> bool:
        """Replace this geometry with a decoded Roblox mesh."""
        content = convert(payload)
        if not content:
            self.clear()
            return False
        try:
            triangles = _obj_triangles(content)
        except IndexError, ValueError:
            self.clear()
            return False
        if not triangles:
            self.clear()
            return False

        minimum, maximum = _bounds(point for point, _normal_value in triangles)
        center = (minimum + maximum) * 0.5
        size = maximum - minimum
        scale = max(abs(size.x()), abs(size.y()), abs(size.z()), math.ulp(1.0)) * 0.5
        packed = bytearray()
        normalized_points: list[QVector3D] = []
        for point, normal in triangles:
            normalized = (point - center) * (1.0 / scale)
            normalized_points.append(normalized)
            packed.extend(
                struct.pack(
                    '<6f',
                    normalized.x(),
                    normalized.y(),
                    normalized.z(),
                    normal.x(),
                    normal.y(),
                    normal.z(),
                )
            )
        normalized_min, normalized_max = _bounds(normalized_points)
        self.clear()
        self.setStride(24)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.PositionSemantic,
            0,
            QQuick3DGeometry.Attribute.ComponentType.F32Type,
        )
        self.addAttribute(
            QQuick3DGeometry.Attribute.Semantic.NormalSemantic,
            12,
            QQuick3DGeometry.Attribute.ComponentType.F32Type,
        )
        self.setVertexData(packed)
        self.setBounds(normalized_min, normalized_max)
        return True
