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
    height: Math.min(420, parent.height - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Hidden cache IDs')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr('Hide assets from the browser by ID. This only filters the list; cached files and live capture are not changed.')
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentTextArea {
            id: blacklistField

            Layout.fillWidth: true
            Layout.fillHeight: true
            placeholderText: qsTr('1818, 1234567890')
            Accessible.name: qsTr('Asset IDs to hide')
            Accessible.description: qsTr('Separate IDs with commas, spaces, semicolons, or new lines')
            wrapMode: TextEdit.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            StatusPill {
                text: qsTr('%n ID(s) currently hidden', '', root.controller.blacklistCount)
                status: root.controller.blacklistCount > 0 ? 'warning' : 'neutral'
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr('Show all')
                flat: true
                enabled: root.controller.blacklistCount > 0
                onClicked: {
                    root.controller.clearBlacklist();
                    root.accept();
                }
            }

            FluentButton {
                text: qsTr('Cancel')
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr('Apply')
                highlighted: true
                onClicked: {
                    root.controller.applyBlacklist(blacklistField.text);
                    root.accept();
                }
            }
        }
    }

    onOpened: {
        blacklistField.text = controller.blacklistText;
        blacklistField.forceActiveFocus();
    }
}
