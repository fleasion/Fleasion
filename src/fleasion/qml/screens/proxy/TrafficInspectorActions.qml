import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

RowLayout {
    id: root

    property var details: ({})
    property string requestText
    property string responseText
    property bool replaying
    signal copyUrlRequested
    signal copyRequestRequested
    signal copyResponseRequested
    signal replayRequested
    signal forwardRequested
    signal dropRequested

    spacing: Theme.spaceSm

    FluentButton {
        text: qsTr("Copy URL")
        enabled: Boolean(root.details.host)
        onClicked: root.copyUrlRequested()
    }

    FluentButton {
        text: qsTr("Copy request")
        enabled: root.requestText.length > 0
        onClicked: root.copyRequestRequested()
    }

    FluentButton {
        text: qsTr("Copy response")
        enabled: root.responseText.length > 0
        onClicked: root.copyResponseRequested()
    }

    Item {
        Layout.fillWidth: true
    }

    BusyIndicator {
        visible: root.replaying
        running: visible
        Layout.preferredWidth: 24
        Layout.preferredHeight: 24
        Accessible.name: qsTr("Replaying request")
    }

    FluentButton {
        text: qsTr("Replay request")
        enabled: !root.details.archived && !root.replaying && root.requestText.length > 0
        onClicked: root.replayRequested()
    }

    FluentButton {
        text: qsTr("Forward")
        visible: Boolean(root.details.pending)
        highlighted: true
        onClicked: root.forwardRequested()
    }

    FluentButton {
        text: qsTr("Drop")
        visible: Boolean(root.details.pending)
        onClicked: root.dropRequested()
    }
}
