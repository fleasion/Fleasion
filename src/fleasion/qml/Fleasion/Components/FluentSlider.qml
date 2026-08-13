import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.Slider {
    id: root

    implicitHeight: Theme.controlHeight
    activeFocusOnTab: true

    background: Rectangle {
        x: root.leftPadding
        y: root.topPadding + (root.availableHeight - height) / 2
        width: root.availableWidth
        height: 4
        radius: 2
        color: Theme.borderStrong

        Rectangle {
            width: root.visualPosition * parent.width
            height: parent.height
            radius: parent.radius
            color: root.enabled ? Theme.accent : Theme.textDisabled
        }
    }

    handle: Rectangle {
        x: root.leftPadding + root.visualPosition * (root.availableWidth - width)
        y: root.topPadding + (root.availableHeight - height) / 2
        implicitWidth: root.pressed ? 18 : 16
        implicitHeight: implicitWidth
        radius: width / 2
        color: root.enabled ? Theme.accentForeground : Theme.surfaceSubtle
        border.width: root.activeFocus ? 3 : 2
        border.color: root.activeFocus ? Theme.focusRing : root.enabled ? Theme.accent : Theme.textDisabled
    }
}
