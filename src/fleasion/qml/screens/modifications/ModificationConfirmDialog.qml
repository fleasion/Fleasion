import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string operation: "restore"
    property bool enabling: false
    signal confirmed(string operation, bool enabling)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(500, parent.width - Theme.spaceXxl)
    modal: true
    title: operation === "fastFlags" ? enabling ? qsTr("Enable custom FastFlags?") : qsTr("Disable custom FastFlags?") : operation === "reset" ? qsTr("Reset this modification?") : operation === "orphan" ? qsTr("Restore this untracked backup?") : qsTr("Restore all original files?")
    standardButtons: Dialog.Yes | Dialog.Cancel
    closePolicy: Popup.CloseOnEscape

    ColumnLayout {
        width: parent.width
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: {
                if (root.operation === "fastFlags")
                    return root.enabling ? qsTr("Roblox may moderate accounts that use unsupported FastFlags. Fleasion cannot determine whether a flag is safe.") : qsTr("Fleasion will stop merging your custom FastFlags into ClientSettings responses.");
                if (root.operation === "reset")
                    return qsTr("The selected modification will be cleared and its original Roblox file restored.");
                if (root.operation === "orphan")
                    return qsTr("The detected original backup will replace the current Roblox file. Any external change at that path will be overwritten.");
                return qsTr("Every managed file will be restored from Fleasion's original backups.");
            }
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            visible: root.operation === "restore"
            text: qsTr("You can apply configured modifications again later.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }
    }

    onAccepted: confirmed(operation, enabling)
}
