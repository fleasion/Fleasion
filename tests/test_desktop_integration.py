import plistlib
from pathlib import Path

from fleasion.utils import desktop_integration


def test_sync_desktop_integration_dispatches_windows_create_and_remove(monkeypatch):
    calls = []

    monkeypatch.setattr(desktop_integration.sys, "platform", "win32")
    monkeypatch.setattr(desktop_integration, "_create_windows_shortcut", lambda: calls.append("create") or True)
    monkeypatch.setattr(desktop_integration, "_remove_windows_shortcut", lambda: calls.append("remove") or True)

    assert desktop_integration.sync_desktop_integration(True)
    assert desktop_integration.sync_desktop_integration(False)
    assert calls == ["create", "remove"]


def test_sync_desktop_integration_delegates_linux_install_and_remove(tmp_path, monkeypatch):
    desktop_entry = tmp_path / "applications" / "fleasion.desktop"
    launcher = tmp_path / "bin" / "fleasion-launch"
    desktop_entry.parent.mkdir()
    launcher.parent.mkdir()
    desktop_entry.write_text("entry", encoding="utf-8")
    launcher.write_text("launcher", encoding="utf-8")
    calls = []

    monkeypatch.setattr(desktop_integration.sys, "platform", "linux")

    from fleasion.utils import platform_linux

    monkeypatch.setattr(platform_linux, "LINUX_DESKTOP_ENTRY_PATH", desktop_entry)
    monkeypatch.setattr(platform_linux, "LINUX_LAUNCHER_PATH", launcher)
    monkeypatch.setattr(platform_linux, "install_desktop_entries", lambda: calls.append("install") or {})

    assert desktop_integration.sync_desktop_integration(True)
    assert calls == ["install"]
    assert desktop_entry.exists()
    assert launcher.exists()

    assert desktop_integration.sync_desktop_integration(False)
    assert not desktop_entry.exists()
    assert not launcher.exists()


def test_macos_launcher_app_contains_metadata_icon_and_current_launch(tmp_path, monkeypatch):
    app_path = tmp_path / "Applications" / "Fleasion.app"
    icon = tmp_path / "fleasionlogoHR.icns"
    icon.write_bytes(b"icon")
    project = tmp_path / "Project"

    monkeypatch.setattr(desktop_integration.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_integration, "MACOS_APPLICATION_PATH", app_path)
    monkeypatch.setattr(desktop_integration, "get_icon_path", lambda: icon)
    monkeypatch.setattr(
        desktop_integration,
        "_launch_command",
        lambda: (
            ["/usr/bin/uv", "--project", str(project), "run", "fleasion"],
            project,
            {},
        ),
    )

    assert desktop_integration.sync_desktop_integration(True)

    info = plistlib.loads((app_path / "Contents" / "Info.plist").read_bytes())
    script = (app_path / "Contents" / "MacOS" / "Fleasion").read_text(encoding="utf-8")

    assert info["CFBundleDisplayName"] == "Fleasion"
    assert info["CFBundleIconFile"] == "fleasionlogoHR"
    assert info["NSHumanReadableCopyright"] == "Roblox asset interceptor and replacer"
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == [
        "roblox",
        "roblox-player",
    ]
    assert "exec /usr/bin/uv --project" in script
    assert "run fleasion" in script
    assert (app_path / "Contents" / "Resources" / "fleasionlogoHR.icns").read_bytes() == b"icon"
    assert (app_path / "Contents" / ".fleasion-managed-launcher").exists()


def test_macos_remove_only_deletes_managed_launcher(tmp_path, monkeypatch):
    app_path = tmp_path / "Applications" / "Fleasion.app"
    contents = app_path / "Contents"
    contents.mkdir(parents=True)
    (contents / ".fleasion-managed-launcher").write_text("managed", encoding="utf-8")

    monkeypatch.setattr(desktop_integration.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_integration, "MACOS_APPLICATION_PATH", app_path)

    assert desktop_integration.sync_desktop_integration(False)
    assert not app_path.exists()


def test_macos_create_refuses_to_overwrite_unmanaged_app(tmp_path, monkeypatch):
    app_path = tmp_path / "Applications" / "Fleasion.app"
    (app_path / "Contents").mkdir(parents=True)

    monkeypatch.setattr(desktop_integration.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_integration, "MACOS_APPLICATION_PATH", app_path)

    assert not desktop_integration.sync_desktop_integration(True)
    assert app_path.exists()
