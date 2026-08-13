import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    id: root

    required property var controller
    required property var settingsController
    readonly property var blacklist: controller.subplaceBlacklist

    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr('Subplace blacklist')
    subtitle: qsTr('Prevent Roblox from teleporting this client into selected subplaces while proxy interception is active.')

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        StatusPill {
            text: qsTr('%n blocked place(s)', '', root.blacklist.blacklistCount)
            status: root.blacklist.blacklistCount > 0 ? 'warning' : 'neutral'
        }

        StatusPill {
            text: root.settingsController && root.settingsController.proxyFeaturesEnabled ? qsTr('Proxy active') : qsTr('Proxy required')
            status: root.settingsController && root.settingsController.proxyFeaturesEnabled ? 'success' : 'warning'
        }

        Item {
            Layout.fillWidth: true
        }

        Label {
            visible: root.blacklist.bypassActive
            text: qsTr('Bypassed for %1 s').arg((root.blacklist.bypassMillisecondsRemaining / 1000).toFixed(1))
            color: Theme.warning
            font.pointSize: TypeScale.label
        }

        FluentButton {
            text: root.blacklist.bypassActive ? qsTr('Restart 5-second bypass') : qsTr('Allow joins for 5 seconds')
            enabled: root.blacklist.blacklistCount > 0
            Accessible.description: qsTr('Temporarily allow joins to every blacklisted subplace')
            onClicked: root.blacklist.bypassForFiveSeconds()
        }
    }

    Label {
        Layout.fillWidth: true
        visible: !root.settingsController || !root.settingsController.proxyFeaturesEnabled
        text: qsTr('The list is saved, but blocking takes effect only when proxy features are enabled in Settings.')
        color: Theme.warning
        font.pointSize: TypeScale.label
        wrapMode: Text.Wrap
    }

    Label {
        text: qsTr('Blacklisted place IDs')
        color: Theme.textPrimary
        font.pointSize: TypeScale.label
        font.weight: TypeScale.medium
    }

    FluentTextArea {
        id: blacklistInput

        Layout.fillWidth: true
        Layout.preferredHeight: 92
        text: root.blacklist.blacklistText
        placeholderText: qsTr('1818, 1234567890, 9876543210')
        wrapMode: TextEdit.WrapAnywhere
        selectByMouse: true
        Accessible.name: qsTr('Blacklisted Roblox place IDs')
        Accessible.description: qsTr('Separate IDs with commas, spaces, semicolons, or new lines')
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr('Invalid fragments are ignored; duplicate and zero-padded IDs are normalized.')
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }

        FluentButton {
            text: qsTr('Clear')
            flat: true
            enabled: blacklistInput.text.length > 0
            onClicked: blacklistInput.clear()
        }

        FluentButton {
            text: qsTr('Apply blacklist')
            highlighted: true
            onClicked: root.blacklist.applyBlacklist(blacklistInput.text)
        }
    }

    Label {
        text: qsTr('When a blacklisted teleport is requested')
        color: Theme.textPrimary
        font.pointSize: TypeScale.label
        font.weight: TypeScale.medium
    }

    ButtonGroup {
        id: blockModeGroup
    }

    FluentRadioButton {
        text: qsTr('Block immediately')
        checked: root.blacklist.mode === 'block'
        ButtonGroup.group: blockModeGroup
        Accessible.description: qsTr('Return a blocked teleport response to Roblox')
        onToggled: if (checked)
            root.blacklist.mode = 'block'
    }

    FluentRadioButton {
        text: qsTr('Keep the teleport waiting')
        checked: root.blacklist.mode === 'stall'
        ButtonGroup.group: blockModeGroup
        Accessible.description: qsTr('Return a queued response so Roblox continues waiting')
        onToggled: if (checked)
            root.blacklist.mode = 'stall'
    }
}
