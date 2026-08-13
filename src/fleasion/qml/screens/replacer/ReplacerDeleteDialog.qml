import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string targetKind: "entries"
    property string profileName
    property int entryCount: 0
    signal confirmed(string targetKind)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(460, parent.width - Theme.spaceXxl)
    modal: true
    title: targetKind === "profile" ? qsTr("Delete profile?") : qsTr("Delete replacements?")
    standardButtons: Dialog.Yes | Dialog.Cancel
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: root.targetKind === "profile" ? qsTr("The profile “%1” and all of its rules will be deleted.").arg(root.profileName) : qsTr("%n selected replacement(s) will be deleted.", "", root.entryCount)
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            text: root.targetKind === "profile" ? qsTr("At least one profile must remain. This action cannot be undone.") : qsTr("You can undo this change until you switch profiles or close Fleasion.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }
    }

    onAccepted: confirmed(targetKind)
}
