from pathlib import Path

from Fleasion.utils import platform_linux


def test_install_desktop_entries_writes_user_launcher_and_removes_deprecated(tmp_path, monkeypatch):
    applications = tmp_path / ".local" / "share" / "applications"
    bin_dir = tmp_path / ".local" / "bin"
    deprecated = applications / "fleasion-non-admin.desktop"
    deprecated.parent.mkdir(parents=True)
    deprecated.write_text("old", encoding="utf-8")

    monkeypatch.setattr(platform_linux, "USER_HOME", tmp_path)
    monkeypatch.setattr(platform_linux, "LINUX_APPLICATIONS_DIR", applications)
    install_dir = tmp_path / ".local" / "share" / "Fleasion"

    monkeypatch.setattr(platform_linux, "LINUX_BIN_DIR", bin_dir)
    monkeypatch.setattr(platform_linux, "LINUX_INSTALL_DIR", install_dir)
    monkeypatch.setattr(platform_linux, "LINUX_DESKTOP_ENTRY_PATH", applications / "fleasion.desktop")
    monkeypatch.setattr(platform_linux, "LINUX_LAUNCHER_PATH", bin_dir / "fleasion-launch")
    monkeypatch.setattr(platform_linux, "LINUX_INSTALLED_APP_PATH", install_dir / "Fleasion")
    monkeypatch.setattr(platform_linux, "LINUX_INSTALLED_ICON_PATH", install_dir / "fleasionlogoHR.ico")
    monkeypatch.setattr(platform_linux, "LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS", (deprecated,))
    monkeypatch.setattr(platform_linux, "_copy_linux_app_payload", lambda: (None, None))
    monkeypatch.setattr(platform_linux, "_linux_app_launch_command", lambda installed_app=None: (["/usr/bin/python3", "launcher.py"], Path("/opt/fleasion")))
    monkeypatch.setattr(platform_linux, "get_icon_path", lambda: None)
    monkeypatch.setattr(platform_linux.shutil, "which", lambda name: None)

    result = platform_linux.install_desktop_entries()

    desktop_text = (applications / "fleasion.desktop").read_text(encoding="utf-8")
    launcher_text = (bin_dir / "fleasion-launch").read_text(encoding="utf-8")

    assert "Name=Fleasion" in desktop_text
    assert f"Exec={bin_dir / 'fleasion-launch'}" in desktop_text
    assert "fleasion-non-admin" not in desktop_text
    assert "pkexec" not in launcher_text
    assert "FLEASION_USER_HOME" in launcher_text
    assert "exec /usr/bin/python3 launcher.py" in launcher_text
    assert not deprecated.exists()
    assert result["removed_deprecated_entries"] == [str(deprecated)]


def test_copy_linux_app_payload_copies_frozen_binary_and_icon(tmp_path, monkeypatch):
    source_binary = tmp_path / "Downloads" / "Fleasion-v2.2.0"
    source_binary.parent.mkdir()
    source_binary.write_bytes(b"binary")
    source_icon = tmp_path / "Downloads" / "fleasionlogoHR.ico"
    source_icon.write_bytes(b"icon")
    install_dir = tmp_path / ".local" / "share" / "Fleasion"

    monkeypatch.setattr(platform_linux.sys, "frozen", True, raising=False)
    monkeypatch.setattr(platform_linux.sys, "executable", str(source_binary))
    monkeypatch.setattr(platform_linux, "LINUX_INSTALL_DIR", install_dir)
    monkeypatch.setattr(platform_linux, "LINUX_INSTALLED_APP_PATH", install_dir / "Fleasion")
    monkeypatch.setattr(platform_linux, "LINUX_INSTALLED_ICON_PATH", install_dir / "fleasionlogoHR.ico")
    monkeypatch.setattr(platform_linux, "get_icon_path", lambda: source_icon)

    installed_app, installed_icon = platform_linux._copy_linux_app_payload()

    assert installed_app == install_dir / "Fleasion"
    assert installed_icon == install_dir / "fleasionlogoHR.ico"
    assert installed_app.read_bytes() == b"binary"
    assert installed_icon.read_bytes() == b"icon"
    assert installed_app.stat().st_mode & 0o111
