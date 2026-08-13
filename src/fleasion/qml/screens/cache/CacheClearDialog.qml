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
    width: Math.min(500, parent.width - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Clear Fleasion cache?')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr('%n cached asset(s) and their local index entries will be removed.', '', root.controller.totalAssets)
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: warningLabel.implicitHeight + Theme.spaceLg
            color: Theme.dangerSubtle
            radius: Theme.radiusMd

            Label {
                id: warningLabel

                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                text: qsTr('This cannot be undone. Export anything you want to keep before continuing.')
                color: Theme.danger
                font.pointSize: TypeScale.label
                wrapMode: Text.Wrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr('Cancel')
                onClicked: root.reject()
            }

            FluentButton {
                text: qsTr('Clear cache')
                danger: true
                enabled: !root.controller.task.busy
                Accessible.description: qsTr('Permanently removes cached assets')
                onClicked: {
                    if (root.controller.clearCache())
                        root.accept();
                }
            }
        }
    }
}
