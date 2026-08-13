pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ItemDelegate {
    id: root

    required property var selectionModel
    required property TreeView treeView
    required property bool isTreeNode
    required property bool expanded
    required property bool hasChildren
    required property int depth
    required property int row
    required property int column
    required property var model
    property bool valueSelected: false
    readonly property bool importableValue: root.model !== undefined && root.model !== null && root.model.importable
    readonly property string nodeName: root.model !== undefined && root.model !== null ? root.model.nodeName : ''
    readonly property string nodePath: root.model !== undefined && root.model !== null ? root.model.nodePath : ''
    readonly property string nodeValue: root.model !== undefined && root.model !== null ? root.model.valueText : ''
    readonly property string nodeKind: root.model !== undefined && root.model !== null ? root.model.valueKind : 'string'
    readonly property string nodeRowId: root.model !== undefined && root.model !== null ? root.model.rowId : ''
    readonly property int nodeChildCount: root.model !== undefined && root.model !== null ? root.model.childCount : 0

    function syncSelection() {
        root.valueSelected = root.importableValue && root.selectionModel.contains(root.nodeRowId);
    }

    implicitWidth: root.treeView.width
    implicitHeight: Theme.largeControlHeight
    topPadding: Theme.spaceXxs
    bottomPadding: Theme.spaceXxs
    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceSm
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.role: root.importableValue ? Accessible.CheckBox : Accessible.ListItem
    Accessible.name: {
        if (root.hasChildren)
            return qsTr('%1, %n child item(s)', '', root.nodeChildCount).arg(root.nodeName);
        return qsTr('%1, %2').arg(root.nodeName).arg(root.nodeValue);
    }
    Accessible.checked: root.importableValue && root.valueSelected
    ToolTip.visible: root.hovered && root.nodePath.length > 36
    ToolTip.text: root.nodePath
    ToolTip.delay: 650

    onClicked: {
        if (root.hasChildren) {
            root.treeView.toggleExpanded(root.row);
        } else if (root.importableValue) {
            root.selectionModel.setSelected(root.nodeRowId, !root.valueSelected);
        }
    }
    onRowChanged: root.syncSelection()

    Keys.onLeftPressed: event => {
        if (root.hasChildren && root.expanded)
            root.treeView.collapse(root.row);
        event.accepted = true;
    }
    Keys.onRightPressed: event => {
        if (root.hasChildren && !root.expanded)
            root.treeView.expand(root.row);
        event.accepted = true;
    }
    Keys.onSpacePressed: event => {
        root.clicked();
        event.accepted = true;
    }

    Connections {
        target: root.selectionModel

        function onSelectionChanged() {
            root.syncSelection();
        }
    }

    Component.onCompleted: root.syncSelection()
    TableView.onReused: root.syncSelection()

    contentItem: RowLayout {
        spacing: Theme.spaceSm

        Item {
            Layout.preferredWidth: root.depth * Theme.spaceLg
            Layout.preferredHeight: 1
        }

        Rectangle {
            Layout.preferredWidth: 28
            Layout.preferredHeight: 28
            color: root.hasChildren && root.hovered ? Theme.surfacePressed : 'transparent'
            radius: Theme.radiusSm
            Accessible.ignored: true

            Label {
                anchors.centerIn: parent
                visible: root.hasChildren
                text: root.expanded ? '⌄' : '›'
                color: Theme.textSecondary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.semibold
                Accessible.ignored: true
            }
        }

        Rectangle {
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
            visible: root.importableValue
            color: root.valueSelected ? Theme.accent : 'transparent'
            radius: Theme.radiusSm
            border.width: root.valueSelected ? 0 : 1
            border.color: Theme.borderStrong
            Accessible.ignored: true

            Label {
                anchors.centerIn: parent
                visible: root.valueSelected
                text: '✓'
                color: Theme.accentForeground
                font.pointSize: TypeScale.caption
                font.weight: TypeScale.semibold
                Accessible.ignored: true
            }
        }

        Item {
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
            visible: !root.importableValue
        }

        Label {
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            text: root.nodeName
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: root.hasChildren ? TypeScale.semibold : TypeScale.medium
            elide: Text.ElideRight
        }

        Label {
            Layout.preferredWidth: 220
            text: root.hasChildren ? qsTr('%n item(s)', '', root.nodeChildCount) : root.nodeValue
            color: root.hasChildren ? Theme.textTertiary : Theme.textSecondary
            font.family: root.hasChildren ? '' : 'monospace'
            font.pointSize: TypeScale.label
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideMiddle
        }

        StatusPill {
            Layout.preferredWidth: 92
            text: {
                switch (root.nodeKind) {
                case 'object':
                    return qsTr('Object');
                case 'array':
                    return qsTr('Array');
                case 'id':
                    return qsTr('Asset ID');
                case 'url':
                    return qsTr('URL');
                case 'path':
                    return qsTr('Local file');
                case 'boolean':
                    return qsTr('Boolean');
                case 'number':
                    return qsTr('Number');
                case 'null':
                    return qsTr('Null');
                default:
                    return qsTr('Text');
                }
            }
            status: root.importableValue ? 'info' : 'neutral'
        }
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            color: {
                if (root.valueSelected)
                    return Theme.accentSubtle;
                if (root.down)
                    return Theme.surfacePressed;
                return root.hovered ? Theme.surfaceHover : 'transparent';
            }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.border
        }

        Rectangle {
            anchors.fill: parent
            color: 'transparent'
            border.width: root.activeFocus ? 2 : 0
            border.color: Theme.focusRing
        }
    }
}
