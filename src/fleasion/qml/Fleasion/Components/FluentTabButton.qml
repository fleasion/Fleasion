import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

TabButton {
    id: root

    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceMd
    rightPadding: Theme.spaceMd
    hoverEnabled: true
    activeFocusOnTab: true

    contentItem: Label {
        text: root.text
        color: root.enabled ? root.checked ? Theme.accent : Theme.textPrimary : Theme.textDisabled
        font.pointSize: TypeScale.label
        font.weight: root.checked ? TypeScale.semibold : TypeScale.medium
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        color: root.down ? Theme.surfacePressed : root.hovered ? Theme.surfaceHover : 'transparent'
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            width: root.checked ? Math.max(24, parent.width - Theme.spaceLg * 2) : 0
            height: 3
            radius: 2
            color: Theme.accent

            Behavior on width {
                NumberAnimation {
                    duration: Motion.fast
                }
            }
        }
    }
}
