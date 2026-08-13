pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property var controller
    signal enableRequested(bool enabled)

    title: qsTr("FastFlags")
    subtitle: qsTr("Tune Roblox with allowlisted local presets and optional advanced live overrides.")
    flat: true
    padding: Theme.spaceXs
    contentSpacing: Theme.sectionGap

    AllowlistedFastFlagsPanel {
        Layout.fillWidth: true
        controller: root.controller
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.borderStrong
        Accessible.ignored: true
    }

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Custom FastFlags")
        subtitle: qsTr("Advanced live ClientSettings overrides from a searchable Roblox catalog.")
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: warningRow.implicitHeight + Theme.spaceLg
        radius: Theme.radiusMd
        color: Theme.warningSubtle

        RowLayout {
            id: warningRow
            anchors.fill: parent
            anchors.margins: Theme.spaceSm
            spacing: Theme.spaceSm

            Label {
                text: "!"
                color: Theme.warning
                font.pointSize: TypeScale.subtitle
                font.weight: TypeScale.semibold
                Accessible.ignored: true
            }

            Label {
                Layout.fillWidth: true
                text: qsTr("Custom FastFlags bypass Roblox's local allowlist. Review every value and use this feature at your own risk.")
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                wrapMode: Text.Wrap
            }
        }
    }

    SettingRow {
        Layout.fillWidth: true
        title: qsTr("Enable custom FastFlags")
        description: qsTr("Intercept ClientSettings responses and merge your overrides")
        iconText: "⚑"

        FluentSwitch {
            checked: root.controller.fastFlagsEnabled
            enabled: root.controller.customFastFlagsAvailable
            Accessible.name: qsTr("Enable custom FastFlags")
            onToggled: {
                if (checked !== root.controller.fastFlagsEnabled)
                    root.enableRequested(checked);
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: Theme.border
        Accessible.ignored: true
    }

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Add or update a flag")
        subtitle: qsTr("Use the exact FastFlag name and a JSON-compatible value.")
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        FluentButton {
            text: qsTr("Import / export")
            onClicked: transferLoader.active = true
        }

        FluentButton {
            text: qsTr("Profiles")
            onClicked: profilesLoader.active = true
        }

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            text: qsTr("Browse catalog")
            onClicked: catalogLoader.active = true
        }
    }

    GridLayout {
        Layout.fillWidth: true
        columns: root.width >= 680 ? 3 : 1
        columnSpacing: Theme.spaceSm
        rowSpacing: Theme.spaceSm

        FluentTextField {
            id: flagName
            Layout.fillWidth: true
            placeholderText: qsTr("FFlagExample")
            Accessible.name: qsTr("FastFlag name")
        }

        FluentTextField {
            id: flagValue
            Layout.fillWidth: true
            placeholderText: qsTr("true")
            Accessible.name: qsTr("FastFlag value")
        }

        FluentButton {
            text: qsTr("Save flag")
            highlighted: true
            enabled: flagName.text.trim().length > 0
            onClicked: {
                if (root.controller.setFastFlag(flagName.text, flagValue.text)) {
                    flagName.clear();
                    flagValue.clear();
                    flagName.forceActiveFocus();
                }
            }
        }
    }

    ListView {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(contentHeight, 260)
        Layout.minimumHeight: root.controller.fastFlagsModel.count > 0 ? 52 : 0
        clip: true
        spacing: Theme.spaceXxs
        model: root.controller.fastFlagsModel
        boundsBehavior: Flickable.StopAtBounds
        reuseItems: true

        delegate: Rectangle {
            id: flagDelegate

            required property string name
            required property string value
            required property string family

            width: ListView.view.width
            height: Theme.largeControlHeight
            radius: Theme.radiusSm
            color: mouseArea.hovered ? Theme.surfaceHover : Theme.surfaceSubtle

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.spaceSm
                anchors.rightMargin: Theme.spaceXs
                spacing: Theme.spaceSm

                StatusPill {
                    text: flagDelegate.family
                    status: "neutral"
                }

                Label {
                    Layout.fillWidth: true
                    text: flagDelegate.name
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.label
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                }

                Label {
                    Layout.preferredWidth: 150
                    text: flagDelegate.value || qsTr("No value")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    elide: Text.ElideRight
                }

                IconButton {
                    iconText: "×"
                    text: qsTr("Remove %1").arg(flagDelegate.name)
                    onClicked: root.controller.removeFastFlag(flagDelegate.name)
                }
            }

            HoverHandler {
                id: mouseArea
            }
        }

        ScrollBar.vertical: ScrollBar {}
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.preferredHeight: 148
        visible: root.controller.fastFlagsModel.count === 0
        iconText: "⚑"
        title: qsTr("No custom FastFlags")
        description: qsTr("Add one manually or browse the Roblox catalog.")
        actionText: qsTr("Browse catalog")
        onActionTriggered: catalogLoader.active = true
    }

    Loader {
        id: catalogLoader

        active: false
        sourceComponent: Component {
            FastFlagCatalogDialog {
                controller: root.controller
                onClosed: catalogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as FastFlagCatalogDialog).open();
        }
    }

    Loader {
        id: transferLoader

        active: false
        sourceComponent: Component {
            FastFlagTransferDialog {
                controller: root.controller
                onClosed: transferLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as FastFlagTransferDialog).open();
        }
    }

    Loader {
        id: profilesLoader

        active: false
        sourceComponent: Component {
            FastFlagProfilesDialog {
                controller: root.controller
                onClosed: profilesLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as FastFlagProfilesDialog).open();
        }
    }
}
