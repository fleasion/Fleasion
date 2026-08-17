import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

Control {
    id: root

    property alias text: editor.text
    property alias readOnly: editor.readOnly
    property string heading
    property string accessibleName

    padding: Theme.spaceSm
    Accessible.role: Accessible.Grouping
    Accessible.name: heading

    contentItem: ColumnLayout {
        spacing: Theme.spaceXs

        Label {
            Layout.fillWidth: true
            text: root.heading
            color: root.readOnly ? Theme.textSecondary : Theme.accent
            font.pointSize: TypeScale.label
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
        }

        FluentScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            FluentTextArea {
                id: editor

                selectByMouse: true
                wrapMode: TextEdit.NoWrap
                color: Theme.textPrimary
                selectionColor: Theme.accent
                selectedTextColor: Theme.accentForeground
                font.family: "monospace"
                font.pointSize: TypeScale.caption
                Accessible.name: root.accessibleName
            }
        }
    }

    background: Rectangle {
        color: Theme.surfaceSubtle
        radius: Theme.radiusMd
        border.width: 1
        border.color: root.readOnly ? Theme.border : Theme.accent
    }
}
