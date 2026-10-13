"""Run shared Glass section-color regressions with fresh QApplication state."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_glass_section_colors_in_fresh_qt_process() -> None:
    """Avoid global stylesheet state left by unrelated GUI tests."""
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/_isolated/glass_section_colors_cases.py', '-q'],
        cwd=project_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f'Isolated Glass section-color regressions failed ({result.returncode}):'
        f'\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    )
    assert '3 passed' in result.stdout, result.stdout
