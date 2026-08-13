pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import Fleasion.Theme
import "../src/fleasion/qml/screens/replacer" as Replacer

Item {
    id: root

    width: 1000
    height: 200

    QtObject {
        id: controllerStub

        property var configs: ["Default"]
        property string activeConfig: "Default"
        property var enabledConfigs: []
        property bool canUndo: true
        property bool canRedo: true

        function selectConfig(_name) {
        }
        function setConfigEnabled(_name, _enabled) {
        }
        function undo() {
        }
        function redo() {
        }
    }

    Component {
        id: toolbarComponent

        Replacer.ReplacerToolbar {
            width: 900
            height: implicitHeight
            controller: controllerStub
        }
    }

    TestCase {
        name: "ReplacerToolbarTests"
        when: windowShown

        function test_narrowWindowUsesTwoRowsWithoutClipping() {
            const toolbar = createTemporaryObject(toolbarComponent, root);
            verify(!!toolbar, "Component exists");
            compare(toolbar.compactLayout, false);
            compare(toolbar.implicitHeight, Theme.controlHeight);

            toolbar.width = 612;
            tryCompare(toolbar, "compactLayout", true);
            tryCompare(toolbar, "implicitHeight", Theme.controlHeight * 2 + Theme.spaceXxs);

            const redoButton = findChild(toolbar, "redoButton");
            verify(!!redoButton, "Object exists");
            const topLeft = redoButton.mapToItem(toolbar, 0, 0);
            verify(topLeft.x >= 0);
            verify(topLeft.y >= 0);
            verify(topLeft.x + redoButton.width <= toolbar.width);
            verify(topLeft.y + redoButton.height <= toolbar.height);
        }
    }
}
