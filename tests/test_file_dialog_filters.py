from pathlib import Path


def test_all_files_filters_match_extensionless_files():
    project_root = Path(__file__).resolve().parents[1]
    dotted_filter = "All Files (" + "*.*" + ")"
    offenders = []

    for path in (project_root / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if dotted_filter in text:
            offenders.append(path.relative_to(project_root).as_posix())

    assert offenders == []
