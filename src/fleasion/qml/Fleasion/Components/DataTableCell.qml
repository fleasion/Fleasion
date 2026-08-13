import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string text: ''
    property string secondaryText: ''
    property string leadingIconText: ''
    property int horizontalAlignment: Text.AlignLeft
    property bool fillWidth: false
    property real preferredWidth: 120
    property real minimumWidth: 48

    Layout.fillWidth: fillWidth
    Layout.preferredWidth: preferredWidth
    Layout.minimumWidth: minimumWidth
    implicitHeight: secondaryText.length > 0 ? 52 : 40
    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceXs
    topPadding: Theme.spaceXxs
    bottomPadding: Theme.spaceXxs
    Accessible.role: Accessible.StaticText
    Accessible.name: secondaryText.length > 0 ? qsTr('%1, %2').arg(text).arg(secondaryText) : text
    background: null

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        Label {
            visible: root.leadingIconText.length > 0
            text: root.leadingIconText
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            Layout.preferredWidth: 20
            Accessible.ignored: true
        }

        ColumnLayout {
            spacing: 1
            Layout.fillWidth: true

            Label {
                text: root.text
                color: root.enabled ? Theme.textPrimary : Theme.textDisabled
                font.pointSize: TypeScale.body
                horizontalAlignment: root.horizontalAlignment
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                Layout.fillWidth: true
                Accessible.ignored: true
            }

            Label {
                visible: root.secondaryText.length > 0
                text: root.secondaryText
                color: root.enabled ? Theme.textSecondary : Theme.textDisabled
                font.pointSize: TypeScale.caption
                horizontalAlignment: root.horizontalAlignment
                elide: Text.ElideRight
                Layout.fillWidth: true
                Accessible.ignored: true
            }
        }
    }
}
