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
    title: qsTr("Reserved server rejoin")
    subtitle: qsTr("Fleasion captures reserved-server credentials from proxied Roblox join traffic. You can also enter them manually.")

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXs

        FluentTextField {
            Layout.fillWidth: true
            text: root.controller ? root.controller.reservedPlaceId : ""
            placeholderText: qsTr("Reserved place ID")
            inputMethodHints: Qt.ImhDigitsOnly
            onEditingFinished: if (root.controller)
                root.controller.reservedPlaceId = text
        }

        FluentTextField {
            Layout.fillWidth: true
            text: root.controller ? root.controller.reservedAccessCode : ""
            placeholderText: qsTr("Access code")
            onEditingFinished: if (root.controller)
                root.controller.reservedAccessCode = text
        }
    }

    RowLayout {
        Layout.fillWidth: true

        StatusPill {
            text: root.controller && root.controller.rejoinAvailable ? qsTr("Valid for %1:%2").arg(Math.floor(root.controller.rejoinSecondsRemaining / 60)).arg(String(root.controller.rejoinSecondsRemaining % 60).padStart(2, "0")) : qsTr("Waiting for capture")
            status: root.controller && root.controller.rejoinAvailable ? "success" : "info"
        }

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            text: qsTr("Rejoin reserved server")
            highlighted: true
            enabled: Boolean(root.controller) && root.controller.rejoinAvailable
            onClicked: root.controller.rejoinReservedServer()
        }
    }
}
