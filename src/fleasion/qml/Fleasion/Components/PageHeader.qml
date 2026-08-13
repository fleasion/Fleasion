import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string title: ''
    property string subtitle: ''
    property string iconText: ''
    default property alias actions: actionRow.data

    horizontalPadding: 0
    verticalPadding: Theme.spaceXxs
    implicitWidth: 560
    implicitHeight: Math.max(68, implicitContentHeight + topPadding + bottomPadding)
    Accessible.role: Accessible.Grouping
    Accessible.name: title
    background: null

    contentItem: RowLayout {
        spacing: Theme.spaceMd

        Label {
            visible: root.iconText.length > 0
            text: root.iconText
            color: Theme.accent
            font.pointSize: TypeScale.title
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Layout.preferredWidth: 34
            Layout.preferredHeight: 34
            Accessible.ignored: true
        }

        ColumnLayout {
            spacing: Theme.spaceXxs
            Layout.fillWidth: true

            Label {
                text: root.title
                color: Theme.textPrimary
                font.pointSize: TypeScale.display
                font.weight: TypeScale.semibold
                elide: Text.ElideRight
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
        }
    }
}
