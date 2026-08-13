import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

ToolBar {
    id: root

    property var details: ({})
    signal closeRequested

    contentItem: RowLayout {
        spacing: Theme.spaceSm

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Label {
                Layout.fillWidth: true
                text: qsTr("%1  %2").arg(String(root.details.method || "")).arg(String(root.details.host || ""))
                color: Theme.textPrimary
                font.pointSize: TypeScale.subtitle
                font.weight: TypeScale.semibold
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: String(root.details.path || "")
                color: Theme.textSecondary
                font.family: "monospace"
                font.pointSize: TypeScale.caption
                elide: Text.ElideMiddle
            }
        }

        StatusPill {
            text: String(root.details.status || qsTr("Pending"))
            status: root.details.pending ? "warning" : root.details.droppedRequest || root.details.droppedResponse ? "danger" : "neutral"
        }

        IconButton {
            iconText: "×"
            text: qsTr("Close traffic inspector")
            onClicked: root.closeRequested()
        }
    }
}
