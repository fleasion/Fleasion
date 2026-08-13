pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var appController
    property int page: 0
    signal finished

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(680, parent ? parent.width - Theme.spaceXxl : 680)
    height: Math.min(560, parent ? parent.height - Theme.spaceXxl : 560)
    modal: true
    closePolicy: Popup.NoAutoClose
    padding: 0

    background: Rectangle {
        color: Theme.surfaceElevated
        radius: Theme.radiusXl
        border.color: Theme.border
    }

    contentItem: ColumnLayout {
        spacing: 0
        Accessible.name: qsTr("Welcome to Fleasion")

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 112
            color: Theme.accentSubtle
            topLeftRadius: Theme.radiusXl
            topRightRadius: Theme.radiusXl

            RowLayout {
                anchors.fill: parent
                anchors.margins: Theme.spaceLg
                spacing: Theme.spaceMd

                Rectangle {
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 64
                    radius: Theme.radiusLg
                    color: Theme.accent

                    Label {
                        anchors.centerIn: parent
                        text: "F"
                        color: Theme.accentForeground
                        font.pointSize: TypeScale.display
                        font.weight: Font.Bold
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXxs

                    Label {
                        Layout.fillWidth: true
                        text: root.page === 0 ? qsTr("Welcome to Fleasion") : qsTr("Choose how Fleasion starts")
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.display
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: root.page === 0 ? qsTr("A new Fluent workspace for intercepting, inspecting, and replacing Roblox assets.") : qsTr("These choices can be changed later in Settings.")
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.body
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Theme.spaceLg
            currentIndex: root.page

            ColumnLayout {
                spacing: Theme.spaceMd

                Card {
                    Layout.fillWidth: true
                    title: qsTr("One workspace, focused tools")
                    subtitle: qsTr("Replacement profiles, captured assets, reversible file modifications, live proxy traffic, and diagnostics now share one consistent shell.")
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceSm

                    StatusPill {
                        text: qsTr("Typed Python bridges")
                        status: "info"
                    }
                    StatusPill {
                        text: qsTr("Light and dark themes")
                        status: "success"
                    }
                    StatusPill {
                        text: qsTr("Keyboard friendly")
                        status: "neutral"
                    }
                }

                Item {
                    Layout.fillHeight: true
                }
            }

            ColumnLayout {
                spacing: Theme.spaceXs

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Run when you sign in")
                    description: qsTr("Keep interception ready without opening the dashboard.")
                    iconText: "▷"

                    FluentSwitch {
                        checked: root.appController.settings.runOnBoot
                        onToggled: root.appController.settings.runOnBoot = checked
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Desktop integration")
                    description: qsTr("Install the platform launcher or Start menu entry.")
                    iconText: "⊞"

                    FluentSwitch {
                        checked: root.appController.settings.desktopIntegration
                        onToggled: root.appController.settings.desktopIntegration = checked
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Keep running in the tray")
                    description: qsTr("Closing the dashboard keeps proxy features available.")
                    iconText: "◎"

                    FluentSwitch {
                        checked: root.appController.settings.closeToTray
                        onToggled: root.appController.settings.closeToTray = checked
                    }
                }

                Item {
                    Layout.fillHeight: true
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Theme.border
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Theme.spaceMd
            spacing: Theme.spaceSm

            Row {
                spacing: Theme.spaceXs
                Repeater {
                    model: 2
                    Rectangle {
                        required property int index
                        width: index === root.page ? 24 : 8
                        height: 8
                        radius: 4
                        color: index === root.page ? Theme.accent : Theme.borderStrong

                        Behavior on width {
                            NumberAnimation {
                                duration: Motion.fast
                            }
                        }
                    }
                }
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                visible: root.page > 0
                text: qsTr("Back")
                onClicked: root.page--
            }

            FluentButton {
                text: root.page === 0 ? qsTr("Continue") : qsTr("Start using Fleasion")
                highlighted: true
                onClicked: {
                    if (root.page === 0) {
                        root.page++;
                    } else {
                        root.finished();
                        root.close();
                    }
                }
            }
        }
    }
}
