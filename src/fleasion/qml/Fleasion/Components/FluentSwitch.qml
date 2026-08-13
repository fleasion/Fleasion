import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

Switch {
    id: root

    implicitWidth: Math.max(48, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeight
    spacing: Theme.spaceXs
    leftPadding: mirrored ? 0 : indicator.width + spacing
    rightPadding: mirrored ? indicator.width + spacing : 0
    hoverEnabled: true
    activeFocusOnTab: true

    indicator: Rectangle {
        id: track

        x: root.mirrored ? root.width - width : 0
        y: (root.height - height) / 2
        implicitWidth: 42
        implicitHeight: 22
        radius: height / 2
        color: root.checked ? Theme.accent : root.hovered ? Theme.surfacePressed : Theme.surfaceSubtle
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : root.checked ? Theme.accent : Theme.borderStrong

        Rectangle {
            width: 14
            height: 14
            y: (parent.height - height) / 2
            x: root.checked ? parent.width - width - 4 : 4
            radius: width / 2
            color: root.checked ? Theme.accentForeground : Theme.textSecondary

            Behavior on x {
                NumberAnimation {
                    duration: Motion.fast
                    easing.type: Easing.OutCubic
                }
            }
        }
    }

    contentItem: Label {
        text: root.text
        color: root.enabled ? Theme.textPrimary : Theme.textDisabled
        font.pointSize: TypeScale.label
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        Accessible.ignored: true
    }
}
