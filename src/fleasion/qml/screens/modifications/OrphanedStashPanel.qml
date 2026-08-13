pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    signal inspectRequested(string name, string targetPath)
    signal restoreRequested(string targetPath)

    visible: root.controller.orphanedModel.count > 0
    spacing: Theme.spaceXxs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Original backups needing attention")
        subtitle: qsTr("Fleasion found backups without active mappings, usually after an interrupted restore or an external file change.")
    }

    Repeater {
        model: root.controller.orphanedModel

        delegate: Rectangle {
            id: orphanDelegate

            required property var model

            Layout.fillWidth: true
            implicitHeight: Theme.largeControlHeight + Theme.spaceXs
            color: orphanHover.hovered ? Theme.surfaceHover : Theme.warningSubtle
            radius: Theme.radiusSm
            Accessible.role: Accessible.ListItem
            Accessible.name: qsTr("Backup for %1").arg(orphanDelegate.model.targetPath)

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.spaceSm
                anchors.rightMargin: Theme.spaceXs
                spacing: Theme.spaceSm

                StatusPill {
                    text: qsTr("Recovery")
                    status: "warning"
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    Label {
                        Layout.fillWidth: true
                        text: orphanDelegate.model.name
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.body
                        font.weight: TypeScale.medium
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("%1 · %2 · %n installation(s)", "", orphanDelegate.model.installationCount).arg(orphanDelegate.model.targetPath).arg(orphanDelegate.model.sizeText)
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.label
                        elide: Text.ElideMiddle
                    }
                }

                FluentButton {
                    text: qsTr("Inspect")
                    compact: true
                    onClicked: root.inspectRequested(orphanDelegate.model.name, orphanDelegate.model.targetPath)
                }

                FluentButton {
                    text: qsTr("Restore original")
                    compact: true
                    highlighted: true
                    onClicked: root.restoreRequested(orphanDelegate.model.targetPath)
                }
            }

            HoverHandler {
                id: orphanHover
            }
        }
    }
}
