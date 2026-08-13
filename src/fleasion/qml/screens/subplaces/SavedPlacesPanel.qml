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
    readonly property bool hasEntries: Boolean(savedModel) && savedModel.count > 0

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

        delegate: FluentItemDelegate {
            id: savedDelegate

            required property string placeId
            required property string name

            width: ListView.view.width
            text: name
            Accessible.description: qsTr("Place ID %1").arg(placeId)
            onClicked: root.controller.usePlace(placeId)

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

                FluentButton {
                    visible: root.allowRemove
                    text: qsTr("Remove")
                    flat: true
                    compact: true
                    onClicked: root.controller.removeRecent(savedDelegate.placeId)
                }
            }
        }

        ScrollBar.vertical: ScrollBar {}
    }

    Label {
        Layout.fillWidth: true
        visible: !root.hasEntries
        text: qsTr("None yet")
        color: Theme.textTertiary
        font.pointSize: TypeScale.label
    }
}
