import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

RowLayout {
    id: root

    property var details: ({})
    spacing: Theme.spaceSm

    Label {
        text: root.details.archived ? qsTr("Preserved request") : qsTr("Request #%1").arg(String(root.details.requestId ?? ""))
        color: Theme.textSecondary
        font.pointSize: TypeScale.label
    }

    Label {
        text: String(root.details.timeText || "")
        color: Theme.textTertiary
        font.pointSize: TypeScale.caption
    }

    Label {
        text: String(root.details.sizeText || "")
        color: Theme.textTertiary
        font.pointSize: TypeScale.caption
    }

    Label {
        text: String(root.details.durationText || "")
        color: Theme.textTertiary
        font.pointSize: TypeScale.caption
    }

    Item {
        Layout.fillWidth: true
    }

    StatusPill {
        visible: Boolean(root.details.archived)
        text: qsTr("Read-only history")
        status: "neutral"
    }

    StatusPill {
        visible: Boolean(root.details.wasIntercepted)
        text: qsTr("Interactively intercepted")
        status: "info"
    }
}
