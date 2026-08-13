import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property var controller

    width: Math.min(540, parent ? parent.width - Theme.spaceXxl : 540)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: qsTr("Update available")
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceLg

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            Rectangle {
                Layout.preferredWidth: 56
                Layout.preferredHeight: 56
                radius: Theme.radiusLg
                color: Theme.accentSubtle

                Label {
                    anchors.centerIn: parent
                    text: "↓"
                    color: Theme.accent
                    font.pointSize: TypeScale.title
                    Accessible.ignored: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXxs

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Fleasion %1 is ready").arg(root.controller ? root.controller.latestVersion : "")
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.title
                    font.weight: TypeScale.semibold
                    wrapMode: Text.Wrap
                }

                Label {
                    text: qsTr("You are currently running version %1.").arg(root.controller ? root.controller.currentVersion : "")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                }
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Open the signed GitHub release page to review notes and choose the installer for your platform. Fleasion will not download or execute software without you.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Later")
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr("Open release")
                highlighted: true
                onClicked: {
                    if (root.controller)
                        root.controller.openRelease();
                    root.accept();
                }
            }
        }
    }
}
