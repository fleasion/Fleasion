pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    required property string flagName
    required property string flagValue
    required property string family
    required property bool flagEnabled
    required property string keybind
    required property bool hasKeybind
    property bool hotkeysSupported: false

    signal enabledRequested(bool enabled)
    signal hotkeyRequested
    signal removeRequested

    width: ListView.view ? ListView.view.width : implicitWidth
    height: Theme.largeControlHeight + Theme.spaceXxs
    radius: Theme.radiusSm
    color: flagHover.hovered ? Theme.surfaceHover : "transparent"
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("%1, %2").arg(flagName).arg(flagEnabled ? qsTr("enabled") : qsTr("disabled"))

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceXs
        anchors.rightMargin: Theme.spaceXs
        spacing: Theme.spaceSm

        FluentSwitch {
            checked: root.flagEnabled
            Accessible.name: qsTr("Enable %1").arg(root.flagName)
            onToggled: root.enabledRequested(checked)
        }

        StatusPill {
            text: root.family
            status: root.flagEnabled ? "neutral" : "warning"
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Label {
                Layout.fillWidth: true
                text: root.flagName
                color: root.flagEnabled ? Theme.textPrimary : Theme.textSecondary
                font.pointSize: TypeScale.label
                font.family: "monospace"
                elide: Text.ElideMiddle
            }

            Label {
                Layout.fillWidth: true
                text: root.flagValue || qsTr("No value")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
                elide: Text.ElideRight
            }
        }

        FluentButton {
            visible: root.hotkeysSupported
            Layout.preferredWidth: 142
            text: root.hasKeybind ? root.keybind : qsTr("Set hotkey")
            compact: true
            onClicked: root.hotkeyRequested()
        }

        IconButton {
            iconText: "×"
            text: qsTr("Remove %1").arg(root.flagName)
            danger: true
            onClicked: root.removeRequested()
        }
    }

    HoverHandler {
        id: flagHover
    }
}
