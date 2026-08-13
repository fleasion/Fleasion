import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

RadioButton {
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
        radius: width / 2
        color: Theme.surfaceElevated
        border.width: root.checked ? 5 : root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : root.checked ? Theme.accent : Theme.borderStrong
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
