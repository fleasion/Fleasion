#!/usr/bin/env python3
"""Reset Fleasion's first-time setup prompt flag for local testing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _default_settings_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / 'src'
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from fleasion.utils.paths import CONFIG_FILE

    return Path(CONFIG_FILE)


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}

    with path.open('r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f'{path} must contain a JSON object')

    return data


def reset_first_time_setup(path: Path) -> None:
    settings = _load_settings(path)
    settings['first_time_setup_complete'] = False

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)
        f.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Reset Fleasion so the first-time setup guide appears on next launch.'
    )
    parser.add_argument(
        '--settings',
        type=Path,
        default=_default_settings_path(),
        help='Path to settings.json. Defaults to the normal Fleasion save file.',
    )
    args = parser.parse_args()

    try:
        reset_first_time_setup(args.settings.expanduser())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f'Failed to reset first-time setup flag: {exc}', file=sys.stderr)
        return 1

    print(f'Reset first_time_setup_complete=false in {args.settings.expanduser()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
