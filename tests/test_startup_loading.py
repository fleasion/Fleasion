from __future__ import annotations

import os
from collections.abc import Callable, Generator
from itertools import count
from pathlib import Path
from threading import Event, Timer, get_ident
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from fleasion.app import startup_tasks as startup_module
from fleasion.app.startup_tasks import StartupTasks
from fleasion.config import manager as config_module
from fleasion.config.manager import ConfigManager
from fleasion.gui import replacer_config as dashboard_module
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.utils import gui_work as work_module
from fleasion.utils.gui_work import GuiWork

_app: QApplication | None = None


@pytest.fixture
def app() -> QApplication:
    global _app
    instance = QApplication.instance()
    _app = cast('QApplication', instance) if instance is not None else QApplication([])
    return _app


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return ConfigManager()


class RecordingTab(QWidget):
    selected_account_changed = Signal(str)

    def __init__(self, *args: object, **kwargs: object) -> None:
        parent = kwargs.get('parent')
        super().__init__(parent if isinstance(parent, QWidget) else None)

    def set_selected_account(self, _name: str) -> None:
        pass

    def set_proxy_features_enabled(self, _enabled: bool) -> None:
        pass

    def build_ui(self) -> Generator[None]:
        yield


def _record_tabs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    created: list[str] = []
    for name in ('RandoStuffTab', 'SubplaceJoinerTab', 'ProxyTrafficTab', 'SettingsTab'):

        def create(*args: object, tab_name: str = name, **kwargs: object) -> RecordingTab:
            created.append(tab_name)
            return RecordingTab(*args, **kwargs)

        monkeypatch.setattr(dashboard_module, name, create)
    return created


def _window(config: ConfigManager) -> ReplacerConfigWindow:
    return cast('Callable[..., ReplacerConfigWindow]', ReplacerConfigWindow)(config)


def test_dashboard_paints_before_preloading_and_switching_never_constructs_tabs(
    app: QApplication,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _record_tabs(monkeypatch)
    window = _window(config)
    at_paint: list[list[str]] = []
    window.first_painted.connect(lambda: at_paint.append(list(created)))
    try:
        assert created == []
        assert window.tab_widget.count() == 5
        window.show()
        for _ in range(100):
            app.processEvents()
            if all(window.tab_widget.isTabEnabled(i) for i in range(5)):
                break
            QTest.qWait(5)
        assert at_paint == [[]]
        assert len(created) == 4
        assert all(window.tab_widget.isTabEnabled(i) for i in range(5))
        for index in range(5):
            window.tab_widget.setCurrentIndex(index)
            app.processEvents()
        assert len(created) == 4
    finally:
        window.close()


def test_close_before_paint_cancels_all_preload(
    app: QApplication,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _record_tabs(monkeypatch)
    window = _window(config)
    window.close()
    app.processEvents()
    assert created == []


def test_close_during_preload_cancels_remaining_pages(
    app: QApplication,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _record_tabs(monkeypatch)
    # Permit exactly one construction step per timer callback
    monkeypatch.setattr(work_module, 'perf_counter', count(0, 0.003).__next__)
    window = _window(config)
    window.setAttribute(dashboard_module.Qt.WidgetAttribute.WA_DeleteOnClose)

    def first_tab(*args: object, **kwargs: object) -> RecordingTab:
        created.append('first')
        QTimer.singleShot(0, window.close)
        return RecordingTab(*args, **kwargs)

    monkeypatch.setattr(dashboard_module, 'RandoStuffTab', first_tab)
    window.show()
    for _ in range(20):
        app.processEvents()
        QTest.qWait(5)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert created == ['first']


def test_gui_work_cancellation_closes_generator_before_destroying_widgets(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(work_module, 'perf_counter', count(0, 0.003).__next__)
    owner = QWidget()
    work = GuiWork(owner)
    events: list[str] = []

    def steps() -> Generator[None]:
        try:
            events.append('started')
            QTimer.singleShot(0, work.cancel)
            yield
            events.append('continued')
        finally:
            events.append('cleaned')

    work.start(steps())
    for _ in range(10):
        app.processEvents()
        QTest.qWait(5)
    assert events == ['started', 'cleaned']
    owner.deleteLater()
    app.processEvents()


def test_startup_probes_do_not_block_qt_and_shutdown_finishes_integration_writes(
    app: QApplication,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    written = Event()
    worker_ids: list[int] = []
    config.first_time_setup_complete = True
    config.desktop_integration = True

    def probe() -> list[str]:
        worker_ids.append(get_ident())
        entered.set()
        assert release.wait(3)
        return ['qt6-base']

    def sync(*, enabled: bool) -> bool:
        assert enabled
        written.set()
        return True

    monkeypatch.setattr(startup_module, 'linux_gui_dependency_packages', probe)
    monkeypatch.setattr(startup_module, 'find_roblox_dirs', lambda: [Path('/roblox')])
    monkeypatch.setattr(startup_module, 'sync_desktop_integration', sync)
    tasks = StartupTasks(config, autostart=False, parent=app)
    tasks.start()
    tasks.start()
    assert entered.wait(1)
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    app.processEvents()
    assert ticks == [True]
    assert worker_ids != [get_ident()]
    assert not written.is_set()
    timer = Timer(0.05, release.set)
    timer.start()
    try:
        tasks.shutdown()
        assert written.is_set()
        assert tasks.missing_packages == ['qt6-base']
        assert tasks.stopping
        assert len(worker_ids) == 1
    finally:
        release.set()
        timer.join()
        tasks.shutdown()


def test_shutdown_before_start_does_not_schedule_work(
    app: QApplication,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(startup_module, 'linux_gui_dependency_packages', lambda: calls.append(True))
    tasks = StartupTasks(config, autostart=False, parent=app)
    tasks.shutdown()
    tasks.start()
    app.processEvents()
    assert calls == []


def _skip_resolver(_self: object) -> None:
    pass


def test_cache_ui_construction_can_yield_to_pending_qt_events(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from fleasion.cache import cache_manager as cache_module
    from fleasion.cache.cache_viewer import CacheViewerTab

    monkeypatch.setattr(cache_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(CacheViewerTab, '_name_resolver_loop', _skip_resolver)
    errors: list[BaseException] = []

    def record_exception(_type: object, error: BaseException, _traceback: object) -> None:
        errors.append(error)

    monkeypatch.setattr(sys, 'excepthook', record_exception)
    tab = CacheViewerTab(cache_module.CacheManager(), defer_setup=True)
    try:
        for _ in tab.build_ui():
            app.processEvents()
        app.processEvents()
        for _ in range(100):
            if tab.initial_population_ready:
                break
            QTest.qWait(5)
        assert errors == []
        assert tab.preview_panel.isHidden()
        assert tab.initial_population_ready
    finally:
        tab.shutdown()
        tab.close()


def test_cache_population_yields_and_restores_table_state_on_cancel(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.cache import cache_manager as cache_module
    from fleasion.cache.cache_viewer import CacheViewerTab

    monkeypatch.setattr(cache_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(CacheViewerTab, '_name_resolver_loop', _skip_resolver)
    tab = CacheViewerTab(cache_module.CacheManager())
    populate = cast(
        'Callable[[list[dict[str, object]]], Generator[None]]',
        getattr(tab, '_populate_table_steps'),
    )
    rows: list[dict[str, object]] = [
        {'id': str(index), 'hash': f'hash{index}', 'type': 1, 'size': 10} for index in range(96)
    ]
    steps = populate(rows)
    try:
        next(steps)
        assert tab.table.rowCount() == 96
        assert tab.table.item(31, 1) is not None
        assert tab.table.item(32, 1) is None
        assert not tab.table.updatesEnabled()
        steps.close()
        assert tab.table.updatesEnabled()
        assert tab.table.isSortingEnabled()
        assert not tab.table.signalsBlocked()
    finally:
        steps.close()
        tab.shutdown()
        tab.close()
        app.processEvents()
