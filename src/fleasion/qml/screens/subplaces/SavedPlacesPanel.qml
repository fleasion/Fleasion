pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property string heading
    property var savedModel
    property var controller
    property bool allowRemove: false
    property bool favoriteEntries: false
    readonly property bool hasEntries: Boolean(savedModel) && savedModel.count > 0
    signal placeRequested(string placeId)
    signal renameRequested(string placeId, string name)

    title: heading
    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    Layout.minimumWidth: 210

    ListView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: root.hasEntries ? 120 : 0
        visible: root.hasEntries
        model: root.savedModel
        spacing: 0
        clip: true
        reuseItems: true
        boundsBehavior: Flickable.StopAtBounds

        delegate: FluentItemDelegate {
            id: savedDelegate

            required property string placeId
            required property string name

            width: ListView.view.width
            text: name
            Accessible.description: qsTr("Place ID %1").arg(placeId)
            onClicked: root.placeRequested(placeId)

            ToolTip.visible: hovered
            ToolTip.text: qsTr("Place ID %1").arg(placeId)

            contentItem: RowLayout {
                Label {
                    Layout.fillWidth: true
                    text: savedDelegate.name
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }

                Label {
                    text: savedDelegate.placeId
                    color: Theme.textTertiary
                    font.pointSize: TypeScale.caption
                }

                IconButton {
                    objectName: "renameSavedPlaceButton"
                    iconText: "✎"
                    text: qsTr("Rename")
                    flat: true
                    controlSize: 30
                    onClicked: root.renameRequested(savedDelegate.placeId, savedDelegate.name)
                }

                IconButton {
                    objectName: "removeSavedPlaceButton"
                    visible: root.allowRemove || root.favoriteEntries
                    iconText: "×"
                    text: qsTr("Remove")
                    flat: true
                    controlSize: 30
                    danger: true
                    onClicked: {
                        if (root.favoriteEntries)
                            root.controller.toggleFavorite(savedDelegate.placeId);
                        else
                            root.controller.removeRecent(savedDelegate.placeId);
                    }
                }
            }
        }

        ScrollBar.vertical: FluentScrollBar {}
    }

    Label {
        Layout.fillWidth: true
        visible: !root.hasEntries
        text: qsTr("None yet")
        color: Theme.textTertiary
        font.pointSize: TypeScale.label
    }
}
