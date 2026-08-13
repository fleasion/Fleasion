from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from fleasion.qml_api.repair import StartupRepairApi
from fleasion.utils import linux_proxy_helper


@pytest.mark.parametrize(
    ('code', 'dialog_kind'),
    [
        ('port_bind_failed', 'port'),
        ('hosts_write_exhausted', 'hosts'),
        ('linux_hosts_read_only', 'hosts'),
        ('linux_helper_unavailable', 'helper'),
        ('macos_helper_unavailable', 'helper'),
        ('macos_ca_patch_failed', 'helper'),
        ('macos_ca_trust_failed', 'certificate'),
        ('macos_relay_failed', 'helper'),
        ('roblox_ca_patch_failed', 'certificate'),
        ('tls_self_test_failed', 'tls'),
        ('windows_upstream_firewall', 'firewall'),
    ],
)
def test_supported_error_codes_have_distinct_repair_presentations(
    code: str,
    dialog_kind: str,
) -> None:
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    try:
        assert controller.report_error(code, {'error': 'diagnostic detail'})
        _wait_until(lambda: controller.active)
        assert controller.dialogKind == dialog_kind
        assert controller.title
        assert controller.summary
        assert controller.guidance
        assert controller.actions.property('count') > 0
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert predicate()


def test_known_startup_error_becomes_a_structured_request() -> None:
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    try:
        assert controller.report_error(
            'port_bind_failed',
            {
                'port': 443,
                'owners': [
                    {
                        'process_name': 'Web Shield',
                        'pid': 8080,
                        'local_address': '0.0.0.0:443',
                    }
                ],
                'bind_reason': 'address_in_use',
            },
        )
        _wait_until(lambda: controller.active)

        assert controller.dialogKind == 'port'
        assert controller.code == 'port_bind_failed'
        assert controller.requestPayload['port'] == 443
        assert controller.diagnostics.property('count') == 3
        assert controller.actions.property('count') == 2
        assert 'Web Shield' in controller.diagnostics.get(1)['value']
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


def test_read_only_linux_hosts_request_contains_copyable_nix_configuration() -> None:
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    try:
        assert controller.report_error(
            'linux_hosts_read_only',
            {'hosts': ['gamejoin.roblox.com', 'apis.roblox.com', 'apis.roblox.com']},
        )
        _wait_until(lambda: controller.active)

        assert controller.dialogKind == 'hosts'
        assert controller.snippet == (
            "networking.extraHosts =\n''\n"
            '  127.0.0.1 apis.roblox.com\n'
            "  127.0.0.1 gamejoin.roblox.com\n'';"
        )
        assert controller.supplementalTitle == 'Nix configuration'
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


def test_requests_are_queued_while_a_repair_dialog_is_active() -> None:
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    try:
        assert controller.report_error('hosts_write_exhausted', {'error': 'denied'})
        _wait_until(lambda: controller.active)
        assert controller.report_error('tls_self_test_failed', {'hosts': ['apis.roblox.com']})
        QCoreApplication.processEvents()
        assert controller.code == 'hosts_write_exhausted'

        controller.dismiss()
        _wait_until(lambda: controller.code == 'tls_self_test_failed')
        assert controller.dialogKind == 'tls'
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


def test_linux_helper_install_is_async_and_retries_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        linux_proxy_helper,
        'install_privileged_helper',
        lambda **_kwargs: {'ok': True, 'helper': '/usr/libexec/fleasion'},
    )
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    retry_spy = QSignalSpy(controller.retryRequested)
    try:
        assert controller.report_error('linux_helper_unavailable', {'code': 'not_installed'})
        _wait_until(lambda: controller.active)

        controller.performAction('install_linux_helper')
        _wait_until(lambda: not controller.task.property('busy') and retry_spy.count() == 1)

        assert not controller.active
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


def test_unknown_proxy_errors_remain_available_to_generic_runtime_handling() -> None:
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    try:
        assert not controller.report_error('unexpected_proxy_failure', {'error': 'boom'})
        QCoreApplication.processEvents()
        assert not controller.active
    finally:
        controller.shutdown()  # pyright: ignore[reportCallIssue]


class _AppControllerStub(QObject):
    @Slot(str)
    def copyText(self, _value: str) -> None:  # noqa: N802
        return


def test_startup_repair_coordinator_loads_the_matching_fluent_dialog() -> None:
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlEngine()
    engine.addImportPath(str(qml_root))
    component = QQmlComponent(engine)
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component.setData(
        b'''import QtQuick
import QtQuick.Controls
import "dialogs" as Dialogs

ApplicationWindow {
    id: rootWindow

    required property var repairController
    required property var appController
    width: 800
    height: 640
    visible: true

    Dialogs.StartupRepairCoordinator {
        anchors.fill: parent
        controller: rootWindow.repairController
        appController: rootWindow.appController
    }
}
''',
        QUrl.fromLocalFile(str(qml_root / 'StartupRepairTestHarness.qml')),
    )
    controller = StartupRepairApi()  # pyright: ignore[reportCallIssue]
    app_controller = _AppControllerStub()
    window = component.createWithInitialProperties(
        {'repairController': controller, 'appController': app_controller}
    )
    assert window is not None, '\n'.join(error.toString() for error in component.errors())
    try:
        assert controller.report_error('linux_hosts_read_only', {'hosts_path': '/etc/hosts'})
        loader = window.findChild(QObject, 'startupRepairLoader')
        assert loader is not None
        _wait_until(lambda: loader.property('item') is not None)
        assert not [warning for warning in warnings if '/src/fleasion/qml/' in warning]
        assert 'HostsRepairDialog' in loader.property('item').metaObject().className()
    finally:
        window.setProperty('visible', False)
        window.deleteLater()
        controller.shutdown()  # pyright: ignore[reportCallIssue]
        QCoreApplication.processEvents()
