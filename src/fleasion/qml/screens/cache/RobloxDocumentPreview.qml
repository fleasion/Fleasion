pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    property string pendingExportFormat: ''
    readonly property bool wideLayout: width >= 520

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            StatusPill {
                text: root.controller.documentKind
                status: root.controller.modified ? 'warning' : 'info'
            }

            Label {
                Layout.fillWidth: true
                text: root.controller.summaryText
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                elide: Text.ElideRight
            }

            Label {
                visible: root.controller.modified
                text: qsTr('Modified')
                color: Theme.warning
                font.pointSize: TypeScale.caption
                font.weight: TypeScale.semibold
            }

            FluentButton {
                text: qsTr('Undo')
                compact: true
                flat: true
                enabled: root.controller.canUndo
                onClicked: root.controller.undo()
            }

            FluentButton {
                text: qsTr('Revert')
                compact: true
                flat: true
                enabled: root.controller.modified
                onClicked: root.controller.revert()
            }

            FluentButton {
                text: qsTr('Export edited…')
                compact: true
                highlighted: root.controller.modified
                enabled: root.controller.loaded
                onClicked: exportDialog.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            SearchBox {
                id: documentSearch

                Layout.fillWidth: true
                text: root.controller.query
                placeholderText: qsTr('Search instances and properties')
                accessibleName: qsTr('Search Roblox document')
                onTextEdited: {
                    root.controller.query = text;
                    if (text.length > 0)
                        Qt.callLater(() => documentTree.expandRecursively(-1));
                }
                onCleared: root.controller.query = ''
            }

            FluentButton {
                text: qsTr('Expand')
                compact: true
                flat: true
                onClicked: documentTree.expandRecursively(-1)
            }

            FluentButton {
                text: qsTr('Collapse')
                compact: true
                flat: true
                onClicked: documentTree.collapseRecursively(-1)
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: root.wideLayout ? 2 : 1
            rowSpacing: Theme.spaceXs
            columnSpacing: Theme.spaceXs

            Rectangle {
                Layout.fillWidth: !root.wideLayout
                Layout.fillHeight: true
                Layout.preferredWidth: root.wideLayout ? Math.max(210, root.width * 0.4) : -1
                Layout.minimumHeight: root.wideLayout ? 190 : 120
                color: 'transparent'
                border.width: 1
                border.color: Theme.border
                radius: Theme.radiusSm
                clip: true

                TreeView {
                    id: documentTree

                    anchors.fill: parent
                    anchors.margins: 1
                    model: root.controller.treeModel
                    boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true
                    selectionBehavior: TableView.SelectionDisabled
                    columnWidthProvider: _column => documentTree.width
                    Accessible.name: qsTr('Roblox document instance hierarchy')

                    delegate: RobloxDocumentTreeDelegate {
                        width: documentTree.width
                        controller: root.controller
                    }

                    ScrollBar.vertical: FluentScrollBar {}
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: root.wideLayout ? 190 : 150
                spacing: Theme.spaceXs

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs

                    FluentTextField {
                        id: nameField

                        Layout.fillWidth: true
                        text: root.controller.selectedName
                        placeholderText: qsTr('Instance name')
                        enabled: root.controller.hasSelection
                        Accessible.name: qsTr('Selected instance name')
                        onAccepted: root.controller.renameSelected(text)
                    }

                    FluentTextField {
                        id: classField

                        Layout.preferredWidth: 140
                        text: root.controller.selectedClassName
                        placeholderText: qsTr('ClassName')
                        enabled: root.controller.hasSelection
                        Accessible.name: qsTr('Selected instance ClassName')
                        onAccepted: root.controller.setSelectedClassName(text)
                    }

                    FluentButton {
                        text: qsTr('Apply')
                        compact: true
                        enabled: root.controller.hasSelection
                        onClicked: {
                            root.controller.renameSelected(nameField.text);
                            root.controller.setSelectedClassName(classField.text);
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: root.controller.errorText.length > 0
                    text: root.controller.errorText
                    color: Theme.danger
                    font.pointSize: TypeScale.caption
                    wrapMode: Text.Wrap
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: root.controller.propertiesModel
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true
                    Accessible.name: qsTr('Selected instance properties')

                    delegate: RobloxPropertyDelegate {
                        required property int index
                        required property string name
                        required property string typeName
                        required property string valueText
                        required property bool editable

                        width: ListView.view.width
                        controller: root.controller
                        ownerIdentity: root.controller.selectedReferent
                        rowIndex: index
                        propertyName: name
                        propertyTypeName: typeName
                        propertyValueText: valueText
                        editableValue: editable
                    }

                    ScrollBar.vertical: FluentScrollBar {}
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs

                    FluentTextField {
                        id: propertyNameField

                        Layout.fillWidth: true
                        placeholderText: qsTr('New property name')
                        enabled: root.controller.hasSelection
                        Accessible.name: qsTr('New property name')
                    }

                    FluentComboBox {
                        id: propertyTypePicker

                        Layout.preferredWidth: 120
                        model: ['String', 'Bool', 'Int', 'Float', 'Double', 'Content', 'Vector3']
                        enabled: root.controller.hasSelection
                        Accessible.name: qsTr('New property type')
                    }

                    FluentButton {
                        text: qsTr('Add')
                        compact: true
                        enabled: root.controller.hasSelection && propertyNameField.text.trim().length > 0
                        onClicked: {
                            if (root.controller.addProperty(propertyNameField.text, propertyTypePicker.currentText))
                                propertyNameField.clear();
                        }
                    }
                }
            }
        }
    }

    FluentDialog {
        id: exportDialog

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(480, parent.width - Theme.spaceXl)
        modal: true
        focus: true
        title: qsTr('Export edited document')
        standardButtons: Dialog.NoButton

        contentItem: ColumnLayout {
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: qsTr('This creates a new file. The cached source is never overwritten.')
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }

            FluentComboBox {
                id: formatPicker

                Layout.fillWidth: true
                model: root.controller.exportFormats
                Accessible.name: qsTr('Edited document format')
                onCurrentTextChanged: {
                    if (exportDialog.opened)
                        destinationField.text = root.controller.suggestedExportUrl(currentText);
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                FluentTextField {
                    id: destinationField

                    Layout.fillWidth: true
                    placeholderText: qsTr('Choose a new export path')
                    Accessible.name: qsTr('Edited document export destination')
                }

                FluentButton {
                    text: qsTr('Browse…')
                    onClicked: destinationDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true

                Item {
                    Layout.fillWidth: true
                }

                FluentButton {
                    text: qsTr('Cancel')
                    onClicked: exportDialog.close()
                }

                FluentButton {
                    text: qsTr('Export')
                    highlighted: true
                    enabled: formatPicker.currentText.length > 0 && destinationField.text.length > 0
                    onClicked: {
                        if (root.controller.exportDocument(formatPicker.currentText, destinationField.text))
                            exportDialog.close();
                    }
                }
            }
        }

        onOpened: destinationField.text = root.controller.suggestedExportUrl(formatPicker.currentText)
    }

    FileDialog {
        id: destinationDialog

        title: qsTr('Choose a new export path')
        fileMode: FileDialog.SaveFile
        onAccepted: destinationField.text = selectedFile.toString()
    }
}
