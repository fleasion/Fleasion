import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: root

    implicitWidth: 220
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceSm
    rightPadding: Theme.spaceSm
    selectByMouse: true
    activeFocusOnTab: true
    color: Theme.textPrimary
    placeholderTextColor: Theme.textTertiary
    selectionColor: Theme.accent
    selectedTextColor: Theme.accentForeground
    font.pointSize: TypeScale.body

    background: Rectangle {
        color: root.enabled ? Theme.surfaceElevated : Theme.surfaceSubtle
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.borderStrong

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: Theme.radiusSm
            anchors.rightMargin: Theme.radiusSm
            height: root.activeFocus ? 2 : 1
            color: root.activeFocus ? Theme.accent : Theme.borderStrong
        }
    }
}
