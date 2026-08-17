pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller
    property string entryName
    property string targetPath
    readonly property var details: root.controller.inspector.info

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(820, parent.width - Theme.spaceXxl)
    height: Math.min(580, parent.height - Theme.spaceXxl)
    modal: true
    title: qsTr("Inspect %1").arg(entryName)
    standardButtons: Dialog.Close
    closePolicy: Popup.CloseOnEscape

    onOpened: root.controller.inspector.inspect(root.targetPath, root.entryName)

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: root.targetPath
            color: Theme.textSecondary
            font.family: "monospace"
            font.pointSize: TypeScale.label
            elide: Text.ElideMiddle
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: root.width >= 660 ? 2 : 1
            columnSpacing: Theme.spaceMd
            rowSpacing: Theme.spaceMd

            ModificationPreviewPane {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: qsTr("Current replacement")
                available: root.details.replacementAvailable || false
                sizeText: root.details.replacementSize || ""
                previewUrl: root.details.replacementPreviewUrl || ""
                previewKind: root.details.previewKind || "binary"
                summary: root.details.replacementSummary || qsTr("Unavailable")
                convertedAvailable: root.details.convertedAvailable || false
                meshGeometry: root.controller.inspector.replacementMeshGeometry
                onExportRequested: replacementExport.open()
                onConvertedExportRequested: convertedExport.open()
            }

            ModificationPreviewPane {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: qsTr("Original file")
                available: root.details.originalAvailable || false
                sizeText: root.details.originalSize || ""
                previewUrl: root.details.originalPreviewUrl || ""
                previewKind: root.details.previewKind || "binary"
                summary: root.details.originalSummary || qsTr("Unavailable")
                meshGeometry: root.controller.inspector.originalMeshGeometry
                onExportRequested: originalExport.open()
            }
        }
    }

    FileDialog {
        id: replacementExport

        title: qsTr("Export current replacement")
        fileMode: FileDialog.SaveFile
        onAccepted: root.controller.inspector.exportFile("replacement", selectedFile)
    }

    FileDialog {
        id: originalExport

        title: qsTr("Export original file")
        fileMode: FileDialog.SaveFile
        onAccepted: root.controller.inspector.exportFile("original", selectedFile)
    }

    FileDialog {
        id: convertedExport

        title: qsTr("Export converted replacement")
        fileMode: FileDialog.SaveFile
        defaultSuffix: (root.details.convertedSuffix || ".bin").replace(".", "")
        onAccepted: root.controller.inspector.exportFile("converted", selectedFile)
    }
}
