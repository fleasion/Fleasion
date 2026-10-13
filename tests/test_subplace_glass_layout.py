"""Exercise Subplace theme/layout regressions in a fresh Qt process.

Qt keeps application-wide widget and stylesheet state between tests. The
visual checks repeatedly switch global styles, so running them after hundreds
of other GUI tests is both expensive and sensitive to unrelated Qt state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_subplace_glass_layout_in_fresh_qt_process() -> None:
    """Run all eight visual cases with their own QApplication lifecycle."""
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/_isolated/subplace_glass_cases.py', '-q'],
        cwd=project_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f'Isolated Subplace GUI regressions failed ({result.returncode}):'
        f'\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    )
    assert '8 passed' in result.stdout, result.stdout
