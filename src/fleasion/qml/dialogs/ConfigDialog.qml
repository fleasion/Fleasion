import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property var controller
    property string mode: "create"
    property string sourceName
    property string initialName
    signal completed(string name)

    function prepare(modeValue, sourceValue, nameValue) {
        mode = modeValue || "create";
        sourceName = sourceValue || "";
        initialName = nameValue || "";
        nameField.text = initialName;
        nameField.selectAll();
    }

    function perform() {
        const value = nameField.text.trim();
        if (!controller || value.length === 0)
            return false;
        if (mode === "rename")
            return controller.renameConfig(sourceName, value);
        if (mode === "duplicate")
            return controller.duplicateConfig(sourceName, value);
        return controller.createConfig(value);
    }

    width: Math.min(460, parent ? parent.width - Theme.spaceXxl : 460)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: mode === "rename" ? qsTr("Rename profile") : mode === "duplicate" ? qsTr("Duplicate profile") : qsTr("New profile")
    standardButtons: Dialog.NoButton
    onOpened: nameField.forceActiveFocus()

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
            text: root.mode === "duplicate" ? qsTr("Create a copy of “%1” with a new name.").arg(root.sourceName) : qsTr("Profile names are used as file names in your Fleasion configuration folder.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: qsTr("Profile name")
            Accessible.name: qsTr("Profile name")
            selectByMouse: true
            onAccepted: submitButton.clicked()
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.reject()
            }

            FluentButton {
                id: submitButton

                text: root.mode === "rename" ? qsTr("Rename") : root.mode === "duplicate" ? qsTr("Duplicate") : qsTr("Create")
                highlighted: true
                enabled: nameField.text.trim().length > 0 && root.controller
                onClicked: {
                    if (root.perform()) {
                        root.completed(nameField.text.trim());
                        root.accept();
                    }
                }
            }
        }
    }
}
