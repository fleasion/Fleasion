"""Fleasion - Roblox asset interceptor and replacer."""

__version__ = '2.1.1'
__all__ = ['main']


def __getattr__(name: str):
    if name == 'main':
        from .app import main
        return main
    raise AttributeError(name)
