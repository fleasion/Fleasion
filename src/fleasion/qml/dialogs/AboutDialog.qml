import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    property var appController

    width: Math.min(560, parent ? parent.width - Theme.spaceXxl : 560)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    title: qsTr("About Fleasion")
    standardButtons: Dialog.Close

    contentItem: ColumnLayout {
        spacing: Theme.spaceLg

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceLg

            Rectangle {
                Layout.preferredWidth: 72
                Layout.preferredHeight: 72
                radius: Theme.radiusXl
                color: Theme.accent

                Label {
                    anchors.centerIn: parent
                    text: "F"
                    color: Theme.accentForeground
                    font.pixelSize: 34
                    font.weight: Font.Bold
                    Accessible.ignored: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                Label {
                    Layout.fillWidth: true
                    text: root.appController ? root.appController.appName : qsTr("Fleasion")
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.display
                    wrapMode: Text.Wrap
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Version %1").arg(root.appController ? root.appController.version : "")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                }

                StatusPill {
                    text: root.appController ? root.appController.platformName : qsTr("Desktop")
                    status: "info"
                }
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("A local Roblox asset interceptor and replacement toolkit, rebuilt with Qt Quick for a faster and more focused desktop experience.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Card {
            Layout.fillWidth: true
            title: qsTr("Project links")
            subtitle: qsTr("Source, releases, help, and the community")

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                FluentButton {
                    text: qsTr("Open repository")
                    enabled: root.appController !== null && root.appController !== undefined
                    onClicked: root.appController.openRepository()
                }

                FluentButton {
                    text: qsTr("Join Discord")
                    enabled: root.appController !== null && root.appController !== undefined
                    onClicked: root.appController.openDiscord()
                }
            }
        }

        Card {
            Layout.fillWidth: true
            title: qsTr("Updates")
            subtitle: root.appController ? root.appController.updates.statusText : ""

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                StatusPill {
                    text: root.appController && root.appController.updates.hasUpdate ? qsTr("Version %1 available").arg(root.appController.updates.latestVersion) : root.appController && root.appController.updates.checking ? qsTr("Checking") : qsTr("Release channel")
                    status: root.appController && root.appController.updates.hasUpdate ? "info" : "success"
                }

                Item {
                    Layout.fillWidth: true
                }

                FluentButton {
                    text: root.appController && root.appController.updates.hasUpdate ? qsTr("Open release") : qsTr("Check now")
                    enabled: Boolean(root.appController) && !root.appController.updates.checking
                    onClicked: {
                        if (root.appController.updates.hasUpdate)
                            root.appController.updates.openRelease();
                        else
                            root.appController.updates.checkNow();
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Fleasion is free software. Roblox is a trademark of Roblox Corporation and is not affiliated with this project.")
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }
    }
}
