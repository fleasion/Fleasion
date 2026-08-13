import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AbstractButton {
    id: root

    property string iconText: ''
    property bool selected: false
    property string badgeText: ''

    implicitWidth: Theme.navigationWidth - Theme.spaceMd * 2
    implicitHeight: Theme.minimumTouchTarget
    padding: Theme.spaceSm
    spacing: Theme.spaceSm
    hoverEnabled: true
    activeFocusOnTab: true
    checkable: true
    checked: selected
    Accessible.name: text
    Accessible.description: selected ? qsTr('Current page') : ''

    contentItem: RowLayout {
        spacing: root.spacing

        Label {
            visible: root.iconText.length > 0
            text: root.iconText
            color: root.selected ? Theme.accent : Theme.textSecondary
            font.pointSize: TypeScale.body
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Layout.preferredWidth: 24
            Accessible.ignored: true
        }

        Label {
            text: root.text
            color: root.enabled ? Theme.textPrimary : Theme.textDisabled
            font.pointSize: TypeScale.body
            font.weight: root.selected ? TypeScale.semibold : TypeScale.regular
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            Layout.fillWidth: true
            Accessible.ignored: true
        }

        Label {
            visible: root.badgeText.length > 0
            text: root.badgeText
            color: root.selected ? Theme.accent : Theme.textSecondary
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.medium
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            leftPadding: Theme.spaceXs
            rightPadding: Theme.spaceXs
            Accessible.ignored: true

            background: Rectangle {
                color: root.selected ? Theme.accentSubtle : Theme.surfacePressed
                radius: Theme.radiusPill
            }
        }
    }

    background: Rectangle {
        color: {
            if (root.down)
                return Theme.surfacePressed;

            if (root.selected)
                return Theme.accentSubtle;

            if (root.hovered)
                return Theme.surfaceHover;

            return 'transparent';
        }
        radius: Theme.radiusMd
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing

        Rectangle {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 3
            height: 20
            visible: root.selected
            color: Theme.accent
            radius: 2
            Accessible.ignored: true
        }

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
