"""Modifications package — Fishstrap-style Mods + FastFlags for Fleasion."""

from .fflag_catalog import FastFlagCatalog
from .fflag_manager import FastFlagManager
from .fflag_profiles import FastFlagProfileManager
from .catalog import ModificationCatalogEntry, built_in_modifications
from .manager import ModificationManager

__all__ = [
    'ModificationManager',
    'FastFlagCatalog',
    'FastFlagManager',
    'FastFlagProfileManager',
    'ModificationCatalogEntry',
    'built_in_modifications',
]
