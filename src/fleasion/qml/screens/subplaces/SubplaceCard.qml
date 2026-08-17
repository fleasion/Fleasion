pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property string placeId
    required property string universeRootPlaceId
    required property string placeName
    required property string thumbnailUrl
    required property bool rootPlace
    required property bool favorite
    property var controller

    signal favoriteRequested(string placeId)
    signal serversRequested(string placeId, string placeName)

    Layout.fillWidth: true
    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXxs

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        Rectangle {
            Layout.preferredWidth: 88
            Layout.preferredHeight: 88
            radius: Theme.radiusMd
            color: Theme.surfaceSubtle
            border.width: 1
            border.color: Theme.border

            Image {
                id: thumbnail

                anchors.fill: parent
                anchors.margins: 1
                source: root.thumbnailUrl
                sourceSize.width: 176
                sourceSize.height: 176
                asynchronous: true
                fillMode: Image.PreserveAspectCrop
                visible: status === Image.Ready
                Accessible.name: qsTr("Thumbnail for %1").arg(root.placeName)
            }

            Label {
                anchors.centerIn: parent
                visible: thumbnail.status !== Image.Ready
                text: "◇"
                color: Theme.textTertiary
                font.pointSize: TypeScale.title
                Accessible.ignored: true
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXxs

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                Label {
                    Layout.fillWidth: true
                    text: root.placeName
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.subtitle
                    font.weight: TypeScale.semibold
                    elide: Text.ElideRight
                }

                StatusPill {
                    visible: root.rootPlace
                    text: qsTr("Root place")
                    status: "info"
                }
            }

            Label {
                text: qsTr("Place ID %1").arg(root.placeId)
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                FluentTextField {
                    id: jobIdField

                    Layout.fillWidth: true
                    placeholderText: qsTr("Job ID (optional)")
                    Accessible.name: qsTr("Optional server job ID for %1").arg(root.placeName)
                }

                FluentButton {
                    text: qsTr("Join")
                    compact: true
                    highlighted: true
                    enabled: Boolean(root.controller) && !root.controller.launchTask.busy
                    onClicked: root.controller.launch(root.placeId, jobIdField.text, root.universeRootPlaceId)
                }

                FluentButton {
                    text: qsTr("Browser")
                    compact: true
                    enabled: Boolean(root.controller)
                    onClicked: root.controller.openBrowser(root.placeId)
                }

                FluentButton {
                    text: qsTr("Servers")
                    compact: true
                    enabled: Boolean(root.controller)
                    onClicked: root.serversRequested(root.placeId, root.placeName)
                }

                FluentButton {
                    text: root.favorite ? qsTr("Unfavorite") : qsTr("Favorite")
                    compact: true
                    enabled: Boolean(root.controller)
                    onClicked: root.favoriteRequested(root.placeId)
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }
}
