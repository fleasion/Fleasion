import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property string iconText: '\u25c7'
    property string title: qsTr('Nothing here yet')
    property string description: ''
    property string actionText: ''
    property int maximumTextWidth: 520
    default property alias actions: actionRow.data

    signal actionTriggered

    implicitWidth: 420
    implicitHeight: content.implicitHeight + Theme.spaceXl * 2
    Accessible.role: Accessible.Grouping
    Accessible.name: title
    Accessible.description: description

    ColumnLayout {
        id: content

        anchors.centerIn: parent
        width: Math.min(root.width, root.maximumTextWidth)
        spacing: Theme.spaceSm

        Rectangle {
            color: Theme.accentSubtle
            radius: Theme.radiusXl
            Layout.preferredWidth: 64
            Layout.preferredHeight: 64
            Layout.alignment: Qt.AlignHCenter
            Accessible.ignored: true

            Label {
                anchors.centerIn: parent
                text: root.iconText
                color: Theme.accent
                font.pointSize: TypeScale.title
            }
        }

        Label {
            text: root.title
            color: Theme.textPrimary
            font.pointSize: TypeScale.subtitle
            font.weight: TypeScale.semibold
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            Accessible.ignored: true
        }

        Label {
            visible: root.description.length > 0
            text: root.description
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            Accessible.ignored: true
        }

        RowLayout {
            id: actionRow

            spacing: Theme.spaceXs
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: Theme.spaceXs

            FluentButton {
                visible: root.actionText.length > 0
                text: root.actionText
                onClicked: root.actionTriggered()
            }
        }
    }
}
