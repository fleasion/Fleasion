import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, parent.width - Theme.spaceXl)
    height: Math.min(430, parent.height - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Load assets from Roblox')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr('Enter up to 100 asset IDs. Public assets work without an account; Fleasion uses the stored Roblox session when a private asset requires it.')
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentTextArea {
            id: assetIdsField

            Layout.fillWidth: true
            Layout.fillHeight: true
            readOnly: root.controller.task.busy
            placeholderText: qsTr('1818, 1234567890, 9876543210')
            Accessible.name: qsTr('Roblox asset IDs to load')
            Accessible.description: qsTr('Separate IDs with commas, spaces, semicolons, or new lines')
            wrapMode: TextEdit.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            BusyIndicator {
                visible: root.controller.task.busy
                running: visible
                Accessible.name: root.controller.task.message
            }

            Label {
                Layout.fillWidth: true
                visible: root.controller.task.busy
                text: root.controller.task.message
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideRight
            }

            FluentButton {
                text: qsTr('Cancel')
                enabled: !root.controller.task.busy
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr('Load assets')
                highlighted: true
                enabled: !root.controller.task.busy && assetIdsField.text.trim().length > 0
                onClicked: {
                    if (root.controller.loadAssets(assetIdsField.text))
                        root.accept();
                }
            }
        }
    }

    onOpened: {
        assetIdsField.clear();
        assetIdsField.forceActiveFocus();
    }
}
