import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    property var controller
    readonly property bool busy: Boolean(controller && controller.lifecycleTask && controller.lifecycleTask.busy)

    title: qsTr("Proxy service")
    subtitle: qsTr("Run the local service that powers interception, replacements, and traffic capture.")
    flat: true
    padding: Theme.spaceXs
    contentSpacing: Theme.spaceXs

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        StatusPill {
            text: root.controller ? root.controller.statusText : qsTr("Unavailable")
            status: root.controller && root.controller.running ? "success" : root.busy ? "info" : "warning"
        }

        BusyIndicator {
            visible: root.busy
            running: visible
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            Accessible.name: qsTr("Proxy operation in progress")
        }

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            text: root.controller && root.controller.running ? qsTr("Stop proxy") : qsTr("Start proxy")
            enabled: Boolean(root.controller) && !root.busy
            highlighted: Boolean(root.controller) && !root.controller.running
            onClicked: {
                if (root.controller.running)
                    root.controller.stop();
                else
                    root.controller.start();
            }
        }

        FluentButton {
            text: qsTr("Restart")
            enabled: Boolean(root.controller) && !root.busy
            onClicked: root.controller.restart()
        }
    }
}
