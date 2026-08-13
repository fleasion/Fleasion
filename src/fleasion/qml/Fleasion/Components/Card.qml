import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string title: ''
    property string subtitle: ''
    property bool flat: false
    property int contentSpacing: Theme.sectionGap
    default property alias contentData: body.data

    padding: Theme.panelPadding
    implicitWidth: 360
    implicitHeight: implicitContentHeight + topPadding + bottomPadding
    Accessible.role: Accessible.Grouping
    Accessible.name: title

    contentItem: ColumnLayout {
        spacing: root.contentSpacing

        ColumnLayout {
            visible: root.title.length > 0 || root.subtitle.length > 0
            spacing: Theme.spaceXxs
            Layout.fillWidth: true

            Label {
                visible: root.title.length > 0
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

        ColumnLayout {
            id: body

            spacing: root.contentSpacing
            Layout.fillWidth: true
        }
    }

    background: Rectangle {
        color: root.flat ? 'transparent' : Theme.surfaceSubtle
        radius: root.flat ? 0 : Theme.radiusMd
        border.width: root.flat ? 0 : 1
        border.color: Theme.border
    }
}
