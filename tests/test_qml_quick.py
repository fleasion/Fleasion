from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_qml_quick_regressions() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.setdefault('QT_QPA_PLATFORM', 'offscreen')
    script = (
        'import sys; '
        'from PySide6.QtQuickTest import QUICK_TEST_MAIN; '
        "sys.exit(QUICK_TEST_MAIN('fleasion_qml', sys.argv[1:], '.'))"
    )

    result = subprocess.run(
        [
            sys.executable,
            '-c',
            script,
            'fleasion_qml',
            '-input',
            'tests',
            '-import',
            'src/fleasion/qml',
            '-o',
            '-,txt',
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
