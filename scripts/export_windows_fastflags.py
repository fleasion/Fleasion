"""Export Windows Player's disk-initialized FastVariable slots as JSON.

Usage: uv run python scripts/export_windows_fastflags.py --exe RobloxPlayerBeta.exe
    --output local-fastvariables.json [--fvariables FVariables.txt]

No original binary is modified, and no Hyperion code is decrypted/executed.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.modifications.fflag_windows_catalog import (
    WindowsStaticFlagCatalog,
    display_initial_value,
    parse_fvariables,
    scan_windows_player,
    selectable_qualified_name,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)


def catalog_to_export(catalog: WindowsStaticFlagCatalog) -> dict[str, object]:
    """Preserve raw storage and provenance; never label it a verified default."""
    return {
        'schema': 1,
        'source': str(catalog.source),
        'source_sha256': catalog.sha256,
        'type_hint_source': catalog.hint_source,
        'record_count': len(catalog.entries),
        'inferred_type_count': catalog.hinted,
        'runtime_defaults_verified': 0,
        'entries': [
            {
                'name': item.name,
                'value_rva': f'0x{item.value_rva:08X}',
                'raw_u64': f'0x{item.raw_u64:016X}',
                'qualified_hint': item.qualifier_hint,
                'scalar_type_hint': item.scalar_kind if item.scalar_kind != 'untyped' else None,
                'initial_value_hint': display_initial_value(item),
                'selectable_qualified_name': selectable_qualified_name(item),
            }
            for item in catalog.entries
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Export an immutable read-only scan and optional FVariables type hints."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True, help='Windows RobloxPlayerBeta.exe')
    parser.add_argument('--output', type=Path, required=True, help='Destination JSON file')
    parser.add_argument('--fvariables', type=Path, help='Optional FVariables.txt type-hint dump')
    args = parser.parse_args(argv)
    binary: Path = args.exe
    output: Path = args.output
    reference: Path | None = args.fvariables
    sources = (binary,) if reference is None else (binary, reference)
    if any(
        output.resolve() == source.resolve()
        or (output.exists() and source.exists() and output.samefile(source))
        for source in sources
    ):
        parser.error('Output path must not overwrite an input file')
    qualified: frozenset[str] = (
        parse_fvariables(reference.read_bytes()) if reference is not None else frozenset()
    )
    catalog = scan_windows_player(
        binary,
        qualified_type_hints=qualified,
        hint_source='fvariables' if qualified else 'none',
    )
    output.write_text(json.dumps(catalog_to_export(catalog), indent=2), encoding='utf-8')
    log.info('Saved %s records (%s type hints) to %s', len(catalog.entries), catalog.hinted, output)
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
