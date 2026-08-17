pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property var controller
    property string pendingHotkeyName

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
            checkable: false
            enabled: root.controller.customFastFlagsAvailable
            Accessible.name: qsTr("Enable custom FastFlags")
            onClicked: {
                if (root.controller.fastFlagsEnabled) {
                    root.controller.fastFlagsEnabled = false;
                } else if (root.controller.customFastFlagsWarningAccepted) {
                    root.controller.fastFlagsEnabled = true;
                } else {
                    riskLoader.active = true;
                }
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

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        SearchBox {
            id: customFlagSearch

            Layout.fillWidth: true
            placeholderText: qsTr("Search custom FastFlags")
            accessibleName: qsTr("Search custom FastFlags")
            onTextChanged: root.controller.filterFastFlags(text, familyFilter.currentText)
        }

        FluentComboBox {
            id: familyFilter

            Layout.preferredWidth: 156
            model: root.controller.fastFlagFamilies
            Accessible.name: qsTr("FastFlag family")
            onActivated: root.controller.filterFastFlags(customFlagSearch.text, currentText)
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

        delegate: CustomFastFlagDelegate {
            id: flagDelegate

            required property var model

            flagName: model.name
            flagValue: model.value
            family: model.family
            flagEnabled: model.enabled
            keybind: model.keybind
            hasKeybind: model.hasKeybind
            hotkeysSupported: root.controller.hotkeysSupported
            onEnabledRequested: enabled => root.controller.setFastFlagEnabled(flagDelegate.flagName, enabled)
            onHotkeyRequested: {
                root.pendingHotkeyName = flagDelegate.flagName;
                hotkeyLoader.active = true;
            }
            onRemoveRequested: root.controller.removeFastFlag(flagDelegate.flagName)
        }

        ScrollBar.vertical: FluentScrollBar {}
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.preferredHeight: 148
        visible: root.controller.fastFlagsModel.count === 0
        iconText: "⚑"
        title: customFlagSearch.text.length > 0 || familyFilter.currentIndex > 0 ? qsTr("No matching FastFlags") : qsTr("No custom FastFlags")
        description: customFlagSearch.text.length > 0 || familyFilter.currentIndex > 0 ? qsTr("Clear the search or choose another family.") : qsTr("Add one manually or browse the Roblox catalog.")
        actionText: customFlagSearch.text.length > 0 || familyFilter.currentIndex > 0 ? qsTr("Clear filters") : qsTr("Browse catalog")
        onActionTriggered: {
            if (customFlagSearch.text.length > 0 || familyFilter.currentIndex > 0) {
                customFlagSearch.clear();
                familyFilter.currentIndex = 0;
                root.controller.filterFastFlags("", "All");
            } else {
                catalogLoader.active = true;
            }
        }
    }

    Loader {
        id: riskLoader

        active: false
        sourceComponent: Component {
            CustomFastFlagRiskDialog {
                onConfirmed: root.controller.fastFlagsEnabled = true
                onClosed: riskLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as CustomFastFlagRiskDialog).open();
        }
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

    Loader {
        id: hotkeyLoader

        active: false
        sourceComponent: Component {
            FastFlagHotkeyDialog {
                controller: root.controller
                flagName: root.pendingHotkeyName
                onClosed: hotkeyLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as FastFlagHotkeyDialog).open();
        }
    }
}
