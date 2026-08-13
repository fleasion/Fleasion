import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    property var controller
    property string rulePath
    property string initialName
    property string initialTargets
    property string initialReplacement
    readonly property bool editing: rulePath.length > 0
    signal saved

    function prepare(pathValue, nameValue, targetsValue, replacementValue) {
        rulePath = pathValue || "";
        initialName = nameValue || "";
        initialTargets = targetsValue || "";
        initialReplacement = replacementValue || "";
        nameField.text = initialName;
        targetsField.text = initialTargets;
        replacementField.text = initialReplacement;
    }

    width: Math.min(620, parent ? parent.width - Theme.spaceXxl : 620)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: editing ? qsTr("Edit replacement") : qsTr("Add replacement")
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: root.title
            color: Theme.textPrimary
            font.pointSize: TypeScale.title
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Map one or more Roblox asset IDs to another ID, a URL, a local file, or leave the replacement blank to remove the asset.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: qsTr("Rule name")
            Accessible.name: qsTr("Rule name")
            selectByMouse: true
        }

        FluentTextArea {
            id: targetsField

            Layout.fillWidth: true
            Layout.preferredHeight: 92
            placeholderText: qsTr("Asset IDs, separated by spaces, commas, or new lines")
            Accessible.name: qsTr("Asset IDs to replace")
            wrapMode: TextEdit.Wrap
            selectByMouse: true
        }

        FileDropField {
            id: replacementField

            Layout.fillWidth: true
            placeholderText: qsTr("Replacement ID, URL, or local file")
            dialogTitle: qsTr("Choose replacement asset")
            nameFilters: [qsTr("All files (*)")]
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
                text: root.editing ? qsTr("Save changes") : qsTr("Add replacement")
                highlighted: true
                enabled: root.controller && nameField.text.trim().length > 0 && targetsField.text.trim().length > 0
                onClicked: {
                    const ok = root.editing ? root.controller.updateRule(root.rulePath, nameField.text, targetsField.text, replacementField.text) : root.controller.addRule(nameField.text, targetsField.text, replacementField.text);
                    if (ok) {
                        root.saved();
                        root.accept();
                    }
                }
            }
        }
    }
}
