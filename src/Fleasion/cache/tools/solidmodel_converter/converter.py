"""High-level converter from RBXM binary to .rbxm / .rbxmx / .obj formats.

Also handles extracting embedded ChildData from PartOperationAsset blobs
and injecting MeshData into child PartOperation instances.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .csg_mesh import export_obj, parse_csg_mesh
from .rbxm.deserializer import RbxmDeserializer
from .rbxm.types import (
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxProperty,
)
from .rbxm.xml_writer import write_rbxmx

log = logging.getLogger(__name__)


def convert_file(
    input_path: Path,
    output_path: Path,
    *,
    extract_child_data: bool = False,
    export_obj_mode: bool = False,
    decompose: bool = False,
) -> None:
    """Convert a .bin / .rbxm file to .rbxm, .rbxmx, or .obj.

    Parameters
    ----------
    input_path
        Path to the input binary file.
    output_path
        Path for the output file. Format is inferred from the extension:
        - ``.rbxm`` copies the binary as-is (it already is valid RBXM).
        - ``.rbxmx`` deserializes and writes XML.
        - ``.obj`` extracts mesh data and exports as Wavefront OBJ.
    extract_child_data
        If True and the top-level object has a ChildData property containing
        an embedded RBXM, deserialize and convert that instead.  The top-level
        MeshData will be injected into the root PartOperation within the
        extracted ChildData.
    export_obj_mode
        If True, extract and decrypt MeshData and export as .obj.
    decompose
        If True (with OBJ export), export per-operation meshes (Union +
        NegativeParts) with CFrame transforms and materials.  If False
        (default), export the pre-computed final result.

    """
    raw = input_path.read_bytes()
    doc = deserialize_rbxm(raw)

    suffix = output_path.suffix.lower()

    # OBJ export mode
    if export_obj_mode or suffix == '.obj':
        _export_obj_from_doc(doc, output_path, decompose=decompose)
        return

    if extract_child_data:
        top_mesh_data = _get_top_level_mesh_data(doc)
        child_doc = _try_extract_child_data(doc)
        if child_doc is not None:
            log.info('Extracted embedded ChildData document')
            doc = child_doc
            # Inject the top-level MeshData into root PartOperation(s)
            if top_mesh_data is not None:
                _inject_mesh_data(doc, top_mesh_data)

    if suffix == '.rbxmx':
        log.info('Writing RBXMX (XML) to %s', output_path)
        xml_bytes = write_rbxmx(doc)
        output_path.write_bytes(xml_bytes)
    elif suffix == '.rbxm':
        if not extract_child_data:
            log.info('Copying raw RBXM binary to %s', output_path)
            shutil.copy2(input_path, output_path)
        else:
            # When extracting child data, we need to write the modified
            # document as RBXMX (XML) since we may have injected MeshData
            # or the child data needs proper serialization.
            rbxmx_path = output_path.with_suffix('.rbxmx')
            log.info('Writing extracted ChildData as RBXMX to %s', rbxmx_path)
            xml_bytes = write_rbxmx(doc)
            rbxmx_path.write_bytes(xml_bytes)
    else:
        msg = f'Unsupported output format: {suffix!r} (use .rbxm, .rbxmx, or .obj)'
        raise ValueError(msg)

    log.info('Done: %s -> %s', input_path, output_path)


def deserialize_rbxm(data: bytes) -> RbxDocument:
    """Deserialize raw RBXM bytes into a document model."""
    return RbxmDeserializer().deserialize(data)


# ---------------------------------------------------------------------------
# OBJ export helpers
# ---------------------------------------------------------------------------


def _export_obj_from_doc(
    doc: RbxDocument,
    output_path: Path,
    *,
    decompose: bool = False,
) -> None:
    """Extract MeshData from the document and export as OBJ.

    Default mode exports the pre-computed final Boolean result (top-level
    MeshData, visual-only sub-mesh).  With decompose=True, exports
    per-operation meshes from ChildData with CFrame transforms and materials.
    """
    from .csg_mesh import (
        ObjMeshPart,
        export_obj_multi,
        parse_csg_mesh_full,
    )

    if decompose:
        # Decomposed mode: per-operation meshes from ChildData
        child_doc = _try_extract_child_data(doc)
        if child_doc is not None:
            parts = _collect_mesh_parts(child_doc)
            if parts:
                log.info(
                    'Exporting %d per-operation meshes from ChildData (decomposed)',
                    len(parts),
                )
                obj_parts: list[ObjMeshPart] = []
                for name, class_name, mesh_bytes, cframe in parts:
                    result = parse_csg_mesh_full(mesh_bytes)
                    vertices = result.vertices
                    indices = result.indices

                    if result.submesh_boundaries and len(result.submesh_boundaries) > 1:
                        visual_end = result.submesh_boundaries[1]
                        total = len(indices)
                        indices = indices[:visual_end]
                        log.info(
                            '  %s (%s): %d vertices, %d/%d visual indices (%d visual, skipped %d auxiliary)',
                            name, class_name, len(vertices), len(indices), total,
                            len(indices) // 3, (total - len(indices)) // 3,
                        )
                    else:
                        log.info(
                            '  %s (%s): %d vertices, %d indices (%d triangles)',
                            name, class_name, len(vertices), len(indices), len(indices) // 3,
                        )

                    obj_parts.append(ObjMeshPart(
                        name=name,
                        class_name=class_name,
                        vertices=vertices,
                        indices=indices,
                        cframe=cframe,
                    ))
                export_obj_multi(obj_parts, output_path)
                return

    # Default mode: pre-computed final result from top-level MeshData
    mesh_data = _get_top_level_mesh_data(doc)
    if mesh_data is None:
        msg = 'No MeshData found in the document'
        raise ValueError(msg)

    result = parse_csg_mesh_full(mesh_data)
    vertices = result.vertices
    indices = result.indices

    # Use only the visual sub-mesh for v3/v4
    if result.submesh_boundaries and len(result.submesh_boundaries) > 1:
        visual_end = result.submesh_boundaries[1]
        total = len(indices)
        indices = indices[:visual_end]
        log.info(
            'Exporting pre-computed result: %d vertices, %d/%d visual indices (%d visual, skipped %d auxiliary)',
            len(vertices), len(indices), total,
            len(indices) // 3, (total - len(indices)) // 3,
        )
    else:
        log.info(
            'Exporting pre-computed result: %d vertices, %d indices (%d triangles)',
            len(vertices), len(indices), len(indices) // 3,
        )

    obj_name = 'CSGMesh'
    for inst in doc.roots:
        name_prop = inst.properties.get('Name')
        if name_prop is not None and isinstance(name_prop.value, str):
            obj_name = name_prop.value
            break

    export_obj(
        vertices,
        indices,
        output_path,
        object_name=obj_name,
    )


def _collect_mesh_parts(
    doc: RbxDocument,
) -> list[tuple[str, str, bytes, dict | None]]:
    """Collect (name, class_name, mesh_data, cframe) from all root PartOperations."""
    _PART_OP_CLASSES = {'UnionOperation', 'NegateOperation', 'PartOperation'}
    parts: list[tuple[str, str, bytes, dict | None]] = []

    for inst in doc.roots:
        if inst.class_name not in _PART_OP_CLASSES:
            continue
        mesh_prop = inst.properties.get('MeshData')
        if mesh_prop is None or not isinstance(mesh_prop.value, bytes) or len(mesh_prop.value) == 0:
            continue

        name_prop = inst.properties.get('Name')
        name = name_prop.value if name_prop and isinstance(name_prop.value, str) else inst.class_name

        # Extract CFrame for transform
        cframe_prop = inst.properties.get('CFrame')
        cframe = cframe_prop.value if cframe_prop is not None and isinstance(cframe_prop.value, dict) else None

        parts.append((name, inst.class_name, mesh_prop.value, cframe))

    return parts


# ---------------------------------------------------------------------------
# MeshData extraction and injection
# ---------------------------------------------------------------------------


def _get_top_level_mesh_data(doc: RbxDocument) -> bytes | None:
    """Get the MeshData from the top-level PartOperationAsset."""
    for inst in doc.roots:
        mesh_prop = inst.properties.get('MeshData')
        if mesh_prop is not None and isinstance(mesh_prop.value, bytes) and len(mesh_prop.value) > 0:
            return mesh_prop.value
    # Also check non-root instances
    for inst in doc.instances.values():
        mesh_prop = inst.properties.get('MeshData')
        if mesh_prop is not None and isinstance(mesh_prop.value, bytes) and len(mesh_prop.value) > 0:
            return mesh_prop.value
    return None


def _get_top_level_mesh_data_from_raw(raw: bytes) -> bytes | None:
    """Get the top-level MeshData from raw RBXM bytes."""
    doc = RbxmDeserializer().deserialize(raw)
    return _get_top_level_mesh_data(doc)


def _inject_mesh_data(doc: RbxDocument, mesh_data: bytes) -> None:
    """Inject MeshData into the root PartOperation instances.

    The ChildData RBXM typically contains UnionOperation and/or NegateOperation
    instances with empty MeshData. We inject the top-level MeshData into
    root instances that lack their own mesh data.

    If ALL root instances already have non-empty MeshData, we skip injection
    entirely — they already have their own valid data from the CDN/dictionary.
    """
    _PART_OP_CLASSES = {'UnionOperation', 'NegateOperation', 'PartOperation'}

    # First pass: check if any root operations need MeshData
    needs_injection = []
    already_has_mesh = []
    for inst in doc.roots:
        if inst.class_name in _PART_OP_CLASSES:
            existing = inst.properties.get('MeshData')
            has_mesh = (
                existing is not None
                and isinstance(existing.value, (bytes, str))
                and len(existing.value) > 0
            )
            if has_mesh:
                already_has_mesh.append(inst)
            else:
                needs_injection.append(inst)

    # If ALL root operations already have MeshData, don't overwrite
    if already_has_mesh and not needs_injection:
        log.info(
            'All %d root PartOperations already have MeshData, skipping injection',
            len(already_has_mesh),
        )
        return

    # Inject into operations that need it
    injected = False
    for inst in needs_injection:
        inst.properties['MeshData'] = RbxProperty(
            name='MeshData',
            fmt=PropertyFormat.STRING,
            value=mesh_data,
        )
        log.info(
            'Injected MeshData (%d bytes) into %s',
            len(mesh_data),
            inst.class_name,
        )
        injected = True

    if not injected and not already_has_mesh:
        log.warning('Could not find any PartOperation instance to inject MeshData into')


# ---------------------------------------------------------------------------
# ChildData extraction (original + improved)
# ---------------------------------------------------------------------------


def _try_extract_child_data(doc: RbxDocument) -> RbxDocument | None:
    """If any root instance has a ChildData property with embedded RBXM, parse it."""
    for inst in doc.roots:
        child_prop = inst.properties.get('ChildData')
        if child_prop is not None and isinstance(child_prop.value, bytes):
            raw = child_prop.value
            if raw[:8] == b'<roblox!':
                log.debug('Found embedded RBXM in ChildData (%d bytes)', len(raw))
                return RbxmDeserializer().deserialize(raw)
    # Also check non-root instances
    for inst in doc.instances.values():
        child_prop = inst.properties.get('ChildData')
        if child_prop is not None and isinstance(child_prop.value, bytes):
            raw = child_prop.value
            if raw[:8] == b'<roblox!':
                log.debug('Found embedded RBXM in ChildData (%d bytes)', len(raw))
                return RbxmDeserializer().deserialize(raw)
    return None


def _get_child_data_bytes(raw_file: bytes) -> bytes | None:
    """Extract the raw ChildData RBXM bytes from the top-level document."""
    doc = RbxmDeserializer().deserialize(raw_file)
    for inst in doc.instances.values():
        child_prop = inst.properties.get('ChildData')
        if child_prop is not None and isinstance(child_prop.value, bytes):
            if child_prop.value[:8] == b'<roblox!':
                return child_prop.value
    return None
