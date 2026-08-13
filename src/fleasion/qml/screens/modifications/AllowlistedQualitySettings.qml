pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    readonly property var textureValues: ["Default", "0", "1", "2", "3"]
    readonly property var textureLabels: [qsTr("Default"), qsTr("Level 0 — lowest"), qsTr("Level 1"), qsTr("Level 2"), qsTr("Level 3 — highest")]

    spacing: Theme.spaceXs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Quality overrides")
        subtitle: qsTr("Pin texture, geometry, and frame rendering quality when Roblox's automatic choices are not ideal.")
    }

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Texture quality")
        description: qsTr("Override Roblox's automatic texture quality")
        iconText: "▦"

        FluentComboBox {
            Layout.preferredWidth: 190
            model: root.textureLabels
            currentIndex: Math.max(0, root.textureValues.indexOf(root.controller.presetTextureQuality))
            Accessible.name: qsTr("Texture quality")
            onActivated: index => root.controller.presetTextureQuality = root.textureValues[index]
        }
    }

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Mesh level of detail")
        description: qsTr("Override the distance thresholds used for CSG meshes")
        iconText: "◇"

        FluentSwitch {
            checked: root.controller.presetMeshLodEnabled
            Accessible.name: qsTr("Enable mesh LOD override")
            onToggled: root.controller.presetMeshLodEnabled = checked
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.spaceSm
        Layout.rightMargin: Theme.spaceSm
        spacing: Theme.spaceSm
        enabled: root.controller.presetMeshLodEnabled

        PresetSlider {
            Layout.fillWidth: true
            from: 0
            to: 4
            stepSize: 1
            value: root.controller.presetMeshLod
            Accessible.name: qsTr("Mesh level of detail")
            onMoved: root.controller.presetMeshLod = Math.round(value)
        }

        StatusPill {
            Layout.preferredWidth: 92
            text: root.controller.presetMeshLod === 0 ? qsTr("Default") : qsTr("Level %1").arg(root.controller.presetMeshLod - 1)
            status: root.controller.presetMeshLodEnabled ? "info" : "neutral"
        }
    }

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("FRM quality")
        description: qsTr("Set Roblox's frame rendering manager quality level")
        iconText: "◈"

        FluentSwitch {
            checked: root.controller.presetFrmEnabled
            Accessible.name: qsTr("Enable FRM quality override")
            onToggled: root.controller.presetFrmEnabled = checked
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: Theme.spaceSm
        Layout.rightMargin: Theme.spaceSm
        spacing: Theme.spaceSm
        enabled: root.controller.presetFrmEnabled

        PresetSlider {
            Layout.fillWidth: true
            from: 0
            to: 21
            stepSize: 1
            value: root.controller.presetFrmQuality
            Accessible.name: qsTr("FRM quality")
            onMoved: root.controller.presetFrmQuality = Math.round(value)
        }

        StatusPill {
            Layout.preferredWidth: 92
            text: root.controller.presetFrmQuality === 0 ? qsTr("Default") : qsTr("Quality %1").arg(root.controller.presetFrmQuality)
            status: root.controller.presetFrmEnabled ? "info" : "neutral"
        }
    }
}
