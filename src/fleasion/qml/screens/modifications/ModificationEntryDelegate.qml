import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property string entryId
    required property string entryName
    required property string targetPath
    required property string sourceType
    required property string sourceValue
    required property string sourceName
    required property string statusText
    required property string errorMessage
    signal replaceRequested(string entryId)
    signal inspectRequested(string name, string targetPath)
    signal resetRequested(string entryId)

    readonly property string statusLabel: {
        switch (statusText) {
        case "applied":
            return qsTr("Applied");
        case "pending":
            return qsTr("Pending");
        case "error":
            return qsTr("Error");
        case "restore_failed":
            return qsTr("Restore failed");
        case "not_set":
            return qsTr("Not configured");
        default:
            return statusText.length > 0 ? statusText : qsTr("Unknown");
        }
    }
    readonly property string statusTone: statusText === "applied" ? "success" : statusText === "error" || statusText === "restore_failed" ? "error" : statusText === "pending" ? "warning" : "neutral"

    implicitHeight: errorMessage.length > 0 ? 72 : 58
    color: pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: 0
    border.width: activeFocus ? 2 : 0
    border.color: Theme.focusRing
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("%1, %2").arg(entryName).arg(statusLabel)
    Accessible.description: errorMessage.length > 0 ? errorMessage : targetPath

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceXs
        spacing: Theme.spaceSm

        StatusPill {
            text: root.statusLabel
            status: root.statusTone
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Label {
                Layout.fillWidth: true
                text: root.entryName
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: root.errorMessage.length > 0 ? root.errorMessage : qsTr("%1 → %2").arg(root.sourceName.length > 0 ? root.sourceName : qsTr("No source")).arg(root.targetPath)
                color: root.errorMessage.length > 0 ? Theme.danger : Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideMiddle
            }
        }

        IconButton {
            iconText: "◉"
            text: qsTr("Inspect current and original files for %1").arg(root.entryName)
            onClicked: root.inspectRequested(root.entryName, root.targetPath)
        }

        IconButton {
            iconText: "⇄"
            text: qsTr("Choose a new source for %1").arg(root.entryName)
            onClicked: root.replaceRequested(root.entryId)
        }

        IconButton {
            iconText: "↺"
            text: qsTr("Reset %1 to its original file").arg(root.entryName)
            danger: true
            onClicked: root.resetRequested(root.entryId)
        }
    }

    HoverHandler {
        id: pointer
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
        Accessible.ignored: true
    }
    Keys.onReturnPressed: event => {
        root.replaceRequested(root.entryId);
        event.accepted = true;
    }
}
