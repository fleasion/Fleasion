import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    signal resetConfirmed

    parent: Controls.Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(480, parent.width - Theme.spaceXxl)
    modal: true
    padding: Theme.spaceLg
    title: qsTr("Reset allowlisted FastFlags?")
    closePolicy: Controls.Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Controls.Label {
            Layout.fillWidth: true
            text: qsTr("This restores Fleasion's ClientSettings backup, resets the global framerate cap, and returns every allowlisted preset to its default.")
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Controls.Label {
            Layout.fillWidth: true
            text: qsTr("Your separate custom live FastFlags are not changed.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }

            FluentButton {
                text: qsTr("Reset presets")
                danger: true
                onClicked: {
                    root.resetConfirmed();
                    root.close();
                }
            }
        }
    }

    background: Rectangle {
        color: Theme.surfaceElevated
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.borderStrong
    }
}
