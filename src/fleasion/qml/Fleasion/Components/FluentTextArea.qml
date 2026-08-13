import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

TextArea {
    id: root

    implicitWidth: 320
    implicitHeight: 112
    padding: Theme.spaceSm
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
    }
}
