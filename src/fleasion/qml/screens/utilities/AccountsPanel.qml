pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    property var controller

    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr("Account launcher")
    subtitle: qsTr("Cookies are encrypted before they are written to disk.")

    Label {
        Layout.fillWidth: true
        text: root.controller && root.controller.selectedUsername.length > 0 ? qsTr("Selected: %1").arg(root.controller.selectedUsername) : qsTr("No account selected")
        color: Theme.textSecondary
        font.pointSize: TypeScale.label
    }

    ListView {
        id: accountList

        Layout.fillWidth: true
        Layout.preferredHeight: count > 0 ? Math.min(140, Math.max(52, contentHeight)) : 0
        visible: count > 0
        model: root.controller ? root.controller.accountsModel : null
        spacing: Theme.spaceXxs
        reuseItems: true

        delegate: FluentItemDelegate {
            id: accountDelegate

            required property int index
            required property string username
            required property string userId
            required property string status
            required property string statusText

            width: ListView.view.width
            highlighted: root.controller && root.controller.selectedUsername === username
            onClicked: root.controller.selectAccount(index)

            contentItem: RowLayout {
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXxs

                    Label {
                        text: accountDelegate.username
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.body
                        font.weight: TypeScale.medium
                    }

                    Label {
                        text: accountDelegate.userId.length > 0 ? qsTr("%1 · User %2").arg(accountDelegate.statusText).arg(accountDelegate.userId) : accountDelegate.statusText
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.caption
                    }
                }

                StatusPill {
                    text: accountDelegate.statusText
                    status: accountDelegate.status === "expired" ? "warning" : "success"
                }

                FluentButton {
                    text: qsTr("Remove")
                    flat: true
                    onClicked: root.controller.removeAccount(accountDelegate.index)
                }
            }
        }
    }

    FluentTextArea {
        id: cookieInput

        Layout.fillWidth: true
        Layout.preferredHeight: 68
        placeholderText: qsTr("Paste .ROBLOSECURITY cookie")
        wrapMode: TextEdit.WrapAnywhere
        Accessible.description: qsTr("This token grants access to your Roblox account. Never share it.")
    }

    Label {
        Layout.fillWidth: true
        text: qsTr("Treat this token like a password. Fleasion validates it with Roblox, then stores only an encrypted copy.")
        color: Theme.warning
        wrapMode: Text.Wrap
        font.pointSize: TypeScale.label
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentButton {
            text: qsTr("Add securely")
            highlighted: true
            enabled: Boolean(root.controller) && !root.controller.accountTask.busy
            onClicked: {
                if (root.controller.addAccount(cookieInput.text))
                    cookieInput.clear();
            }
        }

        FluentButton {
            text: qsTr("Import browser login")
            enabled: Boolean(root.controller) && !root.controller.accountTask.busy
            onClicked: root.controller.importBrowserAccount()
        }

        Item {
            Layout.fillWidth: true
        }

        Label {
            visible: root.controller && root.controller.accountTask.busy
            text: root.controller ? root.controller.accountTask.message : ""
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentTextField {
            id: placeField

            Layout.fillWidth: true
            placeholderText: qsTr("Place ID (optional)")
            inputMethodHints: Qt.ImhDigitsOnly
        }

        FluentTextField {
            id: jobField

            Layout.fillWidth: true
            placeholderText: qsTr("Job ID (optional)")
        }

        FluentButton {
            text: qsTr("Launch selected")
            highlighted: true
            enabled: Boolean(root.controller) && accountList.currentIndex >= 0 && !root.controller.launchTask.busy
            onClicked: root.controller.launchAccount(accountList.currentIndex, placeField.text, jobField.text)
        }

        FluentButton {
            text: qsTr("Use in Roblox")
            enabled: Boolean(root.controller) && accountList.currentIndex >= 0
            onClicked: root.controller.switchToAccount(accountList.currentIndex)
        }
    }
}
