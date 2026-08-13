import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

Control {
    id: root

    property string labelText
    property string valueText
    property string copyText
    property string openUrl
    signal copyRequested(string value)
    signal openRequested(string value)

    padding: Theme.spaceXs
    implicitHeight: Math.max(42, implicitContentHeight + topPadding + bottomPadding)
    Accessible.role: Accessible.Grouping
    Accessible.name: labelText
    Accessible.description: valueText

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Label {
                Layout.fillWidth: true
                text: root.labelText
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: root.valueText || qsTr('Unknown')
                color: Theme.textPrimary
                font.pointSize: TypeScale.label
                elide: Text.ElideMiddle
            }
        }

        IconButton {
            visible: root.copyText.length > 0
            controlSize: 30
            iconText: '⎘'
            text: qsTr('Copy %1').arg(root.labelText)
            onClicked: root.copyRequested(root.copyText)
        }

        IconButton {
            visible: root.openUrl.length > 0
            controlSize: 30
            iconText: '↗'
            text: qsTr('Open %1').arg(root.labelText)
            onClicked: root.openRequested(root.openUrl)
        }
    }

    background: Rectangle {
        color: Theme.surfaceSubtle
        radius: Theme.radiusSm
    }
}
