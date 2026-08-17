import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string action: "create"
    property string currentName
    property string groupPath
    required property var controller
    readonly property bool groupAction: action === "group" || action === "groupSelection" || action === "renameGroup"

    function submit() {
        const value = nameField.text.trim();
        if (value.length === 0)
            return;
        let succeeded = false;
        switch (action) {
        case "rename":
            succeeded = controller.renameConfig(currentName, value);
            break;
        case "duplicate":
            succeeded = controller.duplicateConfig(currentName, value);
            break;
        case "group":
            succeeded = controller.addGroup(value);
            break;
        case "groupSelection":
            succeeded = controller.groupEntries(controller.selection.values(), value);
            break;
        case "renameGroup":
            succeeded = controller.renameGroup(groupPath, value);
            break;
        default:
            succeeded = controller.createConfig(value);
        }
        if (succeeded)
            root.accept();
    }

    function actionTitle() {
        switch (action) {
        case "rename":
            return qsTr("Rename profile");
        case "duplicate":
            return qsTr("Duplicate profile");
        case "renameGroup":
            return qsTr("Rename replacement group");
        case "groupSelection":
            return qsTr("Group selected replacements");
        case "group":
            return qsTr("New replacement group");
        default:
            return qsTr("New profile");
        }
    }

    function initialValue() {
        switch (action) {
        case "rename":
            return currentName;
        case "duplicate":
            return qsTr("%1 copy").arg(currentName);
        case "renameGroup":
            return currentName;
        default:
            return "";
        }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent.width - Theme.spaceXxl)
    modal: true
    title: actionTitle()
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: root.groupAction ? qsTr("Group name") : qsTr("Profile name")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: root.groupAction ? qsTr("Audio replacements") : qsTr("My profile")
            Accessible.name: root.groupAction ? qsTr("Group name") : qsTr("Profile name")
            onAccepted: root.submit()
        }

        Label {
            Layout.fillWidth: true
            text: root.groupAction ? qsTr("Groups keep related rules together and can be enabled, moved, or collapsed as one unit.") : qsTr("Use a short, unique name that describes this set of replacements.")
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        DialogActionBar {
            Layout.fillWidth: true
            acceptText: qsTr("Save")
            acceptEnabled: nameField.text.trim().length > 0
            onCancelRequested: root.reject()
            onAcceptRequested: root.submit()
        }
    }

    onOpened: {
        nameField.text = initialValue();
        nameField.selectAll();
        nameField.forceActiveFocus();
    }
}
