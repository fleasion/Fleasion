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
    required property bool showSource
    required property bool manualOrder
    required property bool filtering
    required property real stateColumnWidth
    required property real actionColumnWidth
    required property real sourceColumnWidth
    required property real organizeColumnWidth
    property bool selected: false
    property string dropPosition
    readonly property bool dragAllowed: manualOrder && !filtering
    signal enabledToggled(string entryPath, bool enabled)
    signal editRequested(string entryPath)
    signal groupRenameRequested(string entryPath, string entryName)
    signal expansionToggled(string entryPath, bool expanded)
    signal selectionRequested(string entryPath, bool toggle, bool extend)
    signal contextMenuRequested(string entryPath, real sceneX, real sceneY)
    signal dragStarted(string entryPath, real sceneX, real sceneY)
    signal dragMoved(real sceneX, real sceneY)
    signal dragFinished(real sceneX, real sceneY)

    function syncSelection() {
        if (!root.selectionModel)
            return;
        root.selected = root.selectionModel.contains(root.entryPath);
    }

    implicitHeight: root.entryKind === "group" ? 42 : 52
    color: root.selected ? Theme.accentSubtle : rowPointer.containsMouse ? Theme.surfaceHover : "transparent"
    border.width: root.dropPosition === "into" || root.activeFocus ? 2 : 0
    border.color: root.dropPosition === "into" ? Theme.accent : Theme.focusRing
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: root.entryKind === "group" ? qsTr("Group %1, %2 replacements, %3").arg(root.entryName).arg(root.targetCount).arg(root.entryExpanded ? qsTr("expanded") : qsTr("collapsed")) : qsTr("%1, replaces %2 targets with %3").arg(root.entryName).arg(root.targetCount).arg(root.actionText)

    MouseArea {
        id: rowPointer

        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        hoverEnabled: true
        onClicked: mouse => {
            root.forceActiveFocus();
            if (mouse.button === Qt.RightButton) {
                const scenePoint = root.mapToItem(null, mouse.x, mouse.y);
                root.contextMenuRequested(root.entryPath, scenePoint.x, scenePoint.y);
                return;
            }
            const toggle = (mouse.modifiers & Qt.ControlModifier) !== 0 || (mouse.modifiers & Qt.MetaModifier) !== 0;
            const extend = (mouse.modifiers & Qt.ShiftModifier) !== 0;
            root.selectionRequested(root.entryPath, toggle, extend);
        }
        onDoubleClicked: mouse => {
            if (mouse.button !== Qt.LeftButton)
                return;
            if (root.entryKind === "group")
                root.expansionToggled(root.entryPath, !root.entryExpanded);
            else
                root.editRequested(root.entryPath);
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        spacing: Theme.spaceXs

        Item {
            Layout.preferredWidth: root.stateColumnWidth
            Layout.fillHeight: true

            FluentCheckBox {
                anchors.centerIn: parent
                tristate: root.entryKind === "group"
                checkState: root.entryState === "mixed" ? Qt.PartiallyChecked : root.entryEnabled ? Qt.Checked : Qt.Unchecked
                Accessible.name: root.entryKind === "group" ? qsTr("Enable all replacements in %1").arg(root.entryName) : qsTr("Enable %1").arg(root.entryName)
                onClicked: root.enabledToggled(root.entryPath, checkState === Qt.Checked)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            spacing: Theme.spaceXxs

            Item {
                Layout.preferredWidth: root.entryDepth * 18
                Layout.fillHeight: true
            }

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

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Label {
                    objectName: "replacementNameLabel"
                    Layout.fillWidth: true
                    text: root.entryName
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.body
                    font.weight: root.entryKind === "group" ? TypeScale.semibold : TypeScale.medium
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    visible: root.entryKind !== "group"
                    text: root.targetsText.length > 0 ? qsTr("Targets: %1").arg(root.targetsText) : qsTr("No targets")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideMiddle
                }
            }
        }

        Item {
            Layout.preferredWidth: root.actionColumnWidth
            Layout.fillHeight: true

            StatusPill {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: root.entryKind === "group" ? qsTr("%n rule(s)", "", root.targetCount) : root.actionText
                status: root.entryKind === "group" ? "neutral" : root.actionText === "Remove" ? "warning" : "info"
            }
        }

        Label {
            Layout.preferredWidth: root.sourceColumnWidth
            Layout.fillHeight: true
            visible: root.showSource
            text: root.entryKind === "group" ? "" : root.replacementText.length > 0 ? root.replacementText : qsTr("Original removed")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideMiddle
        }

        Item {
            Layout.preferredWidth: root.organizeColumnWidth
            Layout.fillHeight: true

            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spaceXxs

                Item {
                    width: 28
                    height: 28

                    Label {
                        anchors.centerIn: parent
                        text: "≡"
                        color: root.dragAllowed ? dragPointer.containsMouse || dragPointer.dragging ? Theme.accent : Theme.textSecondary : Theme.textDisabled
                        font.pointSize: TypeScale.title
                        Accessible.ignored: true
                    }

                    MouseArea {
                        id: dragPointer

                        property bool dragging: false
                        property point pressScene

                        anchors.fill: parent
                        enabled: root.dragAllowed
                        cursorShape: enabled ? Qt.SizeAllCursor : Qt.ArrowCursor
                        hoverEnabled: true
                        Accessible.role: Accessible.Button
                        Accessible.name: root.dragAllowed ? qsTr("Drag %1 to reorder").arg(root.entryName) : qsTr("Clear sorting and search to reorder %1").arg(root.entryName)
                        onPressed: mouse => {
                            pressScene = mapToItem(null, mouse.x, mouse.y);
                            dragging = false;
                        }
                        onPositionChanged: mouse => {
                            if (!pressed)
                                return;
                            const scenePoint = mapToItem(null, mouse.x, mouse.y);
                            const distance = Math.abs(scenePoint.x - pressScene.x) + Math.abs(scenePoint.y - pressScene.y);
                            if (!dragging && distance >= 6) {
                                dragging = true;
                                root.dragStarted(root.entryPath, scenePoint.x, scenePoint.y);
                            }
                            if (dragging)
                                root.dragMoved(scenePoint.x, scenePoint.y);
                        }
                        onReleased: mouse => {
                            if (dragging) {
                                const scenePoint = mapToItem(null, mouse.x, mouse.y);
                                root.dragFinished(scenePoint.x, scenePoint.y);
                            }
                            dragging = false;
                        }
                        onCanceled: dragging = false
                    }
                }

                IconButton {
                    objectName: "entryContextMenuButton"
                    controlSize: 28
                    flat: true
                    iconText: "⋯"
                    text: qsTr("More actions for %1").arg(root.entryName)
                    onClicked: {
                        const scenePoint = mapToItem(null, width, height);
                        root.contextMenuRequested(root.entryPath, scenePoint.x, scenePoint.y);
                    }
                }
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
        root.selectionRequested(root.entryPath, true, false);
        event.accepted = true;
    }
    Keys.onMenuPressed: event => {
        const scenePoint = root.mapToItem(null, root.width - root.organizeColumnWidth, root.height / 2);
        root.contextMenuRequested(root.entryPath, scenePoint.x, scenePoint.y);
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
