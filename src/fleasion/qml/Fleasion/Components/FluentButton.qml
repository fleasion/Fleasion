import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic

AbstractButton {
    id: root

    property bool highlighted: false
    property bool flat: false
    property bool danger: false
    property bool compact: false

    implicitWidth: Math.max(compact ? 64 : 84, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: compact ? 34 : Theme.controlHeight
    leftPadding: compact ? Theme.spaceSm : Theme.spaceMd
    rightPadding: leftPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: text

    contentItem: Label {
        text: root.text
        color: {
            if (!root.enabled)
                return Theme.textDisabled;
            if (root.highlighted || root.danger)
                return Theme.accentForeground;
            return Theme.textPrimary;
        }
        font.pointSize: TypeScale.label
        font.weight: root.highlighted ? TypeScale.semibold : TypeScale.medium
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        Accessible.ignored: true
    }

    background: Rectangle {
        id: buttonSurface

        color: {
            if (!root.enabled)
                return Theme.surfaceSubtle;
            if (root.danger)
                return root.down ? Qt.darker(Theme.danger, 1.18) : root.hovered ? Qt.lighter(Theme.danger, 1.08) : Theme.danger;
            if (root.highlighted)
                return root.down ? Theme.accentPressed : root.hovered ? Theme.accentHover : Theme.accent;
            if (root.down)
                return Theme.surfacePressed;
            if (root.hovered || root.checked)
                return Theme.surfaceHover;
            return root.flat ? 'transparent' : Theme.surfaceElevated;
        }
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : root.flat ? 0 : 1
        border.color: root.activeFocus ? Theme.focusRing : root.highlighted ? Theme.accent : root.danger ? Theme.danger : Theme.borderStrong

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: Theme.radiusSm
            anchors.rightMargin: Theme.radiusSm
            height: root.highlighted || root.danger || root.flat ? 0 : 1
            color: Theme.borderStrong
            opacity: root.enabled && !root.down ? 0.65 : 0
        }

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
