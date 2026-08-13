import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property string jobId
    required property string occupancyText
    required property int availableSlots
    required property string pingText
    required property string fpsText
    required property bool isFull
    signal joinRequested(string jobId)
    signal copyRequested(string jobId)

    implicitHeight: 60
    color: pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: Theme.radiusSm
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Public server %1, %2 players, %3 ping").arg(root.jobId).arg(root.occupancyText).arg(root.pingText)

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceXs
        spacing: Theme.spaceXs

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXxs

            Label {
                Layout.fillWidth: true
                text: root.jobId
                color: Theme.textPrimary
                font.family: "monospace"
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideMiddle
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                Label {
                    text: qsTr("%1 players").arg(root.occupancyText)
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }

                Label {
                    text: root.pingText
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }

                Label {
                    text: root.fpsText
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }

                Label {
                    visible: !root.isFull
                    text: qsTr("%n open slot(s)", "", root.availableSlots)
                    color: Theme.success
                    font.pointSize: TypeScale.caption
                }
            }
        }

        StatusPill {
            text: root.isFull ? qsTr("Full") : qsTr("Joinable")
            status: root.isFull ? "warning" : "success"
        }

        IconButton {
            iconText: "⧉"
            text: qsTr("Copy Job ID")
            onClicked: root.copyRequested(root.jobId)
        }

        FluentButton {
            text: qsTr("Join")
            compact: true
            highlighted: !root.isFull
            enabled: !root.isFull
            onClicked: root.joinRequested(root.jobId)
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
