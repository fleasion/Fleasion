pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    required property var appController
    property bool cacheBusy: false

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    text: qsTr('TexturePack slots')
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.body
                    font.weight: TypeScale.semibold
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr('Use the pack:slot value in Replacer. The map asset ID is only the source image.')
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    wrapMode: Text.Wrap
                }
            }

            FluentButton {
                compact: true
                flat: true
                text: qsTr('Copy XML')
                enabled: root.controller.xmlText.length > 0
                onClicked: root.appController.copyText(root.controller.xmlText)
            }

            FluentButton {
                compact: true
                text: qsTr('Export captured (%1)').arg(root.controller.capturedCount)
                enabled: root.controller.capturedCount > 0
                onClicked: root.controller.exportAllCapturedSlots()
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.controller.loaded ? 0 : 1

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: root.controller.model
                spacing: 0
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                reuseItems: true
                Accessible.name: qsTr('TexturePack map slots')

                delegate: TexturePackSlotDelegate {
                    required property int index
                    required property var model

                    width: ListView.view.width
                    controller: root.controller
                    appController: root.appController
                    rowIndex: index
                    mapName: model.name
                    slotLabel: model.slotLabel
                    slotKey: model.slotKey
                    assetId: model.assetId
                    hashValue: model.hash
                    sizeText: model.sizeText
                    imageSource: model.imageSource
                    cached: model.cached
                    captured: model.captured
                    capturedSizeText: model.capturedSizeText
                    cacheBusy: root.cacheBusy
                }

                ScrollBar.vertical: FluentScrollBar {}
            }

            EmptyState {
                Layout.fillWidth: true
                Layout.fillHeight: true
                iconText: '▧'
                title: qsTr('TexturePack slots unavailable')
                description: root.controller.errorText
            }
        }
    }
}
