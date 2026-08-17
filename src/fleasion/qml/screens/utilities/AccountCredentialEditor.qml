import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

ColumnLayout {
    id: root

    property var controller
    readonly property bool busy: Boolean(controller) && controller.accountTask.busy

    spacing: Theme.spaceXs

    FluentTextArea {
        id: cookieInput

        Layout.fillWidth: true
        Layout.preferredHeight: 64
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
            compact: true
            enabled: Boolean(root.controller) && !root.busy
            onClicked: {
                if (root.controller.addAccount(cookieInput.text))
                    cookieInput.clear();
            }
        }

        FluentButton {
            text: qsTr("Import browser login")
            compact: true
            enabled: Boolean(root.controller) && !root.busy
            onClicked: root.controller.importBrowserAccount()
        }

        Item {
            Layout.fillWidth: true
        }

        Label {
            visible: root.busy
            text: root.controller ? root.controller.accountTask.message : ""
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            elide: Text.ElideRight
        }
    }
}
