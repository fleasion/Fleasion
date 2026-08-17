pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    required property var appController

    function expandSearchResults() {
        if (root.controller.query.length > 0)
            jsonTree.expandRecursively(-1);
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            SearchBox {
                id: searchBox

                Layout.fillWidth: true
                placeholderText: qsTr('Search JSON keys, paths, or values')
                accessibleName: qsTr('Search cached JSON')
                text: root.controller.query
                onTextEdited: {
                    root.controller.query = text;
                    Qt.callLater(root.expandSearchResults);
                }
                onCleared: root.controller.query = ''
            }

            IconButton {
                iconText: '⋁'
                text: qsTr('Expand all JSON nodes')
                flat: false
                onClicked: jsonTree.expandRecursively(-1)
            }

            IconButton {
                iconText: '⋀'
                text: qsTr('Collapse all JSON nodes')
                flat: false
                onClicked: jsonTree.collapseRecursively(-1)
            }
        }

        DataTableHeader {
            Layout.fillWidth: true

            DataTableHeaderCell {
                fillWidth: true
                text: qsTr('JSON key')
            }

            DataTableHeaderCell {
                preferredWidth: root.width < 480 ? 110 : 220
                text: qsTr('Value')
            }

            DataTableHeaderCell {
                preferredWidth: 118
                visible: root.width >= 480
                text: qsTr('Type / action')
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            TreeView {
                id: jsonTree

                objectName: 'cacheJsonTree'
                anchors.fill: parent
                visible: root.controller.model.count > 0
                model: root.controller.model
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: true
                selectionBehavior: TableView.SelectionDisabled
                columnWidthProvider: _column => jsonTree.width
                Accessible.name: qsTr('Expandable cached JSON')

                delegate: JsonTreeDelegate {
                    width: jsonTree.width
                    appController: root.appController
                }

                ScrollBar.vertical: FluentScrollBar {}
            }

            EmptyState {
                anchors.fill: parent
                visible: root.controller.model.count === 0
                iconText: '⌕'
                title: searchBox.text.length > 0 ? qsTr('No matching JSON values') : qsTr('This JSON is empty')
                description: searchBox.text.length > 0 ? qsTr('Try a broader search or clear the filter.') : qsTr('The cached document does not contain any values to browse.')
                actionText: searchBox.text.length > 0 ? qsTr('Clear search') : ''
                onActionTriggered: {
                    searchBox.clear();
                    root.controller.query = '';
                }
            }
        }
    }
}
