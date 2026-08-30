"""Helpers for CDN response wrappers around raw Roblox document bytes."""

from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]


def strip_roblox_metadata(path: Path, content: bytes) -> bytes:
    """Drop CDN metadata prefixes from raw Roblox document replacements."""
    if path.suffix.lower() not in ('', '.bin'):  # ruff: ignore[literal-membership]
        return content
    roblox_start = content.find(b'<roblox')
    if roblox_start <= 0:
        return content
    return content[roblox_start:]
