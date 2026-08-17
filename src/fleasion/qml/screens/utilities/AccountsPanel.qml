import QtQuick
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

    AccountList {
        id: accountList

        Layout.fillWidth: true
        controller: root.controller
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    AccountCredentialEditor {
        Layout.fillWidth: true
        controller: root.controller
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    AccountLaunchOptions {
        Layout.fillWidth: true
        controller: root.controller
        accountIndex: accountList.currentIndex
    }
}
