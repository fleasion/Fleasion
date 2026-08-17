pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    required property string catalogKey
    required property string entryName
    required property string targetPath
    required property string fileFilter
    required property bool muteAvailable
    required property bool supported
    required property string limitation
    required property bool configured
    required property string sourceName
    required property string statusText
    required property string errorMessage
    required property bool optional

    signal editRequested(string catalogKey, string entryName, string targetPath, string fileFilter)
    signal inspectRequested(string entryName, string targetPath)
    signal muteRequested(string catalogKey)
    signal resetRequested(string catalogKey)
    signal removeRequested(string catalogKey)

    readonly property string statusLabel: {
        if (!root.supported)
            return qsTr("Unavailable");
        if (root.statusText === "applied")
            return qsTr("Applied");
        if (root.statusText === "pending")
            return qsTr("Applying");
        if (root.statusText === "error" || root.errorMessage.length > 0)
            return qsTr("Error");
        if (root.statusText === "orphaned_stash")
            return qsTr("Recovery");
        return qsTr("Default");
    }
    readonly property string statusTone: !root.supported ? "warning" : root.statusText === "applied" ? "success" : root.statusText === "pending" ? "info" : root.statusText === "error" || root.errorMessage.length > 0 ? "error" : root.statusText === "orphaned_stash" ? "warning" : "neutral"

    implicitHeight: Math.max(Theme.largeControlHeight + Theme.spaceXs, content.implicitHeight + Theme.spaceSm)
    color: pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: 0
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("%1, %2").arg(root.entryName).arg(root.statusLabel)
    Accessible.description: root.errorMessage.length > 0 ? root.errorMessage : root.limitation.length > 0 ? root.limitation : root.targetPath

    RowLayout {
        id: content

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
            spacing: 1

            Label {
                Layout.fillWidth: true
                text: root.entryName
                color: root.supported ? Theme.textPrimary : Theme.textDisabled
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: root.errorMessage.length > 0 ? root.errorMessage : root.limitation.length > 0 ? root.limitation : root.sourceName.length > 0 ? root.sourceName : root.targetPath
                color: root.errorMessage.length > 0 ? Theme.danger : Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideMiddle
            }
        }

        FluentButton {
            visible: root.muteAvailable
            text: qsTr("Mute")
            compact: true
            enabled: root.supported
            onClicked: root.muteRequested(root.catalogKey)
        }

        IconButton {
            iconText: "◉"
            text: qsTr("Inspect current and original files for %1").arg(root.entryName)
            enabled: root.supported
            onClicked: root.inspectRequested(root.entryName, root.targetPath)
        }

        IconButton {
            iconText: "⇄"
            text: qsTr("Choose a replacement for %1").arg(root.entryName)
            enabled: root.supported
            onClicked: root.editRequested(root.catalogKey, root.entryName, root.targetPath, root.fileFilter)
        }

        IconButton {
            visible: root.configured || root.statusText === "orphaned_stash"
            iconText: "↺"
            text: qsTr("Restore %1").arg(root.entryName)
            danger: true
            onClicked: root.resetRequested(root.catalogKey)
        }

        IconButton {
            visible: root.optional && !root.configured
            iconText: "×"
            text: qsTr("Remove %1 from this list").arg(root.entryName)
            onClicked: root.removeRequested(root.catalogKey)
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
}
