pragma ComponentBehavior: Bound

import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ItemDelegate {
    id: root

    required property var controller
    required property TreeView treeView
    required property bool isTreeNode
    required property bool expanded
    required property bool hasChildren
    required property int depth
    required property int row
    required property int column
    required property var model
    readonly property bool selected: root.controller.selectedReferent === String(root.model.referent)

    implicitWidth: root.treeView.width
    implicitHeight: 38
    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceXs
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.role: Accessible.TreeItem
    Accessible.name: qsTr('%1, %2, %n child item(s)', '', root.model.childCount).arg(root.model.displayName).arg(root.model.className)
    Accessible.selected: root.selected

    onClicked: {
        root.controller.selectInstance(String(root.model.referent));
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
    Keys.onReturnPressed: event => {
        root.clicked();
        event.accepted = true;
    }

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        Item {
            Layout.preferredWidth: root.depth * Theme.spaceMd
            Layout.preferredHeight: 1
        }

        Label {
            Layout.preferredWidth: 18
            visible: root.hasChildren
            text: root.expanded ? '⌄' : '›'
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            horizontalAlignment: Text.AlignHCenter
        }

        Item {
            Layout.preferredWidth: 18
            visible: !root.hasChildren
        }

        Label {
            Layout.fillWidth: true
            text: root.model.displayName
            color: Theme.textPrimary
            font.pointSize: TypeScale.label
            font.weight: root.hasChildren ? TypeScale.semibold : TypeScale.medium
            elide: Text.ElideRight
        }

        Label {
            Layout.preferredWidth: 100
            text: root.model.className
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
        }
    }

    background: Rectangle {
        color: root.selected ? Theme.accentSubtle : root.down ? Theme.surfacePressed : root.hovered ? Theme.surfaceHover : 'transparent'
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing
    }
}
