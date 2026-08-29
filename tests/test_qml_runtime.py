from __future__ import annotations

import time
import tomllib
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtWidgets import QApplication

from fleasion.config import manager as manager_module
from fleasion import qml_runtime as runtime_module
from fleasion.qml_api.context import AppContext
from fleasion.qml_runtime import QmlRuntime

if TYPE_CHECKING:
    from collections.abc import Iterator


_PAGE_IDS = (
    'replacer',
    'cache',
    'modifications',
    'subplaces',
    'misc',
    'proxy',
    'logs',
    'settings',
)


def _class_name(value: QObject) -> str:
    name = value.metaObject().className()
    return name if isinstance(name, str) else bytes(name).decode('utf-8')


class _CacheStub:
    index: dict[str, Any] = {'assets': {}}

    @staticmethod
    def list_assets() -> list[dict[str, Any]]:
        return []

    @staticmethod
    def get_cache_stats() -> dict[str, int]:
        return {'total_assets': 0, 'total_size': 0}


class _ProxyStub:
    def __init__(self) -> None:
        self.cache_manager = _CacheStub()
        self.cache_scraper = None
        self.is_running = False
        self.auto_replace_rules = [
            {
                'enabled': False,
                'direction': 'response',
                'type': 'json_path',
                'match': 'data.name',
                'replacement': 'Fleasion',
                'host_filter': 'apis.roblox.com',
                'path_filter': '',
            }
        ]

    @staticmethod
    def get_env_proxy_traffic() -> list[dict[str, Any]]:
        return []

    @staticmethod
    def clear_env_proxy_traffic() -> None:
        return None

    def get_auto_replace_rules(self) -> list[dict[str, Any]]:
        return [dict(rule) for rule in self.auto_replace_rules]

    def set_auto_replace_rules(self, rules: list[dict[str, Any]]) -> None:
        self.auto_replace_rules = [dict(rule) for rule in rules]


@pytest.fixture
def qml_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppContext]:
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    context = AppContext(manager_module.ConfigManager(), _ProxyStub(), None)
    context._modifications._apply_catalog({'FFlagQmlSmokeTest': 'True'})
    try:
        yield context
    finally:
        context._replacer.shutdown()
        context._modifications.shutdown()
        context._subplaces.shutdown()
        context._utilities.shutdown()
        context._updates.shutdown()
        context._settings.shutdown()
        context._proxy.shutdown()
        context._cache.shutdown()
        context._logs.dispose()


def _wait_for_loader(
    application: QApplication,
    window: QObject,
    *,
    timeout: float = 3.0,
) -> QObject:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        page_id = str(window.property('currentPage'))
        loaders = window.findChildren(QObject, f'pageLoader-{page_id}')
        if loaders and loaders[0].property('item') is not None:
            return loaders[0]
        time.sleep(0.01)
    raise AssertionError('The page Loader did not become ready')


def _activate_lazy_loaders(
    application: QApplication,
    owner: QObject,
    *,
    timeout: float = 3.0,
) -> None:
    loaders = [
        child
        for child in owner.findChildren(QObject)
        if 'Loader' in _class_name(child)
        and not str(child.objectName()).startswith('pageLoader-')
        and child.property('asynchronous') is not True
    ]
    for loader in loaders:
        if loader.property('item') is not None:
            continue
        assert loader.setProperty('active', True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and loader.property('item') is None:
            application.processEvents()
            time.sleep(0.01)
        assert loader.property('item') is not None, (
            f'Loader {loader.objectName()!r} ({_class_name(loader)}) did not create its item; '
            f'source={loader.property("source")!r}, status={loader.property("status")!r}'
        )


def _project_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if '/src/fleasion/qml/' in warning]


def test_qml_application_loads_every_navigation_page(
    qml_context: AppContext,
):
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.addImportPath(str(qml_root))
    engine.rootContext().setContextProperty('App', qml_context)

    engine.load(QUrl.fromLocalFile(str(qml_root / 'Main.qml')))
    application.processEvents()

    roots = engine.rootObjects()
    assert len(roots) == 1, '\n'.join(warnings)
    window = roots[0]
    try:
        _activate_lazy_loaders(application, window)
        assert not _project_warnings(warnings), '\n'.join(warnings)
        for page_id in _PAGE_IDS:
            warnings.clear()
            assert window.setProperty('currentPage', page_id)
            loader = _wait_for_loader(application, window)
            loaded_page = loader.property('item')

            assert window.property('currentPage') == page_id
            assert loaded_page is not None
            assert page_id.casefold() in _class_name(loaded_page).casefold()
            _activate_lazy_loaders(application, loaded_page)
            assert not _project_warnings(warnings), '\n'.join(warnings)
    finally:
        window.setProperty('visible', False)
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()


def test_fluent_dialog_standard_buttons_dispatch_without_platform_delegates():
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_root))
    component = QQmlComponent(engine)
    component.setData(
        b"""import QtQuick
import QtQuick.Controls
import Fleasion.Components
ApplicationWindow {
    id: window
    visible: true
    property bool acceptedHit: false
    FluentDialog {
        id: dialog
        title: "Confirm"
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: window.acceptedHit = true
    }
    Component.onCompleted: {
        dialog.open()
        Qt.callLater(() => dialog.footer.standardButton(DialogButtonBox.Ok).click())
    }
}
""",
        QUrl(),
    )
    window = component.create()
    assert window is not None, '\n'.join(error.toString() for error in component.errors())
    try:
        for _ in range(5):
            application.processEvents()
        assert window.property('acceptedHit') is True
        assert (
            sum('FluentButton' in _class_name(child) for child in window.findChildren(QObject)) >= 2
        )
    finally:
        window.deleteLater()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()


def test_runtime_shutdown_restores_proxy_and_files_after_controller_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    def record(name: str):
        return lambda: calls.append(name)

    def fail_cache() -> None:
        calls.append('cache.fail')
        raise PermissionError('preview file is busy')

    runtime = QmlRuntime.__new__(QmlRuntime)
    QObject.__init__(runtime)
    runtime._shutting_down = False
    runtime._monitor = SimpleNamespace(stop=record('monitor.stop'))
    runtime._control_server = SimpleNamespace(close=record('server.close'))
    runtime._config_watcher = SimpleNamespace(stop=record('watcher.stop'))
    runtime._context = SimpleNamespace(
        _replacer=SimpleNamespace(shutdown=record('replacer.stop')),
        _logs=SimpleNamespace(dispose=record('logs.stop')),
        _cache=SimpleNamespace(shutdown=fail_cache),
        _modifications=SimpleNamespace(shutdown=record('modifications.controller.stop')),
        _proxy=SimpleNamespace(shutdown=record('proxy.controller.stop')),
        _settings=SimpleNamespace(shutdown=record('settings.stop')),
        _subplaces=SimpleNamespace(shutdown=record('subplaces.stop')),
        _utilities=SimpleNamespace(shutdown=record('utilities.stop')),
        _updates=SimpleNamespace(shutdown=record('updates.stop')),
        _startup_repair=SimpleNamespace(shutdown=record('repair.stop')),
    )
    runtime._startup_reapply_thread = None
    runtime._manual_upstream_credentials_timer = SimpleNamespace(stop=record('upstream.timer.stop'))
    runtime._permission_denied_timer = SimpleNamespace(stop=record('permission.timer.stop'))
    runtime._config = SimpleNamespace(
        proxy_mode='direct',
        close_env_proxy_roblox_on_exit=False,
    )
    runtime._lifecycle = SimpleNamespace(cancel=record('lifecycle.cancel'))
    runtime._modifications = SimpleNamespace(
        cancel_pending_operations=record('modifications.cancel'),
        clear_managed_file_read_only=record('modifications.unlock'),
        restore_all=record('modifications.restore'),
    )
    runtime._proxy_lifecycle_lock = threading.RLock()
    runtime._proxy = SimpleNamespace(stop=record('proxy.stop'))
    monkeypatch.setattr(
        runtime_module,
        'QLocalServer',
        SimpleNamespace(removeServer=lambda _name: calls.append('server.remove')),
    )
    monkeypatch.setattr(
        runtime_module.time_tracker, 'save', lambda _config: calls.append('time.save')
    )
    monkeypatch.setattr(runtime_module.log_buffer, 'log', lambda *_args: None)

    runtime.shutdown()

    assert 'cache.fail' in calls
    assert calls.index('modifications.cancel') < calls.index('modifications.restore')
    assert 'proxy.stop' in calls
    assert 'modifications.restore' in calls
    assert 'time.save' in calls


def test_roblox_uri_argument_is_validated_and_forwarded_to_running_instance(
    monkeypatch: pytest.MonkeyPatch,
):
    writes: list[bytes] = []

    class SocketStub:
        def connectToServer(self, _name: str) -> None:  # noqa: N802
            return None

        def waitForConnected(self, _timeout: int) -> bool:  # noqa: N802
            return True

        def write(self, value: bytes) -> None:
            writes.append(value)

        def flush(self) -> None:
            return None

        def waitForBytesWritten(self, _timeout: int) -> bool:  # noqa: N802
            return True

        def disconnectFromServer(self) -> None:  # noqa: N802
            return None

    monkeypatch.setattr(runtime_module, 'QLocalSocket', SocketStub)
    target = 'roblox-player:1+launchmode:play+gameinfo:ticket'

    assert runtime_module._roblox_uri_from_argv(['--no-dashboard', target]) == target
    assert runtime_module._running_instance_available(
        show_dashboard=False,
        roblox_uri=target,
    )
    assert writes == [f'launch-roblox\n{target}'.encode()]
    assert runtime_module._roblox_uri_from_argv(['https://example.com']) is None
    assert runtime_module._normalized_roblox_uri('roblox:\nshow') is None


def test_runtime_preserves_linux_roblox_uri_for_selected_client(
    monkeypatch: pytest.MonkeyPatch,
):
    launches: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._shutting_down = False
    runtime._config = SimpleNamespace(proxy_mode='env', proxy_features_enabled=True)
    monkeypatch.setattr(runtime_module.sys, 'platform', 'linux')
    monkeypatch.setattr(
        runtime_module,
        'launch_as_standard_user',
        lambda target: launches.append(target) or True,
    )

    runtime._launch_roblox_uri('roblox:example')

    assert launches == ['roblox:example']


def test_roblox_file_open_events_queue_until_runtime_is_ready():
    event_filter = runtime_module.RobloxUrlEventFilter()
    received: list[str] = []
    event_filter.robloxUriReceived.connect(received.append)
    event = SimpleNamespace(
        type=lambda: QEvent.Type.FileOpen,
        url=lambda: QUrl('roblox:queued'),
    )

    assert event_filter.eventFilter(QObject(), event)
    assert received == []
    event_filter.start()
    assert received == ['roblox:queued']


def test_qml_module_files_are_packaged_under_the_python_package():
    repository_root = Path(__file__).resolve().parents[1]
    spec_text = (repository_root / 'Fleasion.spec').read_text(encoding='utf-8')
    runtime_text = (repository_root / 'src' / 'fleasion' / 'qml_runtime.py').read_text(
        encoding='utf-8'
    )

    assert "('src/fleasion/qml', 'fleasion/qml')" in spec_text
    assert "'PySide6.QtQml'" in spec_text
    assert "'PySide6.QtQuick'" in spec_text
    assert "'PySide6.QtQuick3D'" in spec_text
    assert "'PySide6.QtQuickControls2'" in spec_text
    assert "'PySide6.QtWidgets'" in spec_text
    assert "'PySide6.QtMultimedia'" in spec_text
    assert "'Qt.labs.platform'" in spec_text
    assert "'QtQuick.Controls.FluentWinUI3'" in spec_text
    assert "'QtQuick.Controls.Fusion'" in spec_text
    assert "'QtQuick3D'" in spec_text
    assert "'QtMultimedia'" in spec_text
    assert "Path(__file__).with_name('qml')" in runtime_text
    assert 'app = QApplication(sys.argv)' in runtime_text
    assert "_SINGLE_INSTANCE_KEY: Final = 'FleasionSingleInstance'" in runtime_text
    assert "_SINGLE_INSTANCE_SERVER: Final = 'FleasionSingleInstanceControl'" in runtime_text
    assert 'self._startup_reapply_thread = run_in_thread' in runtime_text
    assert 'startup_reapply.join(timeout=10.0)' in runtime_text


def test_pyside_project_tracks_every_qml_and_registered_python_source():
    repository_root = Path(__file__).resolve().parents[1]
    project_data = tomllib.loads((repository_root / 'pyproject.toml').read_text(encoding='utf-8'))
    configured = set(project_data['tool']['pyside6-project']['files'])
    qml_root = repository_root / 'src' / 'fleasion' / 'qml'
    api_root = repository_root / 'src' / 'fleasion' / 'qml_api'
    expected = {path.relative_to(repository_root).as_posix() for path in qml_root.rglob('*.qml')}
    expected.update(
        path.relative_to(repository_root).as_posix()
        for path in api_root.glob('*.py')
        if '@QmlElement' in path.read_text(encoding='utf-8')
    )

    assert 'launcher.py' in configured
    assert expected <= configured


def test_pyinstaller_qml_allowlist_covers_import_scanner_modules():
    repository_root = Path(__file__).resolve().parents[1]
    qml_root = repository_root / 'src' / 'fleasion' / 'qml'
    completed = subprocess.run(
        [
            'pyside6-qmlimportscanner',
            '-rootPath',
            str(qml_root),
            '-importPath',
            str(qml_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    imports = {
        entry['name']
        for entry in json.loads(completed.stdout)
        if entry['type'] == 'module' and not entry['name'].startswith('Fleasion.')
    }
    spec_text = (repository_root / 'Fleasion.spec').read_text(encoding='utf-8')

    assert imports <= {
        'Qt.labs.platform',
        'QtMultimedia',
        'QtQml',
        'QtQml.Models',
        'QtQuick',
        'QtQuick.Controls',
        'QtQuick.Controls.Basic',
        'QtQuick.Dialogs',
        'QtQuick.Layouts',
        'QtQuick3D',
    }
    for module_name in imports:
        assert f"'{module_name}'" in spec_text
