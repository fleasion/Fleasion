import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string heading: qsTr("Confirm action")
    property string message
    property string details
    property string acceptText: qsTr("Continue")
    property string rejectText: qsTr("Cancel")
    property bool destructive: false
    signal confirmed

    width: Math.min(520, parent ? parent.width - Theme.spaceXxl : 520)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: heading
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: root.heading
            color: Theme.textPrimary
            font.pointSize: TypeScale.title
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            visible: root.details.length > 0
            implicitHeight: detailLabel.implicitHeight + Theme.spaceLg
            radius: Theme.radiusMd
            color: root.destructive ? Theme.dangerSubtle : Theme.surfaceSubtle

            Label {
                id: detailLabel

                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                text: root.details
                color: root.destructive ? Theme.danger : Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: root.rejectText
                onClicked: root.reject()
            }

            FluentButton {
                text: root.acceptText
                highlighted: true
                danger: root.destructive
                Accessible.description: root.destructive ? qsTr("This action can remove data") : ""
                onClicked: {
                    root.confirmed();
                    root.accept();
                }
            }
        }
    }
}
