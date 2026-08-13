pragma ComponentBehavior: Bound

import QtQuick

Item {
    id: root

    property bool available: true
    property string title: qsTr('Feature unavailable')
    property string description: qsTr('This feature is not available in the current configuration.')
    property string actionText: ''
    default property alias contentData: contentHost.data

    signal actionTriggered

    implicitWidth: 480
    implicitHeight: 280
    Accessible.role: Accessible.Grouping
    Accessible.name: available ? '' : title
    Accessible.description: available ? '' : description

    Item {
        id: contentHost

        anchors.fill: parent
        visible: root.available
    }

    Loader {
        anchors.fill: parent
        active: !root.available
        asynchronous: false
        sourceComponent: gatePanel
    }

    Component {
        id: gatePanel

        EmptyState {
            iconText: '\u2298'
            title: root.title
            description: root.description
            actionText: root.actionText
            onActionTriggered: root.actionTriggered()
        }
    }
}
