pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    readonly property var renderingValues: ["Default", "D3D11", "Vulkan", "OpenGL"]
    readonly property var renderingLabels: [qsTr("Default"), qsTr("Direct3D 11"), qsTr("Vulkan"), qsTr("OpenGL")]
    readonly property var msaaValues: ["Default", "1", "2", "4"]
    readonly property var msaaLabels: [qsTr("Default"), qsTr("1× — lowest"), qsTr("2×"), qsTr("4× — highest")]

    spacing: Theme.spaceXs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Rendering")
        subtitle: qsTr("Choose the graphics path and display compatibility options.")
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 800 ? 2 : 1
        columnSpacing: Theme.spaceMd
        rowSpacing: Theme.spaceXxs

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Rendering mode")
            description: qsTr("Default lets Roblox choose the graphics API")
            iconText: "◫"

            FluentComboBox {
                Layout.preferredWidth: 170
                model: root.renderingLabels
                currentIndex: Math.max(0, root.renderingValues.indexOf(root.controller.presetRenderingMode))
                Accessible.name: qsTr("Rendering mode")
                onActivated: index => root.controller.presetRenderingMode = root.renderingValues[index]
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Multisample anti-aliasing")
            description: qsTr("Smooth jagged edges at a higher GPU cost")
            iconText: "✧"

            FluentComboBox {
                Layout.preferredWidth: 170
                model: root.msaaLabels
                currentIndex: Math.max(0, root.msaaValues.indexOf(root.controller.presetMsaa))
                Accessible.name: qsTr("MSAA level")
                onActivated: index => root.controller.presetMsaa = root.msaaValues[index]
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Fix display scaling")
            description: qsTr("Disable Roblox's automatic DPI scaling")
            iconText: "↔"

            FluentSwitch {
                checked: root.controller.presetDisableDpiScale
                Accessible.name: qsTr("Fix display scaling")
                onToggled: root.controller.presetDisableDpiScale = checked
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Alt+Enter fullscreen")
            description: qsTr("Let Roblox handle the fullscreen shortcut manually")
            iconText: "⛶"

            FluentSwitch {
                checked: root.controller.presetAltEnterFullscreen
                Accessible.name: qsTr("Alt+Enter fullscreen")
                onToggled: root.controller.presetAltEnterFullscreen = checked
            }
        }
    }
}
