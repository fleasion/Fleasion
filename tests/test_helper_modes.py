from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from fleasion import helper_modes


def test_helper_module_has_no_qt_or_legacy_application_imports() -> None:
    script = """
import sys
import fleasion.helper_modes

assert 'fleasion.app' not in sys.modules
assert not any(name == 'PySide6' or name.startswith('PySide6.') for name in sys.modules)
"""
    subprocess.run([sys.executable, '-c', script], check=True)


@pytest.mark.parametrize(
    ('argv', 'handler_name', 'expected_args', 'expected_kwargs'),
    [
        (['--fleasion-gdk-debugger'], '_run_gdk_debugger', (), {}),
        (['--cleanup-hosts'], '_cleanup_hosts_once', (), {}),
        (
            ['--repair-autostart', '--fleasion-requesting-user-sid=S-1-5-21-42'],
            '_repair_autostart_once',
            ('S-1-5-21-42',),
            {'enabled': True},
        ),
        (
            ['--repair-roblox-permissions', '--fleasion-requesting-user-sid=S-1-5-21-42'],
            '_repair_roblox_permissions_once',
            ('S-1-5-21-42',),
            {},
        ),
        (['--repair-firewall'], '_repair_windows_firewall_once', (), {}),
    ],
)
def test_dispatch_routes_non_visual_helper_modes(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    handler_name: str,
    expected_args: tuple[object, ...],
    expected_kwargs: dict[str, object],
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def handler(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return 37

    monkeypatch.setattr(helper_modes, handler_name, handler)

    assert helper_modes.dispatch_helper_mode(argv) == 37
    assert calls == [(expected_args, expected_kwargs)]


def test_dispatch_can_disable_autostart_for_the_original_windows_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(
        helper_modes,
        '_repair_autostart_once',
        lambda sid, *, enabled: calls.append((sid, enabled)) or 0,
    )

    exit_code = helper_modes.dispatch_helper_mode(
        [
            '--repair-autostart',
            '--disable-autostart',
            '--fleasion-requesting-user-sid=S-1-5-21-42',
        ]
    )

    assert exit_code == 0
    assert calls == [('S-1-5-21-42', False)]


def test_dispatch_skips_visual_application_arguments() -> None:
    assert helper_modes.dispatch_helper_mode(['--no-dashboard', '--proxy-debug']) is None
    assert helper_modes.dispatch_helper_mode(['--disable-autostart']) is None


def test_linux_helper_installation_preserves_cli_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils import linux_proxy_helper

    calls: list[bool] = []
    monkeypatch.setattr(helper_modes.sys, 'platform', 'linux')
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_privileged_helper',
        lambda *, enable_promptless: calls.append(enable_promptless)
        or {
            'ok': True,
            'helper': '/usr/lib/fleasion/helper',
            'policy': '/usr/share/polkit-1/actions/fleasion.policy',
            'promptless_rule': '/etc/polkit-1/rules.d/fleasion.rules',
        },
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = helper_modes.dispatch_helper_mode(
        ['--install-linux-privileged-helper', '--linux-helper-promptless'],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [True]
    assert 'Installed Linux privileged helper: /usr/lib/fleasion/helper' in stdout.getvalue()
    assert 'Installed Polkit policy:' in stdout.getvalue()
    assert 'Installed promptless Polkit rule:' in stdout.getvalue()
    assert stderr.getvalue() == ''


def test_qml_runtime_import_graph_excludes_legacy_widget_presentation() -> None:
    script = """
import sys
import fleasion.qml_runtime

assert 'fleasion.app' not in sys.modules
assert 'PySide6.QtWidgets' in sys.modules
assert not any(name.startswith('fleasion.gui.') for name in sys.modules)
assert 'fleasion.tray' not in sys.modules
"""
    subprocess.run([sys.executable, '-c', script], check=True)


def test_windows_gdk_helper_development_command_uses_headless_entrypoint() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / 'src'
        / 'fleasion'
        / 'utils'
        / 'platform_windows.py'
    ).read_text(encoding='utf-8')

    assert "[sys.executable, '-m', 'fleasion.qml_runtime', _GDK_DEBUGGER_SWITCH]" in source
    assert "'-m', 'fleasion.app'" not in source

    completed = subprocess.run(
        [sys.executable, '-m', 'fleasion.qml_runtime', '--repair-autostart'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1


def test_proxy_animation_rig_detection_does_not_import_widget_viewer() -> None:
    script = r"""
import sys
from fleasion.utils.anim_converter import detect_player_rig, detect_rig

animation = b'''<roblox version="4">
<Item class="KeyframeSequence"><Properties />
<Item class="Keyframe"><Properties><float name="Time">0</float></Properties>
<Item class="Pose"><Properties>
<string name="Name">UpperTorso</string><float name="Weight">1</float>
</Properties></Item>
<Item class="Pose"><Properties>
<string name="Name">ToolHandle</string><float name="Weight">1</float>
</Properties></Item>
</Item></Item></roblox>'''

assert detect_rig(animation) == 'unknown'
assert detect_player_rig(animation) == 'R15'
assert 'fleasion.cache.animation_viewer' not in sys.modules
assert 'PySide6.QtWidgets' not in sys.modules
"""
    subprocess.run([sys.executable, '-c', script], check=True)


def test_proxy_runtime_has_no_legacy_json_viewer_back_channel() -> None:
    from fleasion.proxy import master

    assert master.__file__ is not None
    source = Path(master.__file__).read_text(encoding='utf-8')

    assert 'gui.json_viewer' not in source
    assert 'AssetFetcherThread' not in source
