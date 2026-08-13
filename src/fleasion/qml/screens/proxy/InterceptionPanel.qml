import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    property var controller

    title: qsTr("Interactive interception")
    subtitle: qsTr("Capture API traffic, then pause matching requests so their wire content can be reviewed or edited.")
    flat: true
    padding: Theme.spaceXs
    contentSpacing: Theme.spaceXs

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        FluentTextField {
            id: matchField

            Layout.fillWidth: true
            text: root.controller ? root.controller.interceptMatch : ""
            placeholderText: qsTr("Pause when host or path contains…")
            selectByMouse: true
            Accessible.name: qsTr("Interactive interception match")
            onAccepted: applyButton.clicked()
        }

        FluentButton {
            id: applyButton

            text: matchField.text.trim().length > 0 ? qsTr("Arm") : qsTr("Disarm")
            enabled: Boolean(root.controller)
            highlighted: matchField.text.trim().length > 0
            onClicked: root.controller.setInterceptMatch(matchField.text)
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        FluentSwitch {
            id: captureSwitch

            text: qsTr("Capture additional API hosts")
            Accessible.description: qsTr("Decrypt and log Roblox API hosts beyond those already used by Fleasion features")
            onToggled: {
                if (root.controller && checked !== root.controller.captureAllHosts)
                    root.controller.setCaptureAllHosts(checked);
            }
        }

        Binding {
            target: captureSwitch
            property: "checked"
            value: root.controller ? root.controller.captureAllHosts : false
        }

        Item {
            Layout.fillWidth: true
        }

        StatusPill {
            visible: Boolean(root.controller) && root.controller.pendingCount > 0
            text: qsTr("%n held", "", root.controller ? root.controller.pendingCount : 0)
            status: "warning"
        }
    }

    Label {
        Layout.fillWidth: true
        visible: matchField.text.trim().length > 0 && !captureSwitch.checked
        text: qsTr("Enable additional API capture to make matching requests visible and hold them for editing.")
        color: Theme.warning
        font.pointSize: TypeScale.caption
        wrapMode: Text.Wrap
    }
}
