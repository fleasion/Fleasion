import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    default property alias columns: headerRow.data

    leftPadding: Theme.spaceSm
    rightPadding: Theme.spaceSm
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    implicitWidth: 480
    implicitHeight: 36
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr('Table header')

    contentItem: RowLayout {
        id: headerRow

        spacing: Theme.spaceXs
    }

    background: Rectangle {
        color: Theme.surfaceSubtle
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.border
    }
}
