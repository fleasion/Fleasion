pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property var controller
    readonly property var sourceValues: ["", "manual", "Chrome", "Safari", "Firefox", "Brave", "Edge", "Chromium", "Opera", "Vivaldi"]

    function sourceIndex(value) {
        return Math.max(0, sourceValues.indexOf(value));
    }

    Layout.fillWidth: true
    visible: controller.supportsBrowserAuthSource
    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr("Roblox login")
    subtitle: qsTr("Choose the macOS browser Fleasion may read when an authenticated Roblox request is needed.")

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Login source")
        description: qsTr("Browser access remains local. Manual tokens are encrypted before storage.")
        iconText: "◎"

        FluentComboBox {
            id: sourcePicker

            model: [qsTr("Choose when needed"), qsTr("Encrypted manual token"), "Chrome", "Safari", "Firefox", "Brave", "Edge", "Chromium", "Opera", "Vivaldi"]
            currentIndex: root.sourceIndex(root.controller.macosAuthSource)
            Accessible.name: qsTr("Roblox login source")
            onActivated: index => root.controller.macosAuthSource = root.sourceValues[index]
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: sourcePicker.currentIndex === 1
        spacing: Theme.spaceSm

        FluentTextField {
            id: tokenField

            Layout.fillWidth: true
            placeholderText: qsTr(".ROBLOSECURITY token")
            echoMode: TextInput.Password
            enabled: !root.controller.authTask.busy
            Accessible.name: qsTr("Manual Roblox login token")
        }

        FluentButton {
            text: root.controller.authTask.busy ? qsTr("Validating…") : qsTr("Validate and store")
            highlighted: true
            enabled: tokenField.text.trim().length > 0 && !root.controller.authTask.busy
            onClicked: {
                if (root.controller.importManualToken(tokenField.text))
                    tokenField.clear();
            }
        }
    }

    Label {
        Layout.fillWidth: true
        visible: root.controller.authStatus.length > 0
        text: root.controller.authStatus
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }
}
