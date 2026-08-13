import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string title: ''
    property string subtitle: ''
    property string actionText: ''
    default property alias actions: actionRow.data

    signal actionTriggered

    horizontalPadding: 0
    verticalPadding: Theme.spaceXxs
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    Accessible.role: Accessible.Grouping
    Accessible.name: title
    background: null

    contentItem: RowLayout {
        spacing: Theme.spaceSm

        ColumnLayout {
            spacing: Theme.spaceXxs
            Layout.fillWidth: true

            Label {
                text: root.title
                color: Theme.textPrimary
                font.pointSize: TypeScale.subtitle
                font.weight: TypeScale.semibold
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            Label {
                visible: root.subtitle.length > 0
                text: root.subtitle
                color: Theme.textSecondary
                font.pointSize: TypeScale.body
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        RowLayout {
            id: actionRow

            spacing: Theme.spaceXs
            Layout.alignment: Qt.AlignTop | Qt.AlignRight

            FluentButton {
                visible: root.actionText.length > 0
                text: root.actionText
                onClicked: root.actionTriggered()
            }
        }
    }
}
