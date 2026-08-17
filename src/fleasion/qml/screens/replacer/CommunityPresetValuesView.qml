pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    required property var appController

    signal backRequested
    signal draftPrepared

    function expandSearchResults() {
        if (root.controller.valueQuery.length > 0)
            valueTree.expandRecursively(-1);
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceSm

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentButton {
                text: qsTr('← Back')
                onClicked: root.backRequested()
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Label {
                    Layout.fillWidth: true
                    text: root.controller.selectedPresetName
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.subtitle
                    font.weight: TypeScale.semibold
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: root.controller.selectedPayloadKind === 'originals' ? qsTr('Original asset IDs') : qsTr('Community replacement values')
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            SearchBox {
                id: valueSearch

                Layout.fillWidth: true
                placeholderText: qsTr('Search keys, paths, or values')
                accessibleName: qsTr('Search preset JSON')
                text: root.controller.valueQuery
                onTextEdited: {
                    root.controller.valueQuery = text;
                    Qt.callLater(root.expandSearchResults);
                }
                onCleared: root.controller.valueQuery = ''
            }

            IconButton {
                iconText: '⋁'
                text: qsTr('Expand all JSON nodes')
                flat: false
                onClicked: valueTree.expandRecursively(-1)
            }

            IconButton {
                iconText: '⋀'
                text: qsTr('Collapse all JSON nodes')
                flat: false
                onClicked: valueTree.collapseRecursively(-1)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceSm

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 270
                spacing: 0

                DataTableHeader {
                    Layout.fillWidth: true

                    DataTableHeaderCell {
                        fillWidth: true
                        text: qsTr('JSON key')
                    }

                    DataTableHeaderCell {
                        preferredWidth: valueTree.width < 520 ? 118 : 220
                        text: qsTr('Value')
                    }

                    DataTableHeaderCell {
                        preferredWidth: 92
                        visible: valueTree.width >= 520
                        text: qsTr('Type')
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    TreeView {
                        id: valueTree

                        objectName: 'presetValueTree'
                        anchors.fill: parent
                        visible: root.controller.valueTreeModel.count > 0
                        model: root.controller.valueTreeModel
                        boundsBehavior: Flickable.StopAtBounds
                        reuseItems: true
                        selectionBehavior: TableView.SelectionDisabled
                        columnWidthProvider: _column => valueTree.width
                        Accessible.name: qsTr('Expandable preset JSON')

                        delegate: CommunityPresetTreeDelegate {
                            width: valueTree.width
                            selectionModel: root.controller.valueSelection
                        }

                        ScrollBar.vertical: FluentScrollBar {}
                    }

                    EmptyState {
                        anchors.fill: parent
                        visible: root.controller.valueTreeModel.count === 0
                        iconText: '⌕'
                        title: valueSearch.text.length > 0 ? qsTr('No matching JSON values') : qsTr('This JSON is empty')
                        description: valueSearch.text.length > 0 ? qsTr('Try a broader search or clear the filter.') : qsTr('The selected source does not contain any values to browse.')
                        actionText: valueSearch.text.length > 0 ? qsTr('Clear search') : ''
                        onActionTriggered: {
                            valueSearch.clear();
                            root.controller.valueQuery = '';
                        }
                    }
                }
            }

            CommunityValuePreviewPanel {
                Layout.fillHeight: true
                Layout.preferredWidth: Math.min(380, Math.max(280, root.width * 0.4))
                Layout.minimumWidth: 260
                visible: root.controller.selectedCount === 1
                controller: root.controller
                appController: root.appController
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: qsTr('%n importable value(s) selected', '', root.controller.selectedCount)
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
            }

            FluentButton {
                text: qsTr('Use as targets')
                enabled: root.controller.selectedCount > 0
                Accessible.description: qsTr('Send selected values to the replacement editor')
                onClicked: {
                    if (root.controller.useSelectedAsTargets())
                        root.draftPrepared();
                }
            }

            FluentButton {
                text: qsTr('Use as replacement')
                highlighted: root.controller.selectedCount === 1
                enabled: root.controller.selectedCount === 1
                Accessible.description: qsTr('Send the selected value to the replacement editor')
                onClicked: {
                    if (root.controller.useSelectedAsReplacement())
                        root.draftPrepared();
                }
            }
        }
    }
}
