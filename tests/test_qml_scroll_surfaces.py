from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest


def _qml_component(source: bytes) -> tuple[QQmlEngine, QQmlComponent, Any]:
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    engine = QQmlEngine()
    engine.addImportPath(str(qml_root))
    component = QQmlComponent(engine)
    component.setData(source, QUrl())
    instance = component.create()
    assert instance is not None, '\n'.join(error.toString() for error in component.errors())
    QCoreApplication.processEvents()
    return engine, component, instance


def _dispose_qml(engine: QQmlEngine, instance: Any) -> None:
    instance.deleteLater()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def test_user_facing_qml_uses_fluent_scroll_surfaces() -> None:
    qml_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion' / 'qml'
    implementation_files = {
        qml_root / 'Fleasion' / 'Components' / 'FluentScrollBar.qml',
        qml_root / 'Fleasion' / 'Components' / 'FluentScrollView.qml',
    }
    raw_scroll_view = re.compile(r'\bScrollView\s*\{')
    raw_scroll_bar = re.compile(
        r'ScrollBar\.(?:horizontal|vertical)\s*:\s*ScrollBar\s*\{'
    )
    raw_policy = re.compile(r'ScrollBar\.(?:horizontal|vertical)\.policy')
    violations: list[str] = []

    for qml_file in qml_root.rglob('*.qml'):
        if qml_file in implementation_files:
            continue
        source = qml_file.read_text(encoding='utf-8')
        if raw_scroll_view.search(source) or raw_scroll_bar.search(source) or raw_policy.search(source):
            violations.append(qml_file.relative_to(qml_root).as_posix())

    assert violations == []


def test_fluent_scroll_view_positions_and_clips_its_bars() -> None:
    engine, _component, instance = _qml_component(
        b'''import QtQuick
import Fleasion.Components
Item {
    width: 240
    height: 120
    property real verticalX: view.verticalScrollBar.x
    property real verticalHeight: view.verticalScrollBar.height
    property bool horizontalHidden: !view.horizontalScrollBar.visible
    property bool viewportClips: view.clip
    FluentScrollView {
        id: view
        anchors.fill: parent
        contentWidth: availableWidth
        horizontalScrollBarEnabled: false
        Rectangle {
            implicitWidth: 220
            implicitHeight: 400
        }
    }
}
'''
    )
    assert isinstance(instance, QQuickItem)
    try:
        assert instance.property('viewportClips') is True
        assert instance.property('horizontalHidden') is True
        assert instance.property('verticalX') == 228.0
        assert instance.property('verticalHeight') == 120.0
    finally:
        _dispose_qml(engine, instance)


def test_fluent_scroll_bar_supports_keyboard_scrolling() -> None:
    engine, _component, instance = _qml_component(
        b'''import QtQuick
import QtQuick.Controls
import Fleasion.Components
ApplicationWindow {
    visible: true
    width: 120
    height: 240
    property real barPosition: bar.position
    function focusBar() {
        bar.forceActiveFocus()
    }
    FluentScrollBar {
        id: bar
        anchors.right: parent.right
        height: parent.height
        orientation: Qt.Vertical
        size: 0.2
        position: 0.4
    }
}
'''
    )
    assert isinstance(instance, QQuickWindow)
    window = cast(Any, instance)
    try:
        window.focusBar()
        QCoreApplication.processEvents()
        initial_position = float(instance.property('barPosition'))

        QTest.keyClick(window, Qt.Key.Key_Down)
        QCoreApplication.processEvents()
        assert float(instance.property('barPosition')) > initial_position

        QTest.keyClick(window, Qt.Key.Key_Home)
        QCoreApplication.processEvents()
        assert instance.property('barPosition') == 0.0

        QTest.keyClick(window, Qt.Key.Key_End)
        QCoreApplication.processEvents()
        assert abs(float(instance.property('barPosition')) - 0.8) < 0.001
    finally:
        _dispose_qml(engine, instance)
