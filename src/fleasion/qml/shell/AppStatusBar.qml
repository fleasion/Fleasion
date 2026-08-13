import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property var appController

    implicitHeight: 44
    color: Theme.surface
    border.width: 0
    Accessible.role: Accessible.StatusBar
    Accessible.name: qsTr("Application status")

    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Theme.border
        Accessible.ignored: true
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceMd
        anchors.rightMargin: Theme.spaceMd
        spacing: Theme.spaceMd

        StatusPill {
            text: root.appController ? root.appController.proxy.statusText : qsTr("Starting")
            status: root.appController && root.appController.proxy.running ? "success" : "neutral"
            Accessible.name: qsTr("Proxy status: %1").arg(text)
        }

        Label {
            visible: root.width >= 640
            text: root.appController ? qsTr("%1 cached · %2").arg(root.appController.cache.totalAssets).arg(root.appController.cache.totalSizeText) : qsTr("Loading cache…")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            text: qsTr("Clear cache")
            compact: true
            flat: true
            enabled: Boolean(root.appController)
            Accessible.description: qsTr("Open the cache cleanup workflow")
            onClicked: root.appController.cacheCleanupRequested()
        }

        Label {
            visible: root.width >= 760
            text: root.appController ? qsTr("%1 · v%2").arg(root.appController.platformName).arg(root.appController.version) : ""
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
        }
    }
}
