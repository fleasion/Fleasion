"""DDS / Roblox .tex preview converter.

Converts DDS DXT1 `.tex` files (as used by Roblox skyboxes) to PNG bytes
for display in the Modifications preview panel.
"""

from __future__ import annotations

import io


def tex_to_png_bytes(data: bytes) -> bytes | None:
    """Convert a Roblox .tex (DDS DXT1) file to PNG bytes for preview.

    Strips any non-standard header prefix before the DDS magic if present,
    then hands off to Pillow's built-in DDS loader.

    Returns PNG bytes or ``None`` on failure.
    """
    from PIL import Image

    # DDS magic: b'DDS ' (0x44445320)
    DDS_MAGIC = b'DDS '

    working = data

    # Find the DDS magic and strip everything before it.
    idx = working.find(DDS_MAGIC)
    if idx > 0:
        working = working[idx:]
    elif idx < 0:
        # No DDS magic at all — not a format we can handle
        return None

    try:
        img = Image.open(io.BytesIO(working))
        buf = io.BytesIO()
        img.convert('RGBA').save(buf, format='PNG')
        return buf.getvalue()
    except Exception:
        return None
