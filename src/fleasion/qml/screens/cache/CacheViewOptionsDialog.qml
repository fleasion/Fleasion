pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller
    readonly property var sortOptions: [
        {
            key: 'cachedAt',
            label: qsTr('Cached time')
        },
        {
            key: 'name',
            label: qsTr('Asset name')
        },
        {
            key: 'creator',
            label: qsTr('Creator')
        },
        {
            key: 'assetId',
            label: qsTr('Asset ID')
        },
        {
            key: 'typeName',
            label: qsTr('Asset type')
        },
        {
            key: 'size',
            label: qsTr('File size')
        }
    ]
    readonly property var searchOptions: [
        {
            key: 'name',
            label: qsTr('Name')
        },
        {
            key: 'creator',
            label: qsTr('Creator')
        },
        {
            key: 'id',
            label: qsTr('Asset ID')
        },
        {
            key: 'type',
            label: qsTr('Type')
        },
        {
            key: 'hash',
            label: qsTr('Hash')
        },
        {
            key: 'cached_at',
            label: qsTr('Cached time')
        },
        {
            key: 'updated_at',
            label: qsTr('Updated time')
        },
        {
            key: 'created_at',
            label: qsTr('Created time')
        },
        {
            key: 'url',
            label: qsTr('Source URL')
        }
    ]

    function sortIndex() {
        for (let index = 0; index < sortOptions.length; ++index) {
            if (sortOptions[index].key === controller.sortKey)
                return index;
        }
        return 0;
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(500, parent.width - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Cache view options')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr('Sort')
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.semibold
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            FluentComboBox {
                id: sortPicker

                Layout.fillWidth: true
                model: root.sortOptions.map(option => option.label)
                Accessible.name: qsTr('Cache sort field')
                onActivated: index => root.controller.setSortKey(root.sortOptions[index].key)
            }

            FluentButton {
                text: root.controller.sortDescending ? qsTr('Descending') : qsTr('Ascending')
                onClicked: root.controller.sortDescending = !root.controller.sortDescending
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceXs
            text: qsTr('Visible columns')
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.semibold
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: Theme.spaceSm
            rowSpacing: 0

            FluentCheckBox {
                Layout.fillWidth: true
                text: qsTr('Type')
                checked: root.controller.visibleColumns.indexOf('type') >= 0
                onToggled: root.controller.setColumnVisible('type', checked)
            }

            FluentCheckBox {
                Layout.fillWidth: true
                text: qsTr('Size')
                checked: root.controller.visibleColumns.indexOf('size') >= 0
                onToggled: root.controller.setColumnVisible('size', checked)
            }

            FluentCheckBox {
                Layout.fillWidth: true
                text: qsTr('Cached')
                checked: root.controller.visibleColumns.indexOf('cached_at') >= 0
                onToggled: root.controller.setColumnVisible('cached_at', checked)
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceXs
            text: qsTr('Search in')
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.semibold
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: Theme.spaceSm
            rowSpacing: 0

            Repeater {
                model: root.searchOptions

                delegate: FluentCheckBox {
                    required property var modelData

                    Layout.fillWidth: true
                    text: modelData.label
                    checked: root.controller.searchColumns.indexOf(modelData.key) >= 0
                    onToggled: root.controller.setSearchColumnEnabled(modelData.key, checked)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceXs
            spacing: Theme.spaceXs

            FluentButton {
                text: qsTr('Reset')
                flat: true
                onClicked: {
                    root.controller.resetViewOptions();
                    sortPicker.currentIndex = root.sortIndex();
                }
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr('Done')
                highlighted: true
                onClicked: root.accept()
            }
        }
    }

    onOpened: sortPicker.currentIndex = sortIndex()
}
