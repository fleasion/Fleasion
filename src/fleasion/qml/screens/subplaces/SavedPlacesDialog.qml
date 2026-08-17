pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    signal renameRequested(string placeId, string name)

    title: qsTr("Saved places")
    anchors.centerIn: parent
    width: Math.min(560, parent ? parent.width - Theme.spaceLg : 560)
    height: Math.min(620, parent ? parent.height - Theme.spaceLg : 620)
    modal: true
    focus: true
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.sectionGap

        SavedPlacesPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180
            heading: qsTr("Favorites")
            savedModel: root.controller ? root.controller.favoritesModel : null
            controller: root.controller
            favoriteEntries: true
            onPlaceRequested: placeId => {
                root.controller.usePlace(placeId);
                root.close();
            }
            onRenameRequested: (placeId, name) => root.renameRequested(placeId, name)
        }

        SavedPlacesPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180
            heading: qsTr("Recent searches")
            savedModel: root.controller ? root.controller.recentModel : null
            controller: root.controller
            allowRemove: true
            onPlaceRequested: placeId => {
                root.controller.usePlace(placeId);
                root.close();
            }
            onRenameRequested: (placeId, name) => root.renameRequested(placeId, name)
        }

        RowLayout {
            Layout.fillWidth: true

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Close")
                onClicked: root.close()
            }
        }
    }
}
