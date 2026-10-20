"""Fleasion - Roblox asset interceptor and replacer."""

# Must stay first: it has to run before anything imports PySide6
from .lazy_imports import make_imports_eager as make_imports_eager
from .version import read_version

__version__ = read_version()
