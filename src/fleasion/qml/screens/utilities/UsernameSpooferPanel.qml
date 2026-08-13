import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "../components"

Card {
    id: root

    property var controller

    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr("Username spoofer")
    subtitle: qsTr("Client-side only: changes are visible to you while proxy interception is active.")

    SettingSwitchRow {
        Layout.fillWidth: true
        title: qsTr("Save these settings")
        description: qsTr("Restore this spoofer configuration on the next launch.")
        checked: root.controller ? root.controller.saveUsernameSettings : false
        onToggled: value => root.controller.saveUsernameSettings = value
    }

    Label {
        text: qsTr("Other players")
        color: Theme.textPrimary
        font.pointSize: TypeScale.label
        font.weight: TypeScale.semibold
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentTextField {
            Layout.fillWidth: true
            text: root.controller ? root.controller.othersName : ""
            placeholderText: qsTr("Spoofed username")
            onEditingFinished: root.controller.othersName = text
        }

        FluentCheckBox {
            text: qsTr("Apply in game")
            checked: root.controller ? root.controller.othersApplyInGame : false
            onToggled: root.controller.othersApplyInGame = checked
        }

        FluentCheckBox {
            text: qsTr("Verified")
            checked: root.controller ? root.controller.othersVerified : false
            onToggled: root.controller.othersVerified = checked
        }
    }

    Label {
        text: qsTr("Your account")
        color: Theme.textPrimary
        font.pointSize: TypeScale.label
        font.weight: TypeScale.semibold
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentTextField {
            Layout.fillWidth: true
            text: root.controller ? root.controller.selfName : ""
            placeholderText: qsTr("Spoofed username")
            onEditingFinished: root.controller.selfName = text
        }

        FluentCheckBox {
            text: qsTr("Apply in game")
            checked: root.controller ? root.controller.selfApplyInGame : false
            onToggled: root.controller.selfApplyInGame = checked
        }

        FluentCheckBox {
            text: qsTr("Verified")
            checked: root.controller ? root.controller.selfVerified : false
            onToggled: root.controller.selfVerified = checked
        }
    }

    SettingSwitchRow {
        Layout.fillWidth: true
        title: qsTr("Show me as the game creator")
        description: qsTr("Rewrite creator metadata in intercepted game-join responses.")
        checked: root.controller ? root.controller.selfGameCreator : false
        onToggled: value => root.controller.selfGameCreator = value
    }
}
