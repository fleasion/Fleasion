"""opus-better-cook: Roblox SolidModel binary deserializer and converter."""

from __future__ import annotations

from .cli import main
from .converter import convert_file

__all__ = ['convert_file', 'main']
