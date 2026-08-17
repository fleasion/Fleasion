pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../cache" as Cache

Item {
    id: root

    required property var controller
    required property var appController
    readonly property var preview: root.controller.valuePreview

    implicitWidth: 360

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.border
        Accessible.ignored: true
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    text: root.controller.selectedValuePath
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.body
                    font.weight: TypeScale.semibold
                    elide: Text.ElideMiddle
                }

                Label {
                    Layout.fillWidth: true
                    text: root.preview.sourceLabel || root.controller.selectedValueText
                    color: Theme.textSecondary
                    font.family: 'monospace'
                    font.pointSize: TypeScale.caption
                    elide: Text.ElideMiddle
                }
            }

            StatusPill {
                text: root.preview.previewKind === 'none' ? qsTr('Preview') : root.preview.previewKind
                status: root.preview.previewKind === 'error' ? 'danger' : root.preview.task.busy ? 'warning' : 'info'
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.preview.task.busy
            spacing: Theme.spaceXs

            BusyIndicator {
                running: visible
                Accessible.name: root.preview.task.message
            }

            Label {
                Layout.fillWidth: true
                text: root.preview.task.message
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideRight
            }

            FluentButton {
                text: qsTr('Cancel')
                compact: true
                flat: true
                onClicked: root.preview.cancel()
            }
        }

        Cache.AssetPreview {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180
            controller: root.preview
            appController: root.appController
            autoLoad: false
            flat: true
        }

        Label {
            Layout.fillWidth: true
            visible: root.preview.errorText.length > 0
            text: root.preview.errorText
            color: Theme.danger
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
            Accessible.role: Accessible.AlertMessage
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            Label {
                Layout.fillWidth: true
                text: root.preview.sizeText
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }

            FluentButton {
                visible: root.preview.previewKind === 'text' || root.preview.previewKind === 'hex'
                text: qsTr('Copy preview')
                compact: true
                flat: true
                onClicked: root.appController.copyText(root.preview.previewText)
            }

            FluentButton {
                visible: root.preview.canCopyImage
                text: qsTr('Copy image')
                compact: true
                flat: true
                onClicked: root.preview.copyImage()
            }

            FluentButton {
                text: qsTr('Copy value')
                compact: true
                onClicked: root.appController.copyText(root.controller.selectedValueText)
            }
        }
    }
}
