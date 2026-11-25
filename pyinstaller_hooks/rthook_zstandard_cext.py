from __future__ import annotations

import os

# The frozen package intentionally contains only zstandard's CPython extension
os.environ['PYTHON_ZSTANDARD_IMPORT_POLICY'] = 'cext'
