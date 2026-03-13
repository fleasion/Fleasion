"""opus-better-cook: Roblox SolidModel binary deserializer and converter."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .converter import convert_file


def main() -> None:
    """CLI entry point for converting SolidModel .bin files to .rbxm / .rbxmx."""
    parser = argparse.ArgumentParser(
        prog='opus-better-cook',
        description='Deserialize Roblox SolidModel .bin files to .rbxm or .rbxmx',
    )
    parser.add_argument(
        'input',
        type=Path,
        help='Path to the input .bin or .rbxm file',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=None,
        help='Output file path (.rbxm or .rbxmx). Defaults to <input>.rbxmx',
    )
    parser.add_argument(
        '-f',
        '--format',
        choices=['rbxm', 'rbxmx', 'obj'],
        default=None,
        help='Output format (inferred from --output extension if not set)',
    )
    parser.add_argument(
        '--extract-child-data',
        action='store_true',
        help=(
            'Extract and convert the embedded ChildData RBXM '
            'instead of the outer PartOperationAsset wrapper'
        ),
    )
    parser.add_argument(
        '--export-obj',
        action='store_true',
        help='Export the CSG MeshData as a Wavefront OBJ file',
    )
    parser.add_argument(
        '--decompose',
        action='store_true',
        help=(
            'When exporting OBJ, decompose into per-operation meshes '
            '(Union + NegativeParts) instead of the pre-computed final result'
        ),
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable verbose logging',
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s: %(message)s',
    )

    logger = logging.getLogger(__name__)

    input_path: Path = args.input
    if not input_path.is_file():
        logger.error('Input file not found: %s', input_path)
        sys.exit(1)

    output_path: Path | None = args.output
    fmt: str | None = args.format
    export_obj_mode: bool = args.export_obj

    # If --export-obj is set, default format to obj
    if export_obj_mode and fmt is None:
        fmt = 'obj'

    if output_path is None:
        ext = f'.{fmt}' if fmt else '.rbxmx'
        output_path = input_path.with_suffix(ext)
    elif fmt is not None and not output_path.suffix:
        output_path = output_path.with_suffix(f'.{fmt}')

    convert_file(
        input_path,
        output_path,
        extract_child_data=args.extract_child_data,
        export_obj_mode=export_obj_mode,
        decompose=args.decompose,
    )
