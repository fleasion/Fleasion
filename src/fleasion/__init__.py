"""Fleasion - Roblox asset interceptor and replacer."""

from typing import TYPE_CHECKING

from .version import read_version

if TYPE_CHECKING:
    from .app import main as main

__all__ = ['main']
__version__ = read_version()


def __getattr__(name: str) -> object:
    if name == 'main':
        # ruff: ignore[import-outside-top-level] - Keep the Qt application import lazy
        from .app import main

        return main
    raise AttributeError(name)
