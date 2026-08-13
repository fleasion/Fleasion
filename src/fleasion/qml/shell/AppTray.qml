import QtQuick
import Qt.labs.platform as Platform

Item {
    id: root

    required property var appController
    readonly property bool available: tray.available
    signal dashboardRequested
    signal aboutRequested

    Platform.SystemTrayIcon {
        id: tray

        visible: true
        icon.source: root.appController.iconUrl
        tooltip: qsTr("Fleasion · Roblox asset tools")

        menu: Platform.Menu {
            Platform.MenuItem {
                text: qsTr("Open dashboard")
                onTriggered: root.dashboardRequested()
            }

            Platform.MenuSeparator {}

            Platform.MenuItem {
                text: qsTr("Clear Roblox cache")
                onTriggered: root.appController.cacheCleanupRequested()
            }

            Platform.MenuItem {
                text: qsTr("Open logs")
                onTriggered: {
                    root.appController.pageRequested("logs");
                    root.dashboardRequested();
                }
            }

            Platform.MenuItem {
                text: qsTr("Settings")
                onTriggered: {
                    root.appController.pageRequested("settings");
                    root.dashboardRequested();
                }
            }

            Platform.MenuItem {
                text: qsTr("About Fleasion")
                onTriggered: root.aboutRequested()
            }

            Platform.MenuSeparator {}

            Platform.MenuItem {
                text: qsTr("Exit Fleasion")
                onTriggered: root.appController.quitRequested()
            }
        }

        onActivated: reason => {
            if (reason === Platform.SystemTrayIcon.Trigger || reason === Platform.SystemTrayIcon.DoubleClick)
                root.dashboardRequested();
        }
    }
}
