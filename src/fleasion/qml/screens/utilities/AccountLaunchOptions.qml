import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

ColumnLayout {
    id: root

    property var controller
    property int accountIndex: -1
    readonly property bool busy: Boolean(controller) && controller.launchTask.busy

    function launchSelected() {
        if (root.controller && root.accountIndex >= 0)
            root.controller.launchAccount(root.accountIndex, targetField.text, jobField.text, subplaceField.text);
    }

    spacing: Theme.spaceXs

    Label {
        Layout.fillWidth: true
        text: qsTr("Launch the selected account directly, or target a game, private server, shared server, subplace, or Job ID.")
        color: Theme.textSecondary
        wrapMode: Text.Wrap
        font.pointSize: TypeScale.label
    }

    FluentTextField {
        id: targetField

        Layout.fillWidth: true
        placeholderText: qsTr("Place ID or Roblox game/private/share link (optional)")
        Accessible.description: qsTr("Main experience or private server. This is the root experience when a subplace is entered.")
        onAccepted: root.launchSelected()
    }

    GridLayout {
        Layout.fillWidth: true
        columns: width >= 520 ? 2 : 1
        columnSpacing: Theme.spaceXs
        rowSpacing: Theme.spaceXs

        FluentTextField {
            id: subplaceField

            Layout.fillWidth: true
            placeholderText: qsTr("Subplace ID or game URL (optional)")
            Accessible.description: qsTr("Launch this subplace after priming the main experience.")
            onAccepted: root.launchSelected()
        }

        FluentTextField {
            id: jobField

            Layout.fillWidth: true
            placeholderText: qsTr("Server Job ID (optional)")
            Accessible.description: qsTr("Join a specific public server for the selected account.")
            onAccepted: root.launchSelected()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentButton {
            text: qsTr("Launch selected")
            highlighted: true
            compact: true
            enabled: Boolean(root.controller) && root.accountIndex >= 0 && !root.busy
            onClicked: root.launchSelected()
        }

        FluentButton {
            text: qsTr("Use in Roblox")
            compact: true
            enabled: Boolean(root.controller) && root.accountIndex >= 0 && !root.busy
            onClicked: root.controller.switchToAccount(root.accountIndex)
        }

        FluentButton {
            visible: root.busy
            text: qsTr("Cancel")
            flat: true
            compact: true
            onClicked: root.controller.cancelAccountLaunch()
        }

        Item {
            Layout.fillWidth: true
        }

        Label {
            Layout.maximumWidth: Math.max(120, root.width * 0.42)
            visible: root.busy
            text: root.controller ? root.controller.launchTask.message : ""
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            elide: Text.ElideRight
        }
    }
}
