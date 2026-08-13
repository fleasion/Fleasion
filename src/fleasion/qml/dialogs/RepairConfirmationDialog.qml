import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string message
    property string acceptText: qsTr("Continue")
    signal confirmed

    width: Math.min(520, parent ? parent.width - Theme.spaceXxl : 520)
    modal: true
    focus: true
    title: qsTr("Confirm startup repair")
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: scopeLabel.implicitHeight + Theme.spaceMd
            radius: Theme.radiusSm
            color: Theme.warningSubtle

            Label {
                id: scopeLabel

                anchors.fill: parent
                anchors.margins: Theme.spaceXs
                text: qsTr("Only the described Fleasion helper or program-specific firewall rules will be changed.")
                color: Theme.warning
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                compact: true
                onClicked: root.reject()
            }

            FluentButton {
                text: root.acceptText
                compact: true
                highlighted: true
                onClicked: {
                    root.confirmed();
                    root.accept();
                }
            }
        }
    }
}
