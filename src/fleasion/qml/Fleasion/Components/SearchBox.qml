import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: root

    property string accessibleName: qsTr('Search')
    property bool showClearButton: true

    signal cleared

    placeholderText: qsTr('Search')
    leftPadding: 40
    rightPadding: clearButton.visible ? 44 : Theme.spaceSm
    implicitWidth: 280
    implicitHeight: Theme.controlHeight
    selectByMouse: true
    activeFocusOnTab: true
    color: Theme.textPrimary
    placeholderTextColor: Theme.textTertiary
    selectionColor: Theme.accent
    selectedTextColor: Theme.accentForeground
    font.pointSize: TypeScale.body
    Accessible.name: accessibleName
    Keys.onEscapePressed: event => {
        if (root.text.length > 0) {
            root.clear();
            root.cleared();
            event.accepted = true;
        }
    }

    Label {
        anchors.left: parent.left
        anchors.leftMargin: Theme.spaceSm
        anchors.verticalCenter: parent.verticalCenter
        text: '\u2315'
        color: root.activeFocus ? Theme.accent : Theme.textSecondary
        font.pointSize: TypeScale.body
        Accessible.ignored: true
    }

    IconButton {
        id: clearButton

        anchors.right: parent.right
        anchors.rightMargin: 2
        anchors.verticalCenter: parent.verticalCenter
        visible: root.showClearButton && root.text.length > 0
        text: qsTr('Clear search')
        iconText: '\u00d7'
        flat: true
        controlSize: root.height - 4
        onClicked: {
            root.clear();
            root.cleared();
            root.forceActiveFocus();
        }
    }

    background: Rectangle {
        color: root.enabled ? Theme.surfaceElevated : Theme.surfaceSubtle
        radius: Theme.radiusMd
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.borderStrong
    }
}
