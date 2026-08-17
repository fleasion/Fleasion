import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    property string placeId

    function show(placeId, currentName) {
        root.placeId = placeId;
        nameField.text = currentName;
        root.open();
        nameField.forceActiveFocus();
        nameField.selectAll();
    }

    function submit() {
        if (nameField.text.trim().length === 0)
            return;
        root.controller.renameSavedPlace(root.placeId, nameField.text);
        root.accept();
    }

    title: qsTr("Rename saved place")
    anchors.centerIn: parent
    width: Math.min(420, parent ? parent.width - Theme.spaceLg : 420)
    modal: true
    focus: true
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("Choose the name shown in Favorites and Recent searches.")
            color: Theme.textSecondary
            wrapMode: Text.Wrap
        }

        FluentTextField {
            id: nameField

            Layout.fillWidth: true
            placeholderText: qsTr("Saved place name")
            Accessible.name: qsTr("Saved place name")
            onAccepted: root.submit()
        }

        Label {
            Layout.fillWidth: true
            visible: nameField.text.trim().length === 0
            text: qsTr("Enter a name before saving.")
            color: Theme.danger
            font.pointSize: TypeScale.caption
        }

        DialogActionBar {
            Layout.fillWidth: true
            acceptText: qsTr("Save")
            acceptEnabled: nameField.text.trim().length > 0
            onCancelRequested: root.reject()
            onAcceptRequested: root.submit()
        }
    }
}
