pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ItemDelegate {
    id: root

    required property TreeView treeView
    required property bool isTreeNode
    required property bool expanded
    required property bool hasChildren
    required property int depth
    required property int row
    required property int column
    required property var model
    required property var appController
    readonly property string nodeName: root.model ? String(root.model.nodeName || '') : ''
    readonly property string nodePath: root.model ? String(root.model.nodePath || '') : ''
    readonly property string nodeValue: root.model ? String(root.model.valueText || '') : ''
    readonly property string nodeKind: root.model ? String(root.model.valueKind || 'string') : 'string'
    readonly property int nodeChildCount: root.model ? Number(root.model.childCount || 0) : 0
    readonly property bool narrow: root.treeView.width < 480

    implicitWidth: root.treeView.width
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceXs
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: root.hasChildren ? qsTr('%1, %n child item(s)', '', root.nodeChildCount).arg(root.nodeName) : qsTr('%1, %2').arg(root.nodeName).arg(root.nodeValue)
    ToolTip.visible: root.hovered && root.nodePath.length > 44
    ToolTip.text: root.nodePath
    ToolTip.delay: 650
    onClicked: {
        if (root.hasChildren)
            root.treeView.toggleExpanded(root.row);
    }

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

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        Item {
            Layout.preferredWidth: Math.min(root.depth * Theme.spaceLg, root.narrow ? 64 : 160)
            Layout.preferredHeight: 1
        }

        Label {
            Layout.preferredWidth: 24
            text: root.hasChildren ? (root.expanded ? '⌄' : '›') : ''
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.semibold
            horizontalAlignment: Text.AlignHCenter
            Accessible.ignored: true
        }

        Label {
            Layout.fillWidth: true
            Layout.minimumWidth: root.narrow ? 48 : 100
            text: root.nodeName
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: root.hasChildren ? TypeScale.semibold : TypeScale.medium
            elide: Text.ElideRight
        }

        Label {
            Layout.preferredWidth: root.narrow ? 88 : Math.max(120, root.treeView.width * 0.34)
            text: root.hasChildren ? qsTr('%n item(s)', '', root.nodeChildCount) : root.nodeValue
            color: root.hasChildren ? Theme.textTertiary : Theme.textSecondary
            font.family: root.hasChildren ? '' : 'monospace'
            font.pointSize: TypeScale.label
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideMiddle
        }

        StatusPill {
            Layout.preferredWidth: 78
            visible: !root.narrow
            text: {
                switch (root.nodeKind) {
                case 'object':
                    return qsTr('Object');
                case 'array':
                    return qsTr('Array');
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
            status: 'neutral'
        }

        IconButton {
            visible: !root.hasChildren
            iconText: '⧉'
            text: qsTr('Copy value')
            flat: true
            controlSize: Theme.controlHeight - 4
            onClicked: root.appController.copyText(root.nodeValue)
        }
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            color: root.down ? Theme.surfacePressed : root.hovered ? Theme.surfaceHover : 'transparent'
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
