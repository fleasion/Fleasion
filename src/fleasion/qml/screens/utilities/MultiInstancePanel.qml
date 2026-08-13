import QtQuick
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme
import "../components"

Card {
    id: root

    property var controller

    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr("Multi-instance launching")
    subtitle: qsTr("Allow more than one Roblox Player process on Windows.")

    SettingSwitchRow {
        Layout.fillWidth: true
        title: qsTr("Enable multi-instance watcher")
        description: root.controller && root.controller.supportsMultiInstance ? qsTr("Fleasion removes the Roblox singleton event when multiple clients launch.") : qsTr("This operating-system feature is available on Windows only.")
        available: root.controller ? root.controller.supportsMultiInstance : false
        checked: root.controller ? root.controller.multiInstanceEnabled : false
        onToggled: value => root.controller.multiInstanceEnabled = value
    }
}
