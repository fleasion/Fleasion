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
    required property int rowIndex
    required property string mapName
    required property string slotLabel
    required property string slotKey
    required property string assetId
    required property string hashValue
    required property string sizeText
    required property string imageSource
    required property bool cached
    required property bool captured
    required property string capturedSizeText
    required property bool cacheBusy

    implicitHeight: content.implicitHeight + Theme.spaceSm * 2
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr('%1 map, %2, sub-asset %3').arg(mapName).arg(slotLabel).arg(assetId)

    ColumnLayout {
        id: content

        anchors.fill: parent
        anchors.leftMargin: Theme.spaceXs
        anchors.rightMargin: Theme.spaceXs
        anchors.topMargin: Theme.spaceSm
        anchors.bottomMargin: Theme.spaceSm
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Rectangle {
                Layout.preferredWidth: 88
                Layout.preferredHeight: 88
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: Theme.border
                clip: true

                Image {
                    id: mapImage

                    anchors.fill: parent
                    anchors.margins: 2
                    source: root.imageSource
                    sourceSize.width: 256
                    sourceSize.height: 256
                    asynchronous: true
                    cache: true
                    fillMode: Image.PreserveAspectFit
                    mipmap: true
                    Accessible.name: qsTr('%1 texture map preview').arg(root.mapName)
                }

                Label {
                    anchors.centerIn: parent
                    width: parent.width - Theme.spaceSm
                    visible: !root.cached || mapImage.status === Image.Error
                    text: root.cached ? qsTr('Preview unavailable') : qsTr('Map not cached')
                    color: Theme.textTertiary
                    font.pointSize: TypeScale.label
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs

                    Label {
                        Layout.fillWidth: true
                        text: root.mapName
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.body
                        font.weight: TypeScale.semibold
                        elide: Text.ElideRight
                    }

                    StatusPill {
                        text: root.slotLabel
                        status: 'info'
                    }
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr('Replacer ID  %1').arg(root.slotKey)
                    color: Theme.accent
                    font.family: 'monospace'
                    font.pointSize: TypeScale.label
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr('Map asset  %1%2').arg(root.assetId).arg(root.sizeText.length > 0 ? qsTr('  ·  %1').arg(root.sizeText) : '')
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    visible: root.hashValue.length > 0
                    text: qsTr('Hash  %1').arg(root.hashValue)
                    color: Theme.textTertiary
                    font.family: 'monospace'
                    font.pointSize: TypeScale.caption
                    elide: Text.ElideMiddle
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs

                    StatusPill {
                        text: root.cached ? qsTr('Map cached') : qsTr('Map missing')
                        status: root.cached ? 'success' : 'warning'
                    }

                    StatusPill {
                        text: root.captured ? qsTr('KTX2 %1').arg(root.capturedSizeText) : qsTr('No KTX2 capture')
                        status: root.captured ? 'success' : 'neutral'
                    }
                }
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: root.width >= 390 ? 4 : 2
            columnSpacing: Theme.spaceXs
            rowSpacing: Theme.spaceXs

            FluentButton {
                Layout.fillWidth: true
                compact: true
                text: root.cached ? qsTr('Copy image') : qsTr('Load map')
                enabled: root.cached || !root.cacheBusy
                onClicked: {
                    if (root.cached)
                        root.controller.copyMapImage(root.rowIndex);
                    else
                        root.controller.requestMap(root.rowIndex);
                }
            }

            FluentButton {
                Layout.fillWidth: true
                compact: true
                flat: true
                text: qsTr('Copy slot ID')
                onClicked: root.appController.copyText(root.slotKey)
            }

            FluentButton {
                Layout.fillWidth: true
                compact: true
                flat: true
                text: qsTr('Copy map ID')
                onClicked: root.appController.copyText(root.assetId)
            }

            FluentButton {
                Layout.fillWidth: true
                compact: true
                text: qsTr('Export KTX2')
                enabled: root.captured
                onClicked: root.controller.exportCapturedSlot(root.rowIndex)
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
        Accessible.ignored: true
    }
}
