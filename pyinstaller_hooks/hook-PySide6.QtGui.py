# pyright: standard

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# The GTK theme plugin pulls host GTK and a second ICU runtime into Linux builds
binaries = [entry for entry in binaries if Path(entry[0]).name != 'libqgtk3.so']
