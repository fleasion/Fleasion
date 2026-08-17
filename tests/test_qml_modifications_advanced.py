from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtQml import QQmlComponent, QQmlEngine

from fleasion.modifications.fflag_profiles import FastFlagProfileManager
from fleasion.modifications.hotkeys import linux as linux_hotkeys
from fleasion.modifications.stash_paths import resource_stash_dir
from fleasion.qml_api import modification_hotkeys as hotkey_bridge
from fleasion.qml_api import modification_inspector as inspector_module
from fleasion.qml_api.mesh_geometry import MeshGeometry
from fleasion.qml_api.modification_hotkeys import CustomFastFlagHotkeys
from fleasion.qml_api.modification_inspector import ModificationInspector
from fleasion.qml_api.modifications import ModificationsApi


_EARLY_MESH = (
    b'version 1.00\n'
    b'1\n'
    b'[0,0,0][0,1,0][0,0,0]'
    b'[1,0,0][0,1,0][1,0,0]'
    b'[0,1,0][0,1,0][0,1,0]'
)


class _ManagerStub(QObject):
    entry_status_changed = Signal(str, str, str)
    apply_finished = Signal(str)
    restore_finished = Signal()

    def __init__(self, roblox_dirs: list[Path], stash_dir: Path) -> None:
        super().__init__()
        self.roblox_dirs = roblox_dirs
        self._stash_dir = stash_dir
        self.entries: list[dict[str, Any]] = []
        self.fast_flags: dict[str, Any] = {}
        self.fast_flags_enabled = False
        self.framerate_cap = 0
        self.restored_targets: list[str] = []

    def restore_orphaned_stash(self, target_path: str) -> bool:
        self.restored_targets.append(target_path)
        return True


class _HotkeyService(QObject):
    key_pressed = Signal(int, int)
    key_released = Signal(int)
    wheel_scrolled = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.last_error = ''

    def begin_capture(self) -> bool:
        return True


class _HotkeyController(QObject):
    toggled = Signal(str)

    def __init__(self, *_args: object) -> None:
        super().__init__()
        self.service = _HotkeyService()
        self.sync_count = 0

    def sync(self) -> None:
        self.sync_count += 1

    def stop(self) -> None:
        return None


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        custom_fflags={'FFlagExample': 'True'},
        custom_fflags_enabled=True,
        custom_fflag_disabled=[],
        custom_fflag_keybinds={},
    )


def test_orphan_model_distinguishes_restore_remove_and_mixed_recovery(
    tmp_path: Path,
) -> None:
    first = tmp_path / 'RobloxOne'
    second = tmp_path / 'RobloxTwo'
    first.mkdir()
    second.mkdir()
    stash_root = tmp_path / 'stash'
    target = Path('content') / 'textures' / 'custom.png'
    backup = resource_stash_dir(stash_root, first) / target
    marker = resource_stash_dir(stash_root, second) / target
    backup.parent.mkdir(parents=True)
    marker.parent.mkdir(parents=True)
    backup.write_bytes(b'original')
    marker.with_name(f'{marker.name}.fleasion_new').touch()
    manager = _ManagerStub([first, second], stash_root)
    controller = ModificationsApi(  # pyright: ignore[reportCallIssue]
        manager,  # pyright: ignore[reportArgumentType]
        profile_manager=FastFlagProfileManager(tmp_path / 'profiles'),
    )

    row = controller.orphanedModel.get(0)  # type: ignore[attr-defined]

    assert row == {
        'name': 'custom.png',
        'targetPath': target.as_posix(),
        'installationCount': 2,
        'backupCount': 1,
        'createdCount': 1,
        'sizeText': '8 B',
        'kind': 'mixed',
    }
    assert controller.restoreOrphanedStash(target.as_posix())
    assert manager.restored_targets == [target.as_posix()]
    controller.shutdown()


def test_modification_inspector_preserves_empty_files_and_exports_original(
    tmp_path: Path,
) -> None:
    roblox_dir = tmp_path / 'Roblox'
    stash_root = tmp_path / 'stash'
    target = Path('content') / 'empty.txt'
    current = roblox_dir / target
    original = resource_stash_dir(stash_root, roblox_dir) / target
    current.parent.mkdir(parents=True)
    original.parent.mkdir(parents=True)
    current.write_bytes(b'')
    original.write_bytes(b'original text')
    manager = _ManagerStub([roblox_dir], stash_root)
    manager.entries = [{'target_path': target.as_posix()}]
    inspector = ModificationInspector(manager)  # pyright: ignore[reportArgumentType]

    inspector.inspect(target.as_posix(), 'Empty text')
    info = cast('dict[str, object]', inspector.property('info'))

    assert info['replacementAvailable'] is True
    assert info['replacementSize'] == '0 B'
    assert info['originalAvailable'] is True
    destination = tmp_path / 'original.txt'
    assert inspector.exportFile('original', str(destination))
    assert destination.read_bytes() == b'original text'
    inspector.shutdown()


def test_modification_inspector_does_not_present_created_override_as_original(
    tmp_path: Path,
) -> None:
    roblox_dir = tmp_path / 'Roblox'
    stash_root = tmp_path / 'stash'
    target = Path('content') / 'created.bin'
    current = roblox_dir / target
    marker = resource_stash_dir(stash_root, roblox_dir) / target
    current.parent.mkdir(parents=True)
    marker.parent.mkdir(parents=True)
    current.write_bytes(b'override')
    marker.with_name(f'{marker.name}.fleasion_new').touch()
    manager = _ManagerStub([roblox_dir], stash_root)
    inspector = ModificationInspector(manager)  # pyright: ignore[reportArgumentType]

    inspector.inspect(target.as_posix(), 'Created override')
    info = cast('dict[str, object]', inspector.property('info'))

    assert info['replacementAvailable'] is True
    assert info['originalAvailable'] is False
    inspector.shutdown()


def test_modification_inspector_materializes_audio_and_exports_converted_texture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roblox_dir = tmp_path / 'Roblox'
    stash_root = tmp_path / 'stash'
    manager = _ManagerStub([roblox_dir], stash_root)
    audio_target = Path('content') / 'sound.ogg'
    audio = roblox_dir / audio_target
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'OggS preview')
    inspector = ModificationInspector(manager)  # pyright: ignore[reportArgumentType]

    inspector.inspect(audio_target.as_posix(), 'Sound')
    audio_info = cast('dict[str, object]', inspector.property('info'))

    assert str(audio_info['replacementPreviewUrl']).startswith('file:')

    texture_target = Path('content') / 'texture.ktx'
    texture = roblox_dir / texture_target
    texture.write_bytes(b'ktx payload')
    monkeypatch.setattr(inspector_module, 'ktx_to_png', lambda _data: b'png payload')
    inspector.inspect(texture_target.as_posix(), 'Texture')
    texture_info = cast('dict[str, object]', inspector.property('info'))

    assert texture_info['convertedAvailable'] is True
    assert texture_info['convertedSuffix'] == '.png'
    destination = tmp_path / 'converted'
    assert inspector.exportFile('converted', str(destination))
    assert destination.with_suffix('.png').read_bytes() == b'png payload'
    inspector.shutdown()


def test_modification_inspector_releases_both_mesh_geometries_and_keeps_obj_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGeometry(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.deleted = False
            self.payload = b''
            created.append(self)

        def load(self, payload: bytes) -> bool:
            self.payload = payload
            return True

        def deleteLater(self) -> None:  # noqa: N802
            self.deleted = True

    created: list[FakeGeometry] = []

    monkeypatch.setattr(inspector_module, 'MeshGeometry', FakeGeometry)
    roblox_dir = tmp_path / 'Roblox'
    stash_root = tmp_path / 'stash'
    mesh_target = Path('content') / 'model.mesh'
    current = roblox_dir / mesh_target
    original = resource_stash_dir(stash_root, roblox_dir) / mesh_target
    current.parent.mkdir(parents=True)
    original.parent.mkdir(parents=True)
    current.write_bytes(_EARLY_MESH)
    original.write_bytes(_EARLY_MESH)
    manager = _ManagerStub([roblox_dir], stash_root)
    manager.entries = [{'target_path': mesh_target.as_posix()}]
    inspector = ModificationInspector(manager)  # pyright: ignore[reportArgumentType]

    inspector.inspect(mesh_target.as_posix(), 'Model')
    info = cast('dict[str, object]', inspector.property('info'))
    replacement = inspector.replacementMeshGeometry
    original_geometry = inspector.originalMeshGeometry

    assert isinstance(replacement, FakeGeometry)
    assert isinstance(original_geometry, FakeGeometry)
    assert replacement.parent() is inspector
    assert original_geometry.parent() is inspector
    assert info['replacementMeshAvailable'] is True
    assert info['originalMeshAvailable'] is True
    assert info['convertedSuffix'] == '.obj'
    destination = tmp_path / 'mesh-export'
    assert inspector.exportFile('converted', str(destination))
    assert destination.with_suffix('.obj').read_bytes().startswith(b'# Converted')

    text_target = Path('content') / 'notes.txt'
    text_path = roblox_dir / text_target
    text_path.write_text('notes', encoding='utf-8')
    inspector.inspect(text_target.as_posix(), 'Notes')

    assert inspector.replacementMeshGeometry is None
    assert inspector.originalMeshGeometry is None
    assert replacement.deleted
    assert original_geometry.deleted
    assert replacement.parent() is None
    assert original_geometry.parent() is None

    inspector.inspect(mesh_target.as_posix(), 'Model')
    active = (inspector.replacementMeshGeometry, inspector.originalMeshGeometry)
    inspector.shutdown()

    assert len(created) == 4
    assert all(isinstance(geometry, FakeGeometry) and geometry.deleted for geometry in active)
    assert all(geometry.parent() is None for geometry in active if geometry is not None)


def test_modification_mesh_preview_loads_in_real_qml_engine() -> None:
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlEngine()
    engine.addImportPath(str(qml_root))
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(
            str(qml_root / 'screens' / 'modifications' / 'ModificationMeshPreview.qml')
        ),
    )
    geometry = MeshGeometry()  # pyright: ignore[reportCallIssue]
    assert geometry.load(_EARLY_MESH)

    preview = component.createWithInitialProperties(
        {'geometry': geometry, 'accessibleName': 'Test mesh preview'}
    )

    assert preview is not None, component.errorString()
    assert preview.setProperty('orbitYaw', 90.0)
    assert preview.setProperty('cameraDistance', 7.0)
    reset_method = preview.metaObject().method(preview.metaObject().indexOfMethod('resetView()'))
    assert reset_method.invoke(preview)
    assert preview.property('orbitYaw') == 25.0
    assert preview.property('cameraDistance') == 4.5

    preview.deleteLater()
    geometry.deleteLater()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_linux_capture_supports_bare_modifiers_and_does_not_import_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hotkey_bridge.sys, 'platform', 'linux')
    monkeypatch.setattr(
        linux_hotkeys,
        'LinuxCustomFFlagHotkeyController',
        _HotkeyController,
    )
    config = _config()
    bridge = CustomFastFlagHotkeys(config, None)

    assert bridge.begin_capture('FFlagExample')
    bridge._controller.service.key_pressed.emit(29, linux_hotkeys.MOD_CTRL)  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
    assert bridge.capture_busy
    bridge._controller.service.key_released.emit(29)  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]

    assert config.custom_fflag_keybinds == {
        'FFlagExample': {
            'platform': 'linux_evdev',
            'scan_code': 29,
            'modifiers': 0,
        }
    }
    assert 'fleasion.gui' not in Path(hotkey_bridge.__file__).read_text(encoding='utf-8')
    bridge.shutdown()


def test_windows_qml_capture_uses_qt_key_and_preserves_extended_scan_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.modifications.hotkeys import windows as windows_hotkeys

    monkeypatch.setattr(hotkey_bridge.sys, 'platform', 'win32')
    monkeypatch.setattr(
        windows_hotkeys,
        'WindowsCustomFFlagHotkeyController',
        _HotkeyController,
    )
    config = _config()
    bridge = CustomFastFlagHotkeys(config, None)
    control_key = int(Qt.Key.Key_Control.value)

    assert bridge.begin_capture('FFlagExample')
    assert bridge.capture_native_key(0x11D, control_key, 0x04000000)
    assert bridge.release_native_key(0x11D, control_key)

    assert config.custom_fflag_keybinds == {
        'FFlagExample': {
            'scan_code': 0x1D,
            'extended': True,
            'modifiers': 0,
        }
    }
    bridge.shutdown()
