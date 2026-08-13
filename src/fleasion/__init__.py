"""Fleasion - Roblox asset interceptor and replacer."""

from .version import read_version

__all__ = ['main']  # type: ignore[reportUnsupportedDunderAll]
__version__ = read_version()


def __getattr__(name: str):
    if name == 'main':
        from .qml_runtime import main

        return main
    raise AttributeError(name)
