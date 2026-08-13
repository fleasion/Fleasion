import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    property string heading: qsTr("Fleasion")
    property string message
    property string detail
    property string status: "info"

    width: Math.min(500, parent ? parent.width - Theme.spaceXxl : 500)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    title: heading
    standardButtons: Dialog.Ok

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            StatusPill {
                text: root.status === "success" ? qsTr("Complete") : root.status === "warning" ? qsTr("Attention") : root.status === "error" ? qsTr("Error") : qsTr("Notice")
                status: root.status
            }

            Label {
                Layout.fillWidth: true
                text: root.heading
                color: Theme.textPrimary
                font.pointSize: TypeScale.title
                wrapMode: Text.Wrap
            }
        }

        Label {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            visible: root.detail.length > 0
            text: root.detail
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }
    }
}
