import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string action: "create"
    property string currentName
    signal submitted(string action, string name)

    function actionTitle() {
        switch (action) {
        case "rename":
            return qsTr("Rename profile");
        case "duplicate":
            return qsTr("Duplicate profile");
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
        default:
            return "";
        }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent.width - Theme.spaceXxl)
    modal: true
    title: actionTitle()
    standardButtons: Dialog.Ok | Dialog.Cancel
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: root.action === "group" ? qsTr("Group name") : qsTr("Profile name")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: root.action === "group" ? qsTr("Audio replacements") : qsTr("My profile")
            Accessible.name: root.action === "group" ? qsTr("Group name") : qsTr("Profile name")
            onAccepted: {
                if (text.trim().length > 0)
                    root.accept();
            }
        }

        Label {
            Layout.fillWidth: true
            text: root.action === "group" ? qsTr("Groups keep related rules together and can be enabled as one unit.") : qsTr("Use a short, unique name that describes this set of replacements.")
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }
    }

    onOpened: {
        nameField.text = initialValue();
        nameField.selectAll();
        nameField.forceActiveFocus();
    }
    onAccepted: {
        const value = nameField.text.trim();
        if (value.length > 0)
            submitted(action, value);
    }
}
