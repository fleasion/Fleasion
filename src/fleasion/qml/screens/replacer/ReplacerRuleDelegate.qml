import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    objectName: "replacerRuleDelegate"

    required property var selectionModel
    required property string entryPath
    required property string entryKind
    required property int entryDepth
    required property string entryName
    required property bool entryEnabled
    required property string entryState
    required property bool entryExpanded
    required property int childCount
    required property bool canMoveUp
    required property bool canMoveDown
    required property string actionText
    required property string replacementText
    required property string targetsText
    required property int targetCount
    property bool selected: false
    signal enabledToggled(string entryPath, bool enabled)
    signal editRequested(string entryPath)
    signal groupRenameRequested(string entryPath, string entryName)
    signal expansionToggled(string entryPath, bool expanded)
    signal moveRequested(string entryPath, int direction)

    function syncSelection() {
        if (!root.selectionModel)
            return;
        root.selected = root.selectionModel.contains(root.entryPath);
    }

    implicitHeight: root.entryKind === "group" ? 42 : 52
    color: root.selected ? Theme.accentSubtle : pointer.hovered ? Theme.surfaceHover : "transparent"
    border.width: root.activeFocus ? 2 : 0
    border.color: Theme.focusRing
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: root.entryKind === "group" ? qsTr("Group %1, %2 replacements, %3").arg(root.entryName).arg(root.targetCount).arg(root.entryExpanded ? qsTr("expanded") : qsTr("collapsed")) : qsTr("%1, replaces %2 targets with %3").arg(root.entryName).arg(root.targetCount).arg(root.actionText)

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceXs + root.entryDepth * 18
        anchors.rightMargin: Theme.spaceXs
        spacing: Theme.spaceXs

        Item {
            Layout.preferredWidth: 28
            Layout.preferredHeight: 28

            IconButton {
                objectName: "groupExpansionButton"
                anchors.fill: parent
                visible: root.entryKind === "group"
                controlSize: 28
                flat: true
                iconText: root.entryExpanded ? "⌄" : "›"
                text: root.entryExpanded ? qsTr("Collapse %1").arg(root.entryName) : qsTr("Expand %1").arg(root.entryName)
                onClicked: root.expansionToggled(root.entryPath, !root.entryExpanded)
            }
        }

        FluentCheckBox {
            checked: root.selected
            Accessible.name: qsTr("Select %1").arg(root.entryName)
            onToggled: root.selectionModel.setSelected(root.entryPath, checked)
        }

        FluentCheckBox {
            tristate: root.entryKind === "group"
            checkState: root.entryState === "mixed" ? Qt.PartiallyChecked : root.entryEnabled ? Qt.Checked : Qt.Unchecked
            Accessible.name: root.entryKind === "group" ? qsTr("Enable all replacements in %1").arg(root.entryName) : qsTr("Enable %1").arg(root.entryName)
            onClicked: root.enabledToggled(root.entryPath, checkState === Qt.Checked)
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 100
            spacing: 1

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
            text: root.entryKind === "group" ? qsTr("%n rule(s)", "", root.targetCount) : root.actionText
            status: root.entryKind === "group" ? "neutral" : root.actionText === "Remove" ? "warning" : "info"
        }

        Label {
            Layout.preferredWidth: 160
            visible: root.width >= 740 && root.entryKind !== "group"
            text: root.replacementText.length > 0 ? root.replacementText : qsTr("Original removed")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            elide: Text.ElideMiddle
        }

        IconButton {
            objectName: "moveEntryUpButton"
            controlSize: 28
            flat: true
            iconText: "↑"
            text: qsTr("Move %1 up").arg(root.entryName)
            enabled: root.canMoveUp
            onClicked: root.moveRequested(root.entryPath, -1)
        }

        IconButton {
            objectName: "moveEntryDownButton"
            controlSize: 28
            flat: true
            iconText: "↓"
            text: qsTr("Move %1 down").arg(root.entryName)
            enabled: root.canMoveDown
            onClicked: root.moveRequested(root.entryPath, 1)
        }

        IconButton {
            objectName: "editEntryButton"
            controlSize: 28
            flat: true
            iconText: "✎"
            text: root.entryKind === "group" ? qsTr("Rename %1").arg(root.entryName) : qsTr("Edit %1").arg(root.entryName)
            onClicked: {
                if (root.entryKind === "group")
                    root.groupRenameRequested(root.entryPath, root.entryName);
                else
                    root.editRequested(root.entryPath);
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
        Accessible.ignored: true
    }

    HoverHandler {
        id: pointer
    }

    Keys.onReturnPressed: event => {
        if (root.entryKind === "group")
            root.expansionToggled(root.entryPath, !root.entryExpanded);
        else
            root.editRequested(root.entryPath);
        event.accepted = true;
    }
    Keys.onLeftPressed: event => {
        if (root.entryKind === "group" && root.entryExpanded)
            root.expansionToggled(root.entryPath, false);
        event.accepted = root.entryKind === "group";
    }
    Keys.onRightPressed: event => {
        if (root.entryKind === "group" && !root.entryExpanded)
            root.expansionToggled(root.entryPath, true);
        event.accepted = root.entryKind === "group";
    }
    Keys.onSpacePressed: event => {
        root.selectionModel.setSelected(root.entryPath, !root.selected);
        event.accepted = true;
    }

    Component.onCompleted: root.syncSelection()
    onEntryPathChanged: root.syncSelection()

    Connections {
        target: root.selectionModel

        function onSelectionChanged() {
            root.syncSelection();
        }
    }
}
