"""Modifications package — Fishstrap-style Mods + FastFlags for Fleasion."""

from .fflag_manager import FastFlagManager
from .fflag_profiles import FastFlagProfileManager
from .manager import ModificationManager

__all__ = ['ModificationManager', 'FastFlagManager', 'FastFlagProfileManager']  # ruff: ignore[unsorted-dunder-all]
