"""Fleasion - Roblox asset interceptor and replacer."""

from importlib import import_module
from typing import TYPE_CHECKING

from .version import read_version

if TYPE_CHECKING:
    from .app import main as main

__all__ = ['main']
__version__ = read_version()


def __getattr__(name: str) -> object:
    if name == 'main':
        return import_module('.app', __name__).main
    raise AttributeError(name)
