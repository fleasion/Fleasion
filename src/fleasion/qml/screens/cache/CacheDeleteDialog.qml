import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property int assetCount: 0
    signal confirmed

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent.width - Theme.spaceXxl)
    modal: true
    focus: true
    title: qsTr("Delete cached assets?")
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("%n selected cached asset(s) will be permanently deleted from disk.", "", root.assetCount)
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr("Delete")
                danger: true
                Accessible.description: qsTr("Permanently removes the selected cached assets")
                onClicked: {
                    root.confirmed();
                    root.accept();
                }
            }
        }
    }
}
