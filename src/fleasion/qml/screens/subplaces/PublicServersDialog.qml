pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    required property var appController

    width: Math.min(760, parent ? parent.width - Theme.spaceXl : 760)
    height: Math.min(680, parent ? parent.height - Theme.spaceXl : 680)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton

    header: PublicServersToolbar {
        controller: root.controller
        onCloseRequested: root.close()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            Label {
                text: qsTr("Sort by")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentComboBox {
                id: sortBox

                textRole: "label"
                valueRole: "value"
                model: [
                    {
                        "label": qsTr("Players · fewest first"),
                        "value": "playersAscending"
                    },
                    {
                        "label": qsTr("Players · most first"),
                        "value": "playersDescending"
                    },
                    {
                        "label": qsTr("Ping · lowest first"),
                        "value": "pingAscending"
                    },
                    {
                        "label": qsTr("Ping · highest first"),
                        "value": "pingDescending"
                    }
                ]
                Accessible.name: qsTr("Public server sort order")
                enabled: !root.controller.serverTask.busy
                onActivated: root.controller.setServerSortMode(currentValue)
            }

            Item {
                Layout.fillWidth: true
            }

            Label {
                Layout.maximumWidth: 360
                text: root.controller.serverStatusText
                color: root.controller.serverError.length > 0 ? Theme.danger : Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignRight
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 300

            ListView {
                id: serverList

                anchors.fill: parent
                clip: true
                model: root.controller.serverModel
                spacing: 0
                reuseItems: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: FluentScrollBar {}

                delegate: PublicServerDelegate {
                    width: ListView.view.width
                    enabled: !root.controller.launchTask.busy
                    onCopyRequested: jobId => root.appController.copyText(jobId)
                    onJoinRequested: jobId => root.controller.joinServer(jobId)
                }
            }

            EmptyState {
                anchors.fill: parent
                visible: root.controller.serverCount === 0 && !root.controller.serverTask.busy
                iconText: root.controller.serverError.length > 0 ? "!" : "◎"
                title: root.controller.serverError.length > 0 ? qsTr("Public servers unavailable") : qsTr("No active public servers")
                description: root.controller.serverError.length > 0 ? root.controller.serverError : qsTr("Roblox did not report any active public servers for this place. A manually supplied Job ID can still be used from the place card.")
                actionText: qsTr("Retry")
                onActionTriggered: root.controller.refreshServers()
            }

            BusyIndicator {
                anchors.centerIn: parent
                visible: root.controller.serverTask.busy && root.controller.serverCount === 0
                running: visible
                Accessible.name: qsTr("Loading public servers")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: qsTr("Job IDs identify a specific running Roblox server and can disappear at any time.")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }

            FluentButton {
                text: qsTr("Load more")
                visible: root.controller.serverHasMore
                enabled: !root.controller.serverTask.busy
                onClicked: root.controller.loadMoreServers()
            }

            FluentButton {
                text: qsTr("Close")
                onClicked: root.close()
            }
        }
    }
}
