pragma ComponentBehavior: Bound

import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ItemDelegate {
    id: root

    required property string trackName
    required property int sampleCount
    required property string coverageText

    implicitHeight: 36
    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceXs
    hoverEnabled: true
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr('%1, %n sample(s), %2', '', root.sampleCount).arg(root.trackName).arg(root.coverageText)

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        Label {
            Layout.fillWidth: true
            text: root.trackName
            color: Theme.textPrimary
            font.pointSize: TypeScale.label
            font.weight: TypeScale.medium
            elide: Text.ElideRight
        }

        Label {
            text: qsTr('%n sample(s)', '', root.sampleCount)
            color: Theme.textSecondary
            font.pointSize: TypeScale.caption
        }

        Label {
            Layout.preferredWidth: 92
            text: root.coverageText
            color: Theme.textTertiary
            font.family: 'monospace'
            font.pointSize: TypeScale.caption
            horizontalAlignment: Text.AlignRight
        }
    }

    background: Rectangle {
        color: root.hovered ? Theme.surfaceHover : 'transparent'
        radius: Theme.radiusSm
    }
}
