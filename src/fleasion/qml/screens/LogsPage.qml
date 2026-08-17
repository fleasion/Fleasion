pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "components"

Rectangle {
    id: root

    property var controller
    property var appController
    color: Theme.surface

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pageGutter
        anchors.rightMargin: Theme.pageGutter
        anchors.topMargin: Theme.pageTopGutter
        anchors.bottomMargin: Theme.pageBottomGutter
        spacing: Theme.sectionGap

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Activity logs")
            subtitle: qsTr("Review runtime events and copy diagnostic details.")
            iconText: "☷"

            FluentButton {
                text: qsTr("Open logs folder")
                enabled: Boolean(root.appController)
                onClicked: root.appController.openLogsFolder()
            }

            IconButton {
                iconText: "↻"
                text: qsTr("Refresh logs")
                enabled: Boolean(root.controller)
                onClicked: root.controller.refresh()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            SearchBox {
                id: logSearch

                Layout.fillWidth: true
                placeholderText: qsTr("Search messages or categories")
                accessibleName: qsTr("Search activity logs")
            }

            StatusPill {
                text: root.controller && root.controller.model ? qsTr("%n event(s)", "", root.controller.model.count) : qsTr("Unavailable")
                status: root.controller ? "info" : "warning"
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceXxs

            SectionHeader {
                Layout.fillWidth: true
                title: qsTr("Session events")
                subtitle: qsTr("Newest runtime messages are collected by the application log bridge.")
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 300

                ListView {
                    anchors.fill: parent
                    clip: true
                    spacing: 0
                    model: root.controller ? root.controller.model : null
                    reuseItems: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: FluentScrollBar {}

                    delegate: LogDelegate {
                        width: ListView.view.width
                        query: logSearch.text.trim()
                        onCopyRequested: value => {
                            if (root.appController)
                                root.appController.copyText(value);
                        }
                    }
                }

                EmptyState {
                    anchors.fill: parent
                    visible: !root.controller || !root.controller.model || root.controller.model.count === 0
                    iconText: "☷"
                    title: qsTr("No events recorded")
                    description: qsTr("Runtime messages will appear here as you use Fleasion.")
                }
            }
        }
    }
}
