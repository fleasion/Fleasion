import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Item {
    id: root

    required property var controller

    implicitHeight: cards.implicitHeight

    GridLayout {
        id: cards

        anchors.fill: parent
        columns: root.width >= 740 ? 3 : 2
        columnSpacing: Theme.spaceSm
        rowSpacing: Theme.spaceSm

        Card {
            Layout.fillWidth: true
            flat: true
            padding: Theme.spaceXs
            contentSpacing: 2
            title: qsTr("Cached assets")

            Label {
                text: root.controller.totalAssets.toLocaleString()
                color: Theme.textPrimary
                font.pointSize: TypeScale.title
                font.weight: TypeScale.semibold
            }
        }

        Card {
            Layout.fillWidth: true
            flat: true
            padding: Theme.spaceXs
            contentSpacing: 2
            title: qsTr("Disk usage")

            Label {
                text: root.controller.totalSizeText
                color: Theme.textPrimary
                font.pointSize: TypeScale.title
                font.weight: TypeScale.semibold
            }
        }

        Card {
            Layout.fillWidth: true
            Layout.columnSpan: cards.columns === 2 ? 2 : 1
            flat: true
            padding: Theme.spaceXs
            contentSpacing: 2
            title: qsTr("Live capture")

            RowLayout {
                Layout.fillWidth: true

                StatusPill {
                    text: root.controller.scraperEnabled ? qsTr("Capturing") : qsTr("Paused")
                    status: root.controller.scraperEnabled ? "success" : "neutral"
                }

                Item {
                    Layout.fillWidth: true
                }

                FluentSwitch {
                    checked: root.controller.scraperEnabled
                    Accessible.name: qsTr("Capture assets from proxy traffic")
                    onToggled: root.controller.scraperEnabled = checked
                }
            }
        }
    }
}
