import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Item {
    id: root

    required property var controller
    implicitHeight: row.implicitHeight

    RowLayout {
        id: row
        anchors.fill: parent
        spacing: Theme.spaceSm

        Card {
            Layout.fillWidth: true
            flat: true
            padding: Theme.spaceXs
            contentSpacing: 2
            title: qsTr("Configured")

            Label {
                text: root.controller.model.count.toLocaleString()
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
            title: qsTr("Applied")

            RowLayout {
                StatusPill {
                    text: root.controller.appliedCount.toLocaleString()
                    status: "success"
                }
                Label {
                    text: qsTr("ready")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                }
            }
        }

        Card {
            Layout.fillWidth: true
            flat: true
            padding: Theme.spaceXs
            contentSpacing: 2
            title: qsTr("Needs attention")

            RowLayout {
                StatusPill {
                    text: root.controller.problemCount.toLocaleString()
                    status: root.controller.problemCount > 0 ? "error" : "neutral"
                }
                Label {
                    text: root.controller.problemCount > 0 ? qsTr("review") : qsTr("clear")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                }
            }
        }
    }
}
