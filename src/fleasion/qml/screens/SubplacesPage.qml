pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "subplaces"

Rectangle {
    id: root

    property var controller
    property var appController
    property string pendingServerPlaceId
    property string pendingServerPlaceName
    color: Theme.surface

    function openServers(placeId, placeName) {
        pendingServerPlaceId = placeId;
        pendingServerPlaceName = placeName;
        if (controller.openServers(placeId, placeName))
            serversLoader.active = true;
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pageGutter
        anchors.rightMargin: Theme.pageGutter
        anchors.topMargin: Theme.pageTopGutter
        anchors.bottomMargin: Theme.pageBottomGutter
        spacing: Theme.sectionGap

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Subplace explorer")
            subtitle: qsTr("Browse every place in an experience and open it directly in Roblox.")
            iconText: "◎"

            StatusPill {
                text: root.controller && root.controller.task.busy ? qsTr("Searching") : qsTr("Ready")
                status: root.controller && root.controller.task.busy ? "info" : "success"
            }
        }

        Card {
            Layout.fillWidth: true
            flat: true
            padding: Theme.panelPadding
            topPadding: Theme.spaceXs
            bottomPadding: Theme.spaceXs
            contentSpacing: Theme.spaceXs

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                FluentTextField {
                    id: placeInput

                    Layout.fillWidth: true
                    placeholderText: qsTr("Place ID or Roblox experience URL")
                    Accessible.name: qsTr("Experience place ID")
                    onAccepted: if (root.controller)
                        root.controller.search(text)
                }

                FluentButton {
                    text: qsTr("Explore")
                    highlighted: true
                    enabled: Boolean(root.controller) && !root.controller.task.busy
                    onClicked: root.controller.search(placeInput.text)
                }

                FluentButton {
                    text: root.controller && root.controller.currentIsFavorite ? qsTr("Unfavorite") : qsTr("Favorite")
                    enabled: Boolean(root.controller) && root.controller.currentPlaceId.length > 0
                    onClicked: root.controller.toggleFavorite(root.controller.currentPlaceId)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                SearchBox {
                    Layout.fillWidth: true
                    enabled: Boolean(root.controller)
                    placeholderText: qsTr("Filter places by name or ID")
                    accessibleName: qsTr("Filter discovered places")
                    onTextChanged: if (root.controller)
                        root.controller.query = text
                }

                FluentComboBox {
                    id: sortBox

                    textRole: "label"
                    valueRole: "value"
                    model: [
                        {
                            "label": qsTr("Root place first"),
                            "value": "rootFirst"
                        },
                        {
                            "label": qsTr("Name"),
                            "value": "name"
                        },
                        {
                            "label": qsTr("Place ID ascending"),
                            "value": "idAscending"
                        },
                        {
                            "label": qsTr("Place ID descending"),
                            "value": "idDescending"
                        }
                    ]
                    onActivated: if (root.controller)
                        root.controller.sortMode = currentValue
                }
            }

            Label {
                Layout.fillWidth: true
                visible: Boolean(root.controller) && root.controller.task.busy
                text: root.controller ? root.controller.task.message : ""
                color: Theme.textSecondary
                font.pointSize: TypeScale.body
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceSm

            ColumnLayout {
                Layout.preferredWidth: 220
                Layout.fillHeight: true
                visible: root.width >= 940
                spacing: Theme.spaceSm

                SavedPlacesPanel {
                    Layout.fillWidth: true
                    heading: qsTr("Favorites")
                    savedModel: root.controller ? root.controller.favoritesModel : null
                    controller: root.controller
                }

                SavedPlacesPanel {
                    Layout.fillWidth: true
                    heading: qsTr("Recent searches")
                    savedModel: root.controller ? root.controller.recentModel : null
                    controller: root.controller
                    allowRemove: true
                }

                Item {
                    Layout.fillHeight: true
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 320

                ListView {
                    id: resultsView

                    anchors.fill: parent
                    model: root.controller ? root.controller.model : null
                    spacing: Theme.spaceXs
                    clip: true
                    reuseItems: true
                    boundsBehavior: Flickable.StopAtBounds

                    delegate: SubplaceCard {
                        required property string placeId
                        required property string rootPlaceId
                        required property string name
                        required property string thumbnailUrl
                        required property bool isRoot
                        required property bool isFavorite

                        width: ListView.view.width
                        placeName: name
                        rootPlace: isRoot
                        favorite: isFavorite
                        universeRootPlaceId: rootPlaceId
                        controller: root.controller
                        onFavoriteRequested: value => root.controller.toggleFavorite(value)
                        onServersRequested: (placeId, placeName) => root.openServers(placeId, placeName)
                    }

                    ScrollBar.vertical: ScrollBar {}
                }

                EmptyState {
                    anchors.fill: parent
                    visible: resultsView.count === 0 && !(root.controller && root.controller.task.busy)
                    iconText: root.controller && root.controller.currentPlaceId.length > 0 ? "⌕" : "◇"
                    title: root.controller && root.controller.currentPlaceId.length > 0 ? qsTr("No matching places") : qsTr("Explore an experience")
                    description: root.controller && root.controller.currentPlaceId.length > 0 ? qsTr("Try a broader name or place ID filter.") : qsTr("Enter an experience above to discover every place it contains.")
                }
            }
        }
    }

    Loader {
        id: serversLoader

        anchors.fill: parent
        active: false
        sourceComponent: Component {
            PublicServersDialog {
                controller: root.controller
                appController: root.appController
                onClosed: serversLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as PublicServersDialog).open();
        }
    }
}
