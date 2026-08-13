pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    spacing: Theme.sectionGap

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Roblox allowlisted presets")
        subtitle: qsTr("Safe local ClientSettings controls, kept separate from advanced live overrides.")
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 720 ? 4 : 2
        columnSpacing: Theme.spaceSm
        rowSpacing: Theme.spaceXs

        StatusPill {
            text: {
                if (root.controller.presetTask.busy)
                    return qsTr("Applying");
                if (root.controller.presetDirty)
                    return qsTr("Changes pending");
                if (root.controller.allowlistedFastFlagsEnabled)
                    return qsTr("Applied");
                return qsTr("Defaults");
            }
            status: root.controller.presetTask.busy ? "info" : root.controller.presetDirty ? "warning" : root.controller.allowlistedFastFlagsEnabled ? "success" : "neutral"
        }

        Label {
            Layout.fillWidth: true
            text: root.controller.presetTask.busy ? root.controller.presetTask.message : root.controller.presetDirty ? qsTr("Apply when the Roblox Player is closed for a clean update.") : qsTr("Changes are written to every detected Roblox installation.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        FluentButton {
            text: qsTr("Reset")
            flat: true
            enabled: !root.controller.presetTask.busy
            onClicked: resetLoader.active = true
        }

        FluentButton {
            text: qsTr("Apply presets")
            highlighted: true
            enabled: root.controller.presetDirty && !root.controller.presetTask.busy
            onClicked: root.controller.applyAllowlistedFastFlags()
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    AllowlistedRenderingSettings {
        Layout.fillWidth: true
        controller: root.controller
        enabled: !root.controller.presetTask.busy
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    AllowlistedQualitySettings {
        Layout.fillWidth: true
        controller: root.controller
        enabled: !root.controller.presetTask.busy
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    AllowlistedDebugSettings {
        Layout.fillWidth: true
        controller: root.controller
        enabled: !root.controller.presetTask.busy
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    GlobalFramerateSetting {
        Layout.fillWidth: true
        controller: root.controller
        enabled: !root.controller.presetTask.busy
    }

    Loader {
        id: resetLoader

        active: false
        sourceComponent: Component {
            AllowlistedResetDialog {
                onResetConfirmed: root.controller.resetAllowlistedFastFlags()
                onClosed: resetLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as AllowlistedResetDialog).open();
        }
    }
}
