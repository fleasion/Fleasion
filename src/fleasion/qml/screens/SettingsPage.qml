pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "components"
import "settings" as Settings

Rectangle {
    id: root

    property var controller
    property var appController
    color: Theme.surface

    property bool openDashboardOnLaunch: false
    property bool closeToTray: false
    property bool runOnBoot: false
    property bool desktopIntegration: false
    property bool autoDeleteCacheOnExit: false
    property bool clearCacheOnLaunch: false
    property bool closeRobloxOnExit: false
    property bool readOnlyGuard: false
    property bool showReplacerNotifications: false
    property bool wirePreservingPassthrough: false

    function settingValue(key) {
        return controller ? Boolean(controller.value(key)) : false;
    }

    function synchronizeSettings() {
        openDashboardOnLaunch = settingValue("open_dashboard_on_launch");
        closeToTray = settingValue("close_to_tray");
        runOnBoot = settingValue("run_on_boot");
        desktopIntegration = settingValue("desktop_integration");
        autoDeleteCacheOnExit = settingValue("auto_delete_cache_on_exit");
        clearCacheOnLaunch = settingValue("clear_cache_on_launch");
        closeRobloxOnExit = settingValue("close_env_proxy_roblox_on_exit");
        readOnlyGuard = settingValue("lock_roblox_files_read_only");
        showReplacerNotifications = settingValue("show_replacer_notifications");
        wirePreservingPassthrough = settingValue("wire_preserving_passthrough");
    }

    function setBooleanSetting(key, value) {
        if (controller)
            controller.setBool(key, value);
    }

    function themeIndex(themeName) {
        const index = ["System", "Light", "Dark"].indexOf(themeName);
        return Math.max(0, index);
    }

    function proxyModeIndex(mode) {
        return mode === "hosts" ? 1 : 0;
    }

    Component.onCompleted: {
        synchronizeSettings();
        if (controller)
            controller.refresh();
    }

    Connections {
        target: root.controller
        ignoreUnknownSignals: true

        function onValuesChanged() {
            root.synchronizeSettings();
        }
    }

    ScrollView {
        id: settingsScroll

        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            x: Theme.pageGutter
            y: Theme.pageTopGutter
            width: Math.max(0, settingsScroll.availableWidth - Theme.pageGutter * 2)
            spacing: Theme.sectionGap

            PageHeader {
                Layout.fillWidth: true
                title: qsTr("Settings")
                subtitle: qsTr("Personalize Fleasion and choose how it behaves on this device.")
                iconText: "⚙"

                StatusPill {
                    text: root.controller ? root.controller.platformName : qsTr("Unavailable")
                    status: root.controller ? "neutral" : "warning"
                }
            }

            Card {
                Layout.fillWidth: true
                flat: true
                padding: Theme.panelPadding
                topPadding: Theme.spaceXs
                bottomPadding: Theme.spaceXs
                contentSpacing: Theme.spaceXs
                title: qsTr("Appearance")
                subtitle: qsTr("Match your system or choose a consistent light or dark theme.")

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Application theme")
                    description: qsTr("System follows the operating system color preference.")
                    iconText: "◐"

                    FluentComboBox {
                        model: [qsTr("System"), qsTr("Light"), qsTr("Dark")]
                        currentIndex: root.controller ? root.themeIndex(root.controller.theme) : 0
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("Application theme")
                        onActivated: index => {
                            const themes = ["System", "Light", "Dark"];
                            const colorSchemes = ["system", "light", "dark"];
                            root.controller.theme = themes[index];
                            Theme.colorScheme = colorSchemes[index];
                        }
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Accent color")
                    description: qsTr("Choose the color used for focus, selection, and primary actions.")
                    iconText: "◉"

                    RowLayout {
                        spacing: Theme.spaceXs

                        Repeater {
                            model: ["#5b4cf0", "#0067c0", "#008272", "#c43e1c", "#b146c2"]

                            delegate: RoundButton {
                                id: accentButton

                                required property string modelData
                                required property int index
                                readonly property var settingsController: root.controller

                                implicitWidth: Theme.controlHeight
                                implicitHeight: Theme.controlHeight
                                checked: settingsController && settingsController.accentColor === modelData
                                checkable: true
                                Accessible.name: qsTr("Use accent color %1").arg(modelData)
                                onClicked: settingsController.accentColor = modelData

                                background: Rectangle {
                                    radius: width / 2
                                    color: accentButton.modelData
                                    border.width: accentButton.checked ? 3 : 1
                                    border.color: accentButton.checked ? Theme.textPrimary : Theme.borderStrong
                                }
                            }
                        }
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("High contrast")
                    description: qsTr("Increase surface and border contrast throughout the dashboard.")
                    iconText: "◑"

                    FluentSwitch {
                        checked: root.controller ? root.controller.highContrast : false
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("High contrast")
                        onToggled: root.controller.highContrast = checked
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Reduce motion")
                    description: qsTr("Remove non-essential interface animations.")
                    iconText: "→"

                    FluentSwitch {
                        checked: root.controller ? root.controller.reducedMotion : false
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("Reduce motion")
                        onToggled: root.controller.reducedMotion = checked
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Always on top")
                    description: qsTr("Keep the dashboard above other windows.")
                    iconText: "↑"

                    FluentSwitch {
                        checked: root.controller ? root.controller.alwaysOnTop : false
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("Always on top")
                        onToggled: root.controller.alwaysOnTop = checked
                    }
                }
            }

            Settings.AdvancedProxySettings {
                controller: root.controller
            }

            Settings.RobloxAuthSettings {
                controller: root.controller
            }

            Card {
                Layout.fillWidth: true
                flat: true
                padding: Theme.panelPadding
                topPadding: Theme.spaceXs
                bottomPadding: Theme.spaceXs
                contentSpacing: Theme.spaceXs
                title: qsTr("Startup and window behavior")
                subtitle: qsTr("Control when Fleasion starts and what closing the dashboard does.")

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Open dashboard on launch")
                    description: qsTr("Show this window when Fleasion starts.")
                    checked: root.openDashboardOnLaunch
                    onToggled: value => {
                        root.openDashboardOnLaunch = value;
                        root.setBooleanSetting("open_dashboard_on_launch", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Close to system tray")
                    description: qsTr("Keep Fleasion running when its dashboard is closed.")
                    checked: root.closeToTray
                    onToggled: value => {
                        root.closeToTray = value;
                        root.setBooleanSetting("close_to_tray", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Run on boot")
                    description: qsTr("Start Fleasion when you sign in to this device.")
                    checked: root.runOnBoot
                    onToggled: value => {
                        root.runOnBoot = value;
                        root.setBooleanSetting("run_on_boot", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Desktop integration")
                    description: qsTr("Install platform shortcuts and application integration.")
                    checked: root.desktopIntegration
                    onToggled: value => {
                        root.desktopIntegration = value;
                        root.setBooleanSetting("desktop_integration", value);
                    }
                }
            }

            Card {
                Layout.fillWidth: true
                flat: true
                padding: Theme.panelPadding
                topPadding: Theme.spaceXs
                bottomPadding: Theme.spaceXs
                contentSpacing: Theme.spaceXs
                title: qsTr("Proxy")
                subtitle: qsTr("Choose how Roblox traffic is routed through Fleasion.")

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Enable proxy features")
                    description: qsTr("Allow request interception, cache scraping, and proxy-backed tools.")
                    iconText: "⇄"

                    FluentSwitch {
                        checked: root.controller ? root.controller.proxyFeaturesEnabled : false
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("Enable proxy features")
                        onToggled: root.controller.proxyFeaturesEnabled = checked
                    }
                }

                SettingRow {
                    Layout.fillWidth: true
                    title: qsTr("Proxy mode")
                    description: qsTr("Environment proxy is user scoped; Hosts File may require administrator access.")
                    iconText: "⇆"

                    FluentComboBox {
                        model: [qsTr("Environment proxy"), qsTr("Hosts File")]
                        currentIndex: root.controller ? root.proxyModeIndex(root.controller.proxyMode) : 0
                        enabled: Boolean(root.controller)
                        Accessible.name: qsTr("Proxy mode")
                        onActivated: index => root.controller.proxyMode = index === 1 ? "hosts" : "env"
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Wire-preserving pass-through")
                    description: qsTr("Preserve upstream response bytes when no rewrite is required. Changing this restarts the proxy.")
                    checked: root.wirePreservingPassthrough
                    onToggled: value => {
                        root.wirePreservingPassthrough = value;
                        root.setBooleanSetting("wire_preserving_passthrough", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Close Roblox when proxy exits")
                    description: qsTr("Avoid leaving a client connected to a proxy that is no longer running.")
                    checked: root.closeRobloxOnExit
                    onToggled: value => {
                        root.closeRobloxOnExit = value;
                        root.setBooleanSetting("close_env_proxy_roblox_on_exit", value);
                    }
                }
            }

            Card {
                Layout.fillWidth: true
                flat: true
                padding: Theme.panelPadding
                topPadding: Theme.spaceXs
                bottomPadding: Theme.spaceXs
                contentSpacing: Theme.spaceXs
                title: qsTr("Storage and safety")
                subtitle: qsTr("Control cleanup and safeguards for local game files.")

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Clear cache on launch")
                    description: qsTr("Remove Fleasion's generated cache whenever the app starts.")
                    checked: root.clearCacheOnLaunch
                    onToggled: value => {
                        root.clearCacheOnLaunch = value;
                        root.setBooleanSetting("clear_cache_on_launch", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Delete cache on exit")
                    description: qsTr("Remove cached assets when Fleasion shuts down.")
                    checked: root.autoDeleteCacheOnExit
                    onToggled: value => {
                        root.autoDeleteCacheOnExit = value;
                        root.setBooleanSetting("auto_delete_cache_on_exit", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Read-only file guard")
                    description: qsTr("Protect managed Roblox files from being changed unexpectedly.")
                    checked: root.readOnlyGuard
                    onToggled: value => {
                        root.readOnlyGuard = value;
                        root.setBooleanSetting("lock_roblox_files_read_only", value);
                    }
                }

                SettingSwitchRow {
                    Layout.fillWidth: true
                    title: qsTr("Replacement notifications")
                    description: qsTr("Show a notification when a replacement action finishes.")
                    checked: root.showReplacerNotifications
                    onToggled: value => {
                        root.showReplacerNotifications = value;
                        root.setBooleanSetting("show_replacer_notifications", value);
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Theme.pageBottomGutter
            }
        }
    }
}
