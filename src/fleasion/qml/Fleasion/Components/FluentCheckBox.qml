import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

CheckBox {
    id: root

    implicitHeight: Theme.controlHeight
    spacing: Theme.spaceXs
    leftPadding: mirrored ? 0 : indicator.width + spacing
    rightPadding: mirrored ? indicator.width + spacing : 0
    hoverEnabled: true
    activeFocusOnTab: true

    indicator: Rectangle {
        x: root.mirrored ? root.width - width : 0
        y: (root.height - height) / 2
        implicitWidth: 20
        implicitHeight: 20
        radius: Theme.radiusSm - 2
        color: root.checked ? Theme.accent : root.hovered ? Theme.surfaceHover : Theme.surfaceElevated
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : root.checked ? Theme.accent : Theme.borderStrong

        Label {
            anchors.centerIn: parent
            text: '\u2713'
            visible: root.checked
            color: Theme.accentForeground
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.semibold
            Accessible.ignored: true
        }
    }

    contentItem: Label {
        text: root.text
        color: root.enabled ? Theme.textPrimary : Theme.textDisabled
        font.pointSize: TypeScale.label
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
        Accessible.ignored: true
    }
}
