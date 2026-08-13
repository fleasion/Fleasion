pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var controller

    signal sourceRequested(string presetId, string kind)
    signal customImportRequested
    signal deleteRequested(string presetId, string name)

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceSm

        Label {
            text: qsTr('Choose a community-maintained asset list, then select values to send to the replacement editor.')
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: Theme.spaceSm
            Layout.fillWidth: true

            SearchBox {
                id: presetSearch

                placeholderText: qsTr('Search games, creators, or place IDs')
                accessibleName: qsTr('Search community presets')
                text: root.controller.query
                Layout.fillWidth: true
                onTextEdited: root.controller.query = text
                onCleared: root.controller.query = ''
            }

            FluentButton {
                text: qsTr('Import custom')
                enabled: !root.controller.task.busy
                onClicked: root.customImportRequested()
            }

            FluentButton {
                text: qsTr('Refresh')
                enabled: !root.controller.task.busy
                onClicked: root.controller.refresh(true)
            }
        }

        Label {
            visible: root.controller.statusText.length > 0 && !root.controller.task.busy
            text: root.controller.statusText
            color: Theme.warning
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            GridView {
                id: presetGrid

                readonly property int targetCellWidth: 264
                readonly property int columnCount: Math.max(1, Math.floor(width / targetCellWidth))

                anchors.fill: parent
                visible: !root.controller.task.busy && root.controller.catalogModel.count > 0
                model: root.controller.catalogModel
                cellWidth: width / columnCount
                cellHeight: 306
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                reuseItems: true
                Accessible.name: qsTr('Community presets')

                delegate: CommunityPresetCard {
                    required property var model

                    width: GridView.view.cellWidth - Theme.spaceSm
                    height: GridView.view.cellHeight - Theme.spaceSm
                    presetId: model.presetId
                    name: model.name
                    credit: model.credit
                    created: model.created
                    updated: model.updated
                    placeId: model.placeId
                    hasOriginals: model.hasOriginals
                    hasReplacements: model.hasReplacements
                    isCustom: model.isCustom
                    thumbnailUrl: model.thumbnailUrl
                    onSourceRequested: (presetId, kind) => root.sourceRequested(presetId, kind)
                    onDeleteRequested: (presetId, name) => root.deleteRequested(presetId, name)
                }

                ScrollBar.vertical: ScrollBar {}
            }

            BusyIndicator {
                anchors.centerIn: parent
                visible: root.controller.task.busy
                running: visible
                Accessible.name: root.controller.task.message
            }

            EmptyState {
                anchors.fill: parent
                visible: !root.controller.task.busy && root.controller.catalogModel.count === 0
                iconText: presetSearch.text.length > 0 ? '\u2315' : '\u2601'
                title: presetSearch.text.length > 0 ? qsTr('No matching presets') : qsTr('Community catalog unavailable')
                description: presetSearch.text.length > 0 ? qsTr('Try a broader search or clear the filter.') : qsTr('Refresh the catalog, or import a local preset definition.')
                actionText: presetSearch.text.length > 0 ? qsTr('Clear search') : qsTr('Refresh')
                onActionTriggered: {
                    if (presetSearch.text.length > 0) {
                        presetSearch.clear();
                        root.controller.query = '';
                    } else {
                        root.controller.refresh(true);
                    }
                }
            }
        }

        Label {
            text: qsTr('%n preset(s)', '', root.controller.catalogModel.count)
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            Layout.fillWidth: true
        }
    }
}
