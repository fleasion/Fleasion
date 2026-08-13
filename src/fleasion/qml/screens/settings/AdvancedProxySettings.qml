pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property var controller
    readonly property var modes: ["auto", "direct_ip", "system_proxy", "http_connect", "socks5"]

    function syncFields() {
        mode.currentIndex = Math.max(0, modes.indexOf(controller.upstreamTransportMode));
        httpHost.text = controller.httpProxyHost;
        httpPort.value = controller.httpProxyPort;
        httpUser.text = controller.httpProxyUsername;
        socksHost.text = controller.socksProxyHost;
        socksPort.value = controller.socksProxyPort;
        socksUser.text = controller.socksProxyUsername;
        assetLimit.value = controller.assetConnectionLimit;
        cdnLimit.value = controller.cdnConnectionLimit;
    }

    function save() {
        if (controller.configureUpstream(modes[mode.currentIndex], httpHost.text, httpPort.value, httpUser.text, httpPassword.text, socksHost.text, socksPort.value, socksUser.text, socksPassword.text, assetLimit.value, cdnLimit.value)) {
            httpPassword.clear();
            socksPassword.clear();
        }
    }

    Layout.fillWidth: true
    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr("Upstream network")
    subtitle: qsTr("Advanced routing for VPNs, corporate proxies, and constrained networks.")

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Transport")
        description: qsTr("Auto is recommended. Manual transports require a reachable endpoint.")
        iconText: "⇅"

        FluentComboBox {
            id: mode

            model: [qsTr("Automatic"), qsTr("Direct IP"), qsTr("System proxy"), qsTr("HTTP CONNECT"), qsTr("SOCKS5")]
            Accessible.name: qsTr("Upstream transport")
        }
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 720 ? 2 : 1
        columnSpacing: Theme.spaceSm
        rowSpacing: Theme.spaceXs

        ColumnLayout {
            id: httpSection

            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: Theme.spaceXs
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("HTTP CONNECT proxy")

            Label {
                Layout.fillWidth: true
                text: qsTr("HTTP CONNECT")
                color: Theme.textPrimary
                font.pointSize: TypeScale.label
                font.weight: TypeScale.semibold
            }

            GridLayout {
                Layout.fillWidth: true
                columns: httpSection.width >= 360 ? 2 : 1
                columnSpacing: Theme.spaceXs
                rowSpacing: Theme.spaceXs

                FluentTextField {
                    id: httpHost
                    Layout.fillWidth: true
                    placeholderText: qsTr("Proxy host")
                    Accessible.name: qsTr("HTTP proxy host")
                }
                FluentSpinBox {
                    id: httpPort
                    from: 0
                    to: 65535
                    editable: true
                    Accessible.name: qsTr("HTTP proxy port")
                }
                FluentTextField {
                    id: httpUser
                    Layout.fillWidth: true
                    placeholderText: qsTr("Username (optional)")
                    Accessible.name: qsTr("HTTP proxy username")
                }
                FluentTextField {
                    id: httpPassword
                    Layout.fillWidth: true
                    placeholderText: root.controller.httpProxyPasswordStored ? qsTr("Stored · enter to replace") : qsTr("Password (optional)")
                    echoMode: TextInput.Password
                    Accessible.name: qsTr("HTTP proxy password")
                }
                FluentButton {
                    Layout.columnSpan: httpSection.width >= 360 ? 2 : 1
                    Layout.alignment: Qt.AlignRight
                    text: qsTr("Clear stored password")
                    flat: true
                    enabled: root.controller.httpProxyPasswordStored
                    onClicked: root.controller.clearUpstreamPassword("http")
                }
            }
        }

        ColumnLayout {
            id: socksSection

            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: Theme.spaceXs
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("SOCKS5 proxy")

            Label {
                Layout.fillWidth: true
                text: qsTr("SOCKS5")
                color: Theme.textPrimary
                font.pointSize: TypeScale.label
                font.weight: TypeScale.semibold
            }

            GridLayout {
                Layout.fillWidth: true
                columns: socksSection.width >= 360 ? 2 : 1
                columnSpacing: Theme.spaceXs
                rowSpacing: Theme.spaceXs

                FluentTextField {
                    id: socksHost
                    Layout.fillWidth: true
                    placeholderText: qsTr("Proxy host")
                    Accessible.name: qsTr("SOCKS5 proxy host")
                }
                FluentSpinBox {
                    id: socksPort
                    from: 0
                    to: 65535
                    editable: true
                    Accessible.name: qsTr("SOCKS5 proxy port")
                }
                FluentTextField {
                    id: socksUser
                    Layout.fillWidth: true
                    placeholderText: qsTr("Username (optional)")
                    Accessible.name: qsTr("SOCKS5 proxy username")
                }
                FluentTextField {
                    id: socksPassword
                    Layout.fillWidth: true
                    placeholderText: root.controller.socksProxyPasswordStored ? qsTr("Stored · enter to replace") : qsTr("Password (optional)")
                    echoMode: TextInput.Password
                    Accessible.name: qsTr("SOCKS5 proxy password")
                }
                FluentButton {
                    Layout.columnSpan: socksSection.width >= 360 ? 2 : 1
                    Layout.alignment: Qt.AlignRight
                    text: qsTr("Clear stored password")
                    flat: true
                    enabled: root.controller.socksProxyPasswordStored
                    onClicked: root.controller.clearUpstreamPassword("socks5")
                }
            }
        }
    }

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Connection limits")
        description: qsTr("Lower these values when a VPN throttles parallel Roblox requests.")
        iconText: "≡"

        RowLayout {
            Label {
                text: qsTr("Assets")
                color: Theme.textSecondary
            }
            FluentSpinBox {
                id: assetLimit
                from: 1
                to: 128
                editable: true
                Accessible.name: qsTr("Asset delivery connection limit")
            }
            Label {
                text: qsTr("CDN")
                color: Theme.textSecondary
            }
            FluentSpinBox {
                id: cdnLimit
                from: 1
                to: 256
                editable: true
                Accessible.name: qsTr("CDN connection limit")
            }
        }
    }

    FluentButton {
        Layout.alignment: Qt.AlignRight
        text: qsTr("Save and restart proxy")
        highlighted: true
        onClicked: root.save()
    }

    Component.onCompleted: syncFields()

    Connections {
        target: root.controller

        function onValuesChanged() {
            root.syncFields();
        }
    }
}
