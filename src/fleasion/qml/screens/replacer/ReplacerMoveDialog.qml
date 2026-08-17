import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    property var selectedPaths: []

    function submit() {
        const choices = root.controller.groupDestinations;
        const destination = destinationPicker.currentIndex >= 0 && destinationPicker.currentIndex < choices.length ? String(choices[destinationPicker.currentIndex].path || "") : "";
        if (root.controller.moveEntries(root.selectedPaths, destination, -1))
            root.accept();
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent.width - Theme.spaceXxl)
    modal: true
    title: qsTr("Move replacements")
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("Move %n selected item(s) to:", "", root.selectedPaths.length)
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
        }

        FluentComboBox {
            id: destinationPicker

            Layout.fillWidth: true
            model: root.controller.groupDestinations
            textRole: "label"
            currentIndex: 0
            Accessible.name: qsTr("Destination group")
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("You can also drag selected rows by their handle when the list is in manual order.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        DialogActionBar {
            Layout.fillWidth: true
            acceptText: qsTr("Move")
            acceptEnabled: destinationPicker.currentIndex >= 0
            onCancelRequested: root.reject()
            onAcceptRequested: root.submit()
        }
    }
}
