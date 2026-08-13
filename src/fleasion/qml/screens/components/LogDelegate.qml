import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property string time
    required property string category
    required property string message
    required property string text
    property string query
    readonly property bool matchesQuery: query.length === 0 || text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) >= 0
    signal copyRequested(string text)

    implicitHeight: matchesQuery ? Math.max(46, row.implicitHeight + Theme.spaceSm) : 0
    visible: implicitHeight > 0
    color: pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: Theme.radiusSm
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("%1, %2, %3").arg(root.time).arg(root.category).arg(root.message)

    RowLayout {
        id: row

        anchors.fill: parent
        anchors.margins: Theme.spaceXs
        spacing: Theme.spaceXs

        Label {
            Layout.preferredWidth: 64
            Layout.alignment: Qt.AlignTop
            text: root.time
            color: Theme.textTertiary
            font.family: "monospace"
            font.pointSize: TypeScale.caption
        }

        Label {
            Layout.preferredWidth: 86
            Layout.alignment: Qt.AlignTop
            text: root.category
            color: Theme.accent
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
        }

        Label {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textPrimary
            font.family: "monospace"
            font.pointSize: TypeScale.label
            wrapMode: Text.WrapAnywhere
        }

        IconButton {
            iconText: "⧉"
            text: qsTr("Copy log entry")
            controlSize: 32
            onClicked: root.copyRequested(root.message)
        }
    }

    HoverHandler {
        id: pointer
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
