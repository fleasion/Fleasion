import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property var selectionModel
    required property string entryPath
    required property string entryKind
    required property int entryDepth
    required property string entryName
    required property bool entryEnabled
    required property string entryState
    required property string actionText
    required property string replacementText
    required property string targetsText
    required property int targetCount
    property bool selected: false
    signal enabledToggled(string entryPath, bool enabled)
    signal editRequested(string entryPath)

    function syncSelection() {
        root.selected = root.selectionModel.contains(root.entryPath);
    }

    implicitHeight: entryKind === "group" ? 46 : 56
    color: selected ? Theme.accentSubtle : pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: Theme.radiusMd
    border.width: activeFocus ? 2 : 0
    border.color: Theme.focusRing
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: entryKind === "group" ? qsTr("Group %1, %2 replacements").arg(entryName).arg(targetCount) : qsTr("%1, replaces %2 targets with %3").arg(entryName).arg(targetCount).arg(actionText)

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm + root.entryDepth * Theme.spaceMd
        anchors.rightMargin: Theme.spaceSm
        spacing: Theme.spaceSm

        FluentCheckBox {
            checked: root.selected
            Accessible.name: qsTr("Select %1").arg(root.entryName)
            onToggled: root.selectionModel.setSelected(root.entryPath, checked)
        }

        FluentCheckBox {
            tristate: root.entryKind === "group"
            checkState: root.entryState === "mixed" ? Qt.PartiallyChecked : root.entryEnabled ? Qt.Checked : Qt.Unchecked
            Accessible.name: qsTr("Enable %1").arg(root.entryName)
            onClicked: root.enabledToggled(root.entryPath, checkState === Qt.Checked)
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            spacing: 2

            Label {
                Layout.fillWidth: true
                text: root.entryName
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: root.entryKind === "group" ? TypeScale.semibold : TypeScale.medium
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                visible: root.entryKind !== "group"
                text: root.targetsText.length > 0 ? qsTr("Targets: %1").arg(root.targetsText) : qsTr("No targets")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideMiddle
            }
        }

        StatusPill {
            text: root.entryKind === "group" ? qsTr("Group") : root.actionText
            status: root.entryKind === "group" ? "neutral" : root.actionText === "Remove" ? "warning" : "info"
        }

        Label {
            Layout.preferredWidth: 190
            visible: root.width >= 650 && root.entryKind !== "group"
            text: root.replacementText.length > 0 ? root.replacementText : qsTr("Original removed")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            elide: Text.ElideMiddle
        }

        IconButton {
            visible: root.entryKind !== "group"
            iconText: "✎"
            text: qsTr("Edit %1").arg(root.entryName)
            onClicked: root.editRequested(root.entryPath)
        }
    }

    HoverHandler {
        id: pointer
    }

    Keys.onReturnPressed: event => {
        if (root.entryKind !== "group")
            root.editRequested(root.entryPath);
        event.accepted = true;
    }
    Keys.onSpacePressed: event => {
        root.selectionModel.setSelected(root.entryPath, !root.selected);
        event.accepted = true;
    }

    Component.onCompleted: syncSelection()
    onEntryPathChanged: syncSelection()

    Connections {
        target: root.selectionModel

        function onSelectionChanged() {
            root.syncSelection();
        }
    }
}
