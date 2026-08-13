import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    property string assetKey
    property var formats: []

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, parent.width - Theme.spaceXxl)
    modal: true
    focus: true
    title: qsTr("Export cached asset")
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("Export format")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentComboBox {
            id: formatPicker
            Layout.fillWidth: true
            model: root.formats
            Accessible.name: qsTr("Export format")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Destination")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        RowLayout {
            Layout.fillWidth: true

            FluentTextField {
                id: destinationField
                Layout.fillWidth: true
                placeholderText: qsTr("Choose where to save the asset")
                Accessible.name: qsTr("Export destination")
            }

            FluentButton {
                text: qsTr("Browse…")
                onClicked: destinationDialog.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr("Export")
                highlighted: true
                enabled: formatPicker.currentText.length > 0 && destinationField.text.length > 0
                onClicked: {
                    if (root.controller.exportAsset(root.assetKey, formatPicker.currentText, destinationField.text))
                        root.accept();
                }
            }
        }
    }

    FileDialog {
        id: destinationDialog
        title: qsTr("Choose export destination")
        fileMode: FileDialog.SaveFile
        onAccepted: destinationField.text = selectedFile.toString()
    }

    onOpened: {
        formats = controller.exportFormats(assetKey);
        destinationField.text = "";
    }
}
