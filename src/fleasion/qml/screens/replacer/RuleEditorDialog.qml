import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    property string entryPath
    readonly property bool editing: entryPath.length > 0

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent.width - Theme.spaceXxl)
    modal: true
    title: editing ? qsTr("Edit replacement") : qsTr("Add replacement")
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    footer: DialogActionBar {
        acceptText: qsTr("Save")
        onCancelRequested: root.reject()
        onAcceptRequested: {
            const saved = root.editing ? root.controller.updateRule(root.entryPath, nameField.text, targetsField.text, replacementField.text) : root.controller.addRule(nameField.text, targetsField.text, replacementField.text);
            if (saved)
                root.accept();
        }
    }

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("Name")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: qsTr("Custom walk animation")
            Accessible.name: qsTr("Replacement name")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Target asset IDs or asset types")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: targetsField

            Layout.fillWidth: true
            placeholderText: qsTr("123456789, Animation")
            Accessible.name: qsTr("Target assets")
            Accessible.description: qsTr("Separate multiple asset IDs or asset types with commas")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Replacement")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: replacementField

            Layout.fillWidth: true
            placeholderText: qsTr("Asset ID, URL, local file, or leave empty to remove")
            Accessible.name: qsTr("Replacement source")
        }

        FileDropField {
            Layout.fillWidth: true
            text: replacementField.text
            placeholderText: qsTr("Drop or choose a local replacement file")
            dialogTitle: qsTr("Choose replacement file")
            nameFilters: [qsTr("All files (*)")]
            onFileChosen: fileUrl => replacementField.text = fileUrl
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: hintRow.implicitHeight + Theme.spaceMd
            radius: Theme.radiusMd
            color: Theme.infoSubtle

            RowLayout {
                id: hintRow

                anchors.fill: parent
                anchors.margins: Theme.spaceXs
                spacing: Theme.spaceXs

                Label {
                    text: "ⓘ"
                    color: Theme.info
                    Accessible.ignored: true
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("A numeric value uses another Roblox asset. HTTP URLs use the CDN directly. An empty value removes the target.")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    onOpened: {
        const values = editing ? controller.entry(entryPath) : controller.takeDraft();
        nameField.text = values.name || "";
        targetsField.text = values.targets || "";
        replacementField.text = values.replacement || "";
        nameField.forceActiveFocus();
    }
}
