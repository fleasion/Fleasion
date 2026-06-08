from Fleasion.app import _looks_like_macos_fleasion_command


def test_macos_fleasion_process_matching_accepts_real_launch_forms():
    assert _looks_like_macos_fleasion_command(
        "/Applications/Fleasion.app/Contents/MacOS/Fleasion-v2.1.0 --no-dashboard"
    )
    assert _looks_like_macos_fleasion_command("/project/.venv/bin/Fleasion")
    assert _looks_like_macos_fleasion_command("/usr/bin/python3 /project/launcher.py")
    assert _looks_like_macos_fleasion_command("/usr/bin/python3 -m Fleasion")


def test_macos_fleasion_process_matching_rejects_unrelated_commands():
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c tail '/Users/test/Library/Application Support/FleasionNT/logs/fleasion.log'"
    )
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c ps -axo command | rg 'Fleasion-v2.1.0|launcher.py'"
    )
    assert not _looks_like_macos_fleasion_command("/usr/bin/python3 /tmp/not-fleasion.py")
