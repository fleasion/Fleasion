import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    property string title
    property string description
    property string statusText: qsTr("Migration in progress")
    property string actionText
    property bool actionEnabled: false
    signal actionTriggered

    implicitHeight: content.implicitHeight + 32
    radius: Theme.radiusLg
    color: Theme.surfaceSubtle
    border.width: 1
    border.color: Theme.border
    Accessible.role: Accessible.Grouping
    Accessible.name: root.title
    Accessible.description: root.description

    ColumnLayout {
        id: content

        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Label {
                Layout.fillWidth: true
                text: root.title
                color: Theme.textPrimary
                font.pointSize: TypeScale.subtitle
                font.weight: TypeScale.semibold
                wrapMode: Text.Wrap
            }

            StatusPill {
                text: root.statusText
                status: "info"
            }
        }

        Label {
            Layout.fillWidth: true
            text: root.description
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentButton {
            Layout.alignment: Qt.AlignLeft
            visible: root.actionText.length > 0
            enabled: root.actionEnabled
            text: root.actionText
            Accessible.description: root.actionEnabled ? "" : qsTr("This feature is not available yet")
            onClicked: root.actionTriggered()
        }
    }
}
