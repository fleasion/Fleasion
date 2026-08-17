pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    objectName: "replacerSelectionBar"

    required property var controller
    property var selectedPaths: []
    property bool canGroupSelection: false
    readonly property int selectedCount: selectedPaths.length
    readonly property bool compactLayout: width < 820
    signal groupRequested
    signal deleteRequested

    function syncSelection() {
        const values = root.controller.selection.values();
        root.selectedPaths = values;
        root.canGroupSelection = root.controller.canGroupEntries(values);
    }

    implicitHeight: actions.implicitHeight + Theme.spaceXs * 2
    color: Theme.accentSubtle
    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("Selected replacement actions")

    GridLayout {
        id: actions

        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceXs
        anchors.topMargin: Theme.spaceXs
        anchors.bottomMargin: Theme.spaceXs
        columns: 8
        columnSpacing: Theme.spaceXs
        rowSpacing: Theme.spaceXxs

        Label {
            Layout.row: 0
            Layout.column: 0
            Layout.columnSpan: root.compactLayout ? 8 : 1
            Layout.fillWidth: true
            text: qsTr("%n selected", "", root.selectedCount)
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.medium
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 0 : 1
            controlSize: 32
            flat: true
            iconText: "✓"
            text: qsTr("Enable selected")
            onClicked: root.controller.setEntriesEnabled(root.selectedPaths, true)
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 1 : 2
            controlSize: 32
            flat: true
            iconText: "⊘"
            text: qsTr("Disable selected")
            onClicked: root.controller.setEntriesEnabled(root.selectedPaths, false)
        }

        FluentButton {
            objectName: "groupSelectionButton"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 2 : 3
            compact: true
            text: qsTr("Group")
            enabled: root.canGroupSelection
            onClicked: root.groupRequested()
        }

        FluentComboBox {
            id: destinationPicker

            objectName: "moveDestinationPicker"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 3 : 4
            Layout.columnSpan: root.compactLayout ? 2 : 1
            Layout.fillWidth: true
            Layout.minimumWidth: 140
            Layout.maximumWidth: 230
            model: root.controller.groupDestinations
            textRole: "label"
            currentIndex: 0
            Accessible.name: qsTr("Move destination")
        }

        FluentButton {
            objectName: "moveSelectionButton"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 5 : 5
            compact: true
            text: qsTr("Move")
            onClicked: {
                const choices = root.controller.groupDestinations;
                const destination = destinationPicker.currentIndex >= 0 && destinationPicker.currentIndex < choices.length ? String(choices[destinationPicker.currentIndex].path || "") : "";
                root.controller.moveEntries(root.selectedPaths, destination, -1);
            }
        }

        IconButton {
            objectName: "clearSelectionButton"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 6 : 6
            controlSize: 32
            flat: true
            iconText: "×"
            text: qsTr("Clear selection")
            onClicked: root.controller.selection.clear()
        }

        IconButton {
            objectName: "deleteSelectionButton"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 7 : 7
            controlSize: 32
            flat: true
            danger: true
            iconText: "⌫"
            text: qsTr("Delete selected")
            onClicked: root.deleteRequested()
        }
    }

    Component.onCompleted: root.syncSelection()

    Connections {
        target: root.controller.selection

        function onSelectionChanged() {
            root.syncSelection();
        }
    }

    Connections {
        target: root.controller

        function onModelChanged() {
            if (destinationPicker.currentIndex >= root.controller.groupDestinations.length)
                destinationPicker.currentIndex = 0;
            root.syncSelection();
        }
    }
}
