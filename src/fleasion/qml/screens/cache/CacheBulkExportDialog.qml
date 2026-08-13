import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    property var assetKeys: []
    property var formats: []

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, parent.width - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Export selected assets')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr('%n selected asset(s) can be exported as individual files or as a community-preset-compatible game dump.', '', root.assetKeys.length)
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            text: qsTr('Common file format')
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
        }

        FluentComboBox {
            id: formatPicker

            Layout.fillWidth: true
            model: root.formats
            Accessible.name: qsTr('Bulk export format')
        }

        Label {
            Layout.fillWidth: true
            text: qsTr('Individual files are written to the Fleasion exports folder. A game dump only stores names, types, and asset IDs in JSON.')
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentButton {
                text: qsTr('Game dump…')
                enabled: root.assetKeys.length > 0
                onClicked: gameDumpDialog.open()
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr('Cancel')
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr('Export files')
                highlighted: true
                enabled: formatPicker.currentText.length > 0 && !root.controller.task.busy
                onClicked: {
                    if (root.controller.exportAssets(root.assetKeys, formatPicker.currentText))
                        root.accept();
                }
            }
        }
    }

    FileDialog {
        id: gameDumpDialog

        title: qsTr('Save game dump')
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr('JSON files (*.json)'), qsTr('All files (*)')]
        defaultSuffix: 'json'
        onAccepted: {
            if (root.controller.exportGameDump(root.assetKeys, selectedFile.toString()))
                root.accept();
        }
    }

    onOpened: formats = controller.commonExportFormats(assetKeys)
}
