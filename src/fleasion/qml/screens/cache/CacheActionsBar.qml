import QtQuick
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Item {
    id: root

    required property var controller
    signal blacklistRequested
    signal clearRequested
    signal loadRequested

    implicitHeight: actions.implicitHeight

    RowLayout {
        id: actions

        anchors.fill: parent
        spacing: Theme.spaceXs

        StatusPill {
            visible: root.controller.blacklistCount > 0
            text: qsTr('%n hidden ID(s)', '', root.controller.blacklistCount)
            status: 'warning'
        }

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            text: qsTr('Load assets')
            compact: true
            enabled: !root.controller.task.busy
            onClicked: root.loadRequested()
        }

        FluentButton {
            text: qsTr('Hidden IDs')
            compact: true
            flat: true
            onClicked: root.blacklistRequested()
        }

        FluentButton {
            text: qsTr('Cache folder')
            compact: true
            flat: true
            onClicked: root.controller.openCacheFolder()
        }

        FluentButton {
            text: qsTr('Exports')
            compact: true
            flat: true
            onClicked: root.controller.openExportsFolder()
        }

        FluentButton {
            text: qsTr('Clear all')
            compact: true
            danger: true
            enabled: !root.controller.task.busy && root.controller.totalAssets > 0
            onClicked: root.clearRequested()
        }
    }
}
