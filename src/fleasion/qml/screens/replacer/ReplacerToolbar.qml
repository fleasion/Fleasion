import QtQuick
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Item {
    id: root

    required property var controller
    signal createProfileRequested
    signal renameProfileRequested
    signal duplicateProfileRequested
    signal deleteProfileRequested
    signal importProfileRequested
    signal exportProfileRequested
    signal communityPresetsRequested

    readonly property real singleRowMinimumWidth: 160 + enabledSwitch.implicitWidth + Theme.controlHeight * 9 + Theme.spaceXs * 11 + 1 + Theme.spaceSm
    readonly property bool compactLayout: width < singleRowMinimumWidth

    implicitHeight: toolbar.implicitHeight
    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("Replacement profile tools")

    GridLayout {
        id: toolbar

        anchors.fill: parent
        columns: root.compactLayout ? 10 : 12
        columnSpacing: Theme.spaceXs
        rowSpacing: Theme.spaceXxs

        FluentComboBox {
            id: profilePicker

            Layout.row: 0
            Layout.column: 0
            Layout.columnSpan: root.compactLayout ? 8 : 1
            Layout.fillWidth: true
            Layout.minimumWidth: root.compactLayout ? 0 : 160
            Layout.maximumWidth: root.compactLayout ? 65535 : 300
            model: root.controller.configs
            currentIndex: root.controller.configs.indexOf(root.controller.activeConfig)
            Accessible.name: qsTr("Active replacement profile")
            onActivated: index => root.controller.selectConfig(root.controller.configs[index])
        }

        FluentSwitch {
            id: enabledSwitch

            Layout.row: 0
            Layout.column: root.compactLayout ? 8 : 1
            Layout.columnSpan: root.compactLayout ? 2 : 1
            Layout.alignment: Qt.AlignRight
            checked: root.controller.enabledConfigs.indexOf(root.controller.activeConfig) !== -1
            text: qsTr("Enabled")
            Accessible.name: qsTr("Enable profile %1").arg(root.controller.activeConfig)
            onToggled: root.controller.setConfigEnabled(root.controller.activeConfig, checked)
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 0 : 2
            iconText: "+"
            text: qsTr("New profile")
            onClicked: root.createProfileRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 1 : 3
            iconText: "⎘"
            text: qsTr("Duplicate profile")
            onClicked: root.duplicateProfileRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 2 : 4
            iconText: "✎"
            text: qsTr("Rename profile")
            onClicked: root.renameProfileRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 3 : 5
            iconText: "↓"
            text: qsTr("Import profile")
            onClicked: root.importProfileRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 4 : 6
            iconText: "↑"
            text: qsTr("Export profile")
            onClicked: root.exportProfileRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 5 : 7
            iconText: "☁"
            text: qsTr("Community presets")
            onClicked: root.communityPresetsRequested()
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 6 : 8
            iconText: "⌫"
            text: qsTr("Delete profile")
            danger: true
            onClicked: root.deleteProfileRequested()
        }

        Rectangle {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 7 : 9
            Layout.preferredWidth: 1
            Layout.preferredHeight: Theme.controlHeight - Theme.spaceSm
            color: Theme.border
            Accessible.ignored: true
        }

        IconButton {
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 8 : 10
            iconText: "↶"
            text: qsTr("Undo")
            enabled: root.controller.canUndo
            onClicked: root.controller.undo()
        }

        IconButton {
            id: redoButton

            objectName: "redoButton"
            Layout.row: root.compactLayout ? 1 : 0
            Layout.column: root.compactLayout ? 9 : 11
            iconText: "↷"
            text: qsTr("Redo")
            enabled: root.controller.canRedo
            onClicked: root.controller.redo()
        }
    }
}
