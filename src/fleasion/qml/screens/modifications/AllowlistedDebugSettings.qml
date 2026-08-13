pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    spacing: Theme.spaceXs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("World and debug options")
        subtitle: qsTr("Tune sky, voxel, and grass behavior with Roblox's local allowlist.")
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 800 ? 2 : 1
        columnSpacing: Theme.spaceMd
        rowSpacing: Theme.spaceXxs

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Grey sky")
            description: qsTr("Use Roblox's neutral debug sky")
            iconText: "☁"

            FluentSwitch {
                checked: root.controller.presetGreySky
                Accessible.name: qsTr("Grey sky")
                onToggled: root.controller.presetGreySky = checked
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Pause voxelizer")
            description: qsTr("Pause voxel lighting updates for debugging")
            iconText: "Ⅱ"

            FluentSwitch {
                checked: root.controller.presetPauseVoxelizer
                Accessible.name: qsTr("Pause voxelizer")
                onToggled: root.controller.presetPauseVoxelizer = checked
            }
        }
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 1050 ? 3 : root.width >= 700 ? 2 : 1
        columnSpacing: Theme.spaceMd
        rowSpacing: Theme.spaceXs

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Grass maximum distance")
            description: qsTr("0 keeps the Roblox default")

            FluentSpinBox {
                Layout.preferredWidth: 132
                from: 0
                to: 100000
                editable: true
                value: root.controller.presetGrassMax
                Accessible.name: qsTr("Grass maximum distance")
                onValueModified: root.controller.presetGrassMax = value
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Grass minimum distance")
            description: qsTr("0 keeps the Roblox default")

            FluentSpinBox {
                Layout.preferredWidth: 132
                from: 0
                to: 100000
                editable: true
                value: root.controller.presetGrassMin
                Accessible.name: qsTr("Grass minimum distance")
                onValueModified: root.controller.presetGrassMin = value
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Grass motion factor")
            description: qsTr("0 keeps the Roblox default")

            FluentSpinBox {
                Layout.preferredWidth: 132
                from: 0
                to: 100000
                editable: true
                value: root.controller.presetGrassMotion
                Accessible.name: qsTr("Grass motion factor")
                onValueModified: root.controller.presetGrassMotion = value
            }
        }
    }
}
