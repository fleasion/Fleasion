import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

RowLayout {
    id: root

    property var controller
    signal closeRequested

    spacing: Theme.spaceXs

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceXxs

        Label {
            Layout.fillWidth: true
            text: qsTr("Public servers · %1").arg(root.controller ? root.controller.serverPlaceName : "")
            color: Theme.textPrimary
            font.pointSize: TypeScale.title
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Place ID %1").arg(root.controller ? root.controller.serverPlaceId : "")
            color: Theme.textSecondary
            font.pointSize: TypeScale.caption
            elide: Text.ElideRight
        }
    }

    StatusPill {
        text: root.controller && root.controller.serverTask.busy ? qsTr("Loading") : qsTr("%n server(s)", "", root.controller ? root.controller.serverCount : 0)
        status: root.controller && root.controller.serverTask.busy ? "info" : "neutral"
    }

    IconButton {
        iconText: "↻"
        text: qsTr("Refresh public servers")
        enabled: Boolean(root.controller) && !root.controller.serverTask.busy
        onClicked: root.controller.refreshServers()
    }

    IconButton {
        iconText: "×"
        text: qsTr("Close public servers")
        onClicked: root.closeRequested()
    }
}
