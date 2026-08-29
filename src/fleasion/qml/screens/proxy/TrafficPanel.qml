pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "../../dialogs" as Dialogs

Card {
    id: root

    property var controller
    signal inspectRequested(string entryKey)

    title: qsTr("Live traffic")
    subtitle: root.controller && root.controller.model ? qsTr("%n captured request(s)", "", root.controller.model.count) : qsTr("No traffic source")
    flat: true
    padding: 0
    contentSpacing: Theme.spaceXs

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: livePrivacyWarning.implicitHeight + Theme.spaceMd
        radius: Theme.radiusSm
        color: Theme.warningSubtle

        Text {
            id: livePrivacyWarning

            anchors.fill: parent
            anchors.margins: Theme.spaceXs
            text: root.controller ? root.controller.trafficPrivacyWarning : ""
            color: Theme.warning
            font.pointSize: TypeScale.caption
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        SearchBox {
            id: trafficSearch

            Layout.fillWidth: true
            placeholderText: qsTr("Search ID, method, host, path, or status")
            accessibleName: qsTr("Search proxy traffic")
            text: root.controller ? root.controller.query : ""
            onTextEdited: {
                if (root.controller)
                    root.controller.query = text;
            }
            onCleared: {
                if (root.controller)
                    root.controller.query = "";
            }
        }

        FluentButton {
            text: qsTr("Forward all held")
            visible: Boolean(root.controller) && root.controller.pendingCount > 0
            onClicked: root.controller.resolveAll("forward")
        }

        FluentButton {
            text: qsTr("Drop all held")
            visible: Boolean(root.controller) && root.controller.pendingCount > 0
            onClicked: bulkDropLoader.active = true
        }

        TrafficPreserveControl {
            controller: root.controller
        }

        FluentButton {
            text: qsTr("Clear")
            enabled: Boolean(root.controller && root.controller.model && root.controller.model.count > 0)
            onClicked: root.controller.clear()
        }
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 220

        ListView {
            id: trafficList

            anchors.fill: parent
            clip: true
            spacing: Theme.spaceXxs
            model: root.controller ? root.controller.model : null
            reuseItems: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: FluentScrollBar {}

            delegate: ProxyTrafficDelegate {
                required property string key
                required property string path
                required property string status

                width: ListView.view.width
                entryKey: key
                pathText: path
                statusText: status
                onActivated: entryKey => root.inspectRequested(entryKey)
            }
        }

        EmptyState {
            anchors.fill: parent
            visible: !root.controller || !root.controller.model || root.controller.model.count === 0
            iconText: "⇄"
            title: trafficSearch.text.length > 0 ? qsTr("No matching traffic") : qsTr("No traffic yet")
            description: trafficSearch.text.length > 0 ? qsTr("Try a broader search phrase.") : qsTr("Captured requests appear here while the proxy is running.")
        }
    }

    Loader {
        id: bulkDropLoader

        active: false
        sourceComponent: Component {
            Dialogs.ConfirmDialog {
                heading: qsTr("Drop all held traffic?")
                message: qsTr("Every request and response currently waiting for a decision will be discarded.")
                details: qsTr("This can interrupt active Roblox network operations and cannot be undone.")
                acceptText: qsTr("Drop all")
                destructive: true
                onConfirmed: root.controller.resolveAll("drop")
                onClosed: bulkDropLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.ConfirmDialog).open();
        }
    }
}
