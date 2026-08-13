import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

AbstractButton {
    id: root

    property string iconText: ''
    property bool flat: false
    property bool danger: false
    property int controlSize: Theme.controlHeight

    implicitWidth: controlSize
    implicitHeight: controlSize
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: text
    ToolTip.visible: hovered && text.length > 0
    ToolTip.text: text
    ToolTip.delay: 650

    contentItem: Label {
        text: root.iconText
        color: {
            if (!root.enabled)
                return Theme.textDisabled;

            if (root.danger)
                return Theme.danger;

            if (root.checked)
                return Theme.accent;

            return Theme.textPrimary;
        }
        font.pointSize: TypeScale.body
        font.weight: TypeScale.medium
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        Accessible.ignored: true
    }

    background: Rectangle {
        color: {
            if (root.down)
                return Theme.surfacePressed;

            if (root.hovered || root.checked)
                return Theme.surfaceHover;

            return root.flat ? 'transparent' : Theme.surfaceSubtle;
        }
        radius: Theme.radiusMd
        border.width: root.activeFocus ? 2 : (root.flat ? 0 : 1)
        border.color: root.activeFocus ? Theme.focusRing : Theme.border

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
