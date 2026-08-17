import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    property string entryId
    readonly property bool replacing: entryId.length > 0

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(600, parent.width - Theme.spaceXxl)
    modal: true
    title: replacing ? qsTr("Replace source file") : qsTr("Add file modification")
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    footer: DialogActionBar {
        acceptText: qsTr("Save")
        onCancelRequested: root.reject()
        onAcceptRequested: {
            const saved = root.replacing ? root.controller.replaceSource(root.entryId, sourceField.text) : root.controller.addModification(nameField.text, targetField.text, sourceField.text);
            if (saved)
                root.accept();
        }
    }

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            visible: !root.replacing
            text: qsTr("Display name")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: nameField
            Layout.fillWidth: true
            visible: !root.replacing
            placeholderText: qsTr("Custom cursor")
            Accessible.name: qsTr("Modification name")
        }

        Label {
            Layout.fillWidth: true
            visible: !root.replacing
            text: qsTr("Path inside the Roblox installation")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: targetField
            Layout.fillWidth: true
            visible: !root.replacing
            placeholderText: qsTr("content/textures/Cursors/KeyboardMouse/ArrowCursor.png")
            Accessible.name: qsTr("Roblox target path")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Local source file")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FileDropField {
            id: sourceField
            Layout.fillWidth: true
            placeholderText: qsTr("Drop or choose the replacement file")
            dialogTitle: qsTr("Choose modification source")
            nameFilters: [qsTr("All files (*)")]
            accessibleName: qsTr("Local modification source")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Fleasion keeps a restorable copy of the original file before applying this change.")
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }
    }

    onOpened: {
        nameField.text = "";
        targetField.text = "";
        sourceField.text = "";
        if (replacing)
            sourceField.forceActiveFocus();
        else
            nameField.forceActiveFocus();
    }
}
