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
    signal restoreRequested(string targetPath, string recoveryKind)

    visible: root.controller.orphanedModel.count > 0
    spacing: Theme.spaceXxs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Recovery items")
        subtitle: qsTr("These files have recovery data but no active mapping. Review whether recovery restores an original or removes a Fleasion-created file.")
    }

    Repeater {
        model: root.controller.orphanedModel

        delegate: Rectangle {
            id: orphanDelegate

            required property var model
            readonly property string recoveryLabel: orphanDelegate.model.kind === "created" ? qsTr("Remove") : orphanDelegate.model.kind === "mixed" ? qsTr("Mixed") : qsTr("Restore")
            readonly property string recoveryDetail: {
                if (orphanDelegate.model.kind === "created")
                    return qsTr("%1 · no original existed · %n installation(s)", "", orphanDelegate.model.installationCount).arg(orphanDelegate.model.targetPath);
                if (orphanDelegate.model.kind === "mixed")
                    return qsTr("%1 · %2 original backup(s) · %3 created file(s)").arg(orphanDelegate.model.targetPath).arg(orphanDelegate.model.backupCount).arg(orphanDelegate.model.createdCount);
                return qsTr("%1 · %2 · %n installation(s)", "", orphanDelegate.model.installationCount).arg(orphanDelegate.model.targetPath).arg(orphanDelegate.model.sizeText);
            }

            Layout.fillWidth: true
            implicitHeight: Theme.largeControlHeight + Theme.spaceXs
            color: orphanHover.hovered ? Theme.surfaceHover : "transparent"
            Accessible.role: Accessible.ListItem
            Accessible.name: qsTr("Recovery item for %1").arg(orphanDelegate.model.targetPath)

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.spaceSm
                anchors.rightMargin: Theme.spaceXs
                spacing: Theme.spaceSm

                StatusPill {
                    text: orphanDelegate.recoveryLabel
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
                        text: orphanDelegate.recoveryDetail
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
                    text: orphanDelegate.model.kind === "created" ? qsTr("Remove override") : qsTr("Recover")
                    compact: true
                    highlighted: true
                    onClicked: root.restoreRequested(orphanDelegate.model.targetPath, orphanDelegate.model.kind)
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.border
                Accessible.ignored: true
            }

            HoverHandler {
                id: orphanHover
            }
        }
    }
}
