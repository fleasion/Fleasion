pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "utilities"
import "../dialogs" as Dialogs

Rectangle {
    id: root

    property var controller
    property var appController
    color: Theme.surface

    function requestCacheCleanup() {
        cleanupDialogLoader.active = true;
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            x: Theme.pageGutter
            y: Theme.pageTopGutter
            width: Math.max(0, parent.width - Theme.pageGutter * 2)
            spacing: Theme.sectionGap

            PageHeader {
                Layout.fillWidth: true
                title: qsTr("Utilities")
                subtitle: qsTr("Accounts, session tools, identity overrides, and maintenance in one workspace.")
                iconText: "⋯"
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.width >= 1060 ? 2 : 1
                columnSpacing: Theme.spaceSm
                rowSpacing: Theme.spaceSm

                AccountsPanel {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    controller: root.controller
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.spaceSm

                    ReservedRejoinPanel {
                        Layout.fillWidth: true
                        controller: root.controller
                    }

                    MultiInstancePanel {
                        Layout.fillWidth: true
                        controller: root.controller
                    }
                }
            }

            UsernameSpooferPanel {
                Layout.fillWidth: true
                controller: root.controller
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.width >= 1060 ? 2 : 1
                columnSpacing: Theme.spaceSm
                rowSpacing: Theme.spaceSm

                AnimationConverterPanel {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    controller: root.controller
                }

                SubplaceBlacklistPanel {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    controller: root.controller
                    settingsController: root.appController ? root.appController.settings : null
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.width >= 900 ? 2 : 1
                columnSpacing: Theme.spaceSm
                rowSpacing: Theme.spaceSm

                Card {
                    Layout.fillWidth: true
                    flat: true
                    padding: Theme.panelPadding
                    topPadding: Theme.spaceXs
                    bottomPadding: Theme.spaceXs
                    contentSpacing: Theme.spaceXs
                    title: qsTr("Application data")
                    subtitle: qsTr("Open folders managed by Fleasion.")

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Configuration files")
                        description: qsTr("View profiles, rules, and preferences.")
                        iconText: "⚙"
                        interactive: true
                        enabled: Boolean(root.appController)
                        onActivated: root.appController.openConfigsFolder()
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Diagnostic logs")
                        description: qsTr("Open Fleasion's log folder.")
                        iconText: "☷"
                        interactive: true
                        enabled: Boolean(root.appController)
                        onActivated: root.appController.openLogsFolder()
                    }
                }

                Card {
                    Layout.fillWidth: true
                    flat: true
                    padding: Theme.panelPadding
                    topPadding: Theme.spaceXs
                    bottomPadding: Theme.spaceXs
                    contentSpacing: Theme.spaceXs
                    title: qsTr("Maintenance and support")

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Clear cached assets")
                        description: qsTr("Remove locally generated Roblox cache data.")
                        iconText: "✕"

                        FluentButton {
                            text: qsTr("Clear cache")
                            enabled: Boolean(root.appController)
                            onClicked: root.requestCacheCleanup()
                        }
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Source repository")
                        description: qsTr("Open the Fleasion project on GitHub.")
                        iconText: "↗"
                        interactive: true
                        onActivated: root.appController.openRepository()
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Theme.pageBottomGutter
            }
        }
    }

    Loader {
        id: cleanupDialogLoader

        anchors.fill: parent
        active: false
        sourceComponent: Component {
            Dialogs.ConfirmDialog {
                heading: qsTr("Clear cached assets?")
                message: qsTr("Fleasion will remove locally cached assets. Original Roblox files are not affected.")
                acceptText: qsTr("Clear cache")
                destructive: true
                onConfirmed: root.appController.cacheCleanupRequested()
                onClosed: cleanupDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.ConfirmDialog).open();
        }
    }
}
