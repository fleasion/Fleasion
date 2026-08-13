import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

ItemDelegate {
    id: root

    implicitHeight: Theme.largeControlHeight
    leftPadding: Theme.spaceSm
    rightPadding: Theme.spaceSm
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    hoverEnabled: true
    activeFocusOnTab: true

    contentItem: Label {
        text: root.text
        color: root.enabled ? Theme.textPrimary : Theme.textDisabled
        font.pointSize: TypeScale.body
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        color: {
            if (root.down)
                return Theme.surfacePressed;
            if (root.highlighted || root.checked)
                return Theme.accentSubtle;
            return root.hovered ? Theme.surfaceHover : 'transparent';
        }
        radius: Theme.radiusMd
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
