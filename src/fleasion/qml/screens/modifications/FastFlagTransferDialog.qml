pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    property bool replaceExisting: false

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(680, parent.width - Theme.spaceXxl)
    height: Math.min(620, parent.height - Theme.spaceXxl)
    modal: true
    title: qsTr("Import or export FastFlags")
    standardButtons: Dialog.Close

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("Paste a JSON object, choose a JSON file, or export the current editor.")
            color: Theme.textSecondary
            wrapMode: Text.Wrap
        }

        FluentTextArea {
            id: jsonEditor

            Layout.fillWidth: true
            Layout.fillHeight: true
            text: root.controller.fastFlagsJson()
            selectByMouse: true
            wrapMode: TextEdit.NoWrap
            font.family: "monospace"
            Accessible.name: qsTr("FastFlag JSON")
        }

        FluentCheckBox {
            text: qsTr("Replace current flags when importing")
            checked: root.replaceExisting
            onToggled: root.replaceExisting = checked
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentButton {
                text: qsTr("Choose file")
                onClicked: importDialog.open()
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Export file")
                onClicked: exportDialog.open()
            }

            FluentButton {
                text: qsTr("Import JSON")
                highlighted: true
                onClicked: {
                    if (root.controller.importFastFlagsJson(jsonEditor.text, root.replaceExisting))
                        root.accept();
                }
            }
        }
    }

    FileDialog {
        id: importDialog

        title: qsTr("Import FastFlags")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("JSON files (*.json)"), qsTr("All files (*)")]
        onAccepted: {
            if (root.controller.importFastFlagsFile(selectedFile, root.replaceExisting))
                root.accept();
        }
    }

    FileDialog {
        id: exportDialog

        title: qsTr("Export FastFlags")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [qsTr("JSON files (*.json)")]
        onAccepted: root.controller.exportFastFlags(selectedFile)
    }
}
