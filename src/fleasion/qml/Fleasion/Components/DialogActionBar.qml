import Fleasion.Theme
import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root

    property string acceptText: qsTr('Save')
    property string cancelText: qsTr('Cancel')
    property bool acceptEnabled: true
    property bool acceptHighlighted: true
    property bool acceptDanger: false

    signal acceptRequested
    signal cancelRequested

    implicitHeight: actions.implicitHeight + Theme.spaceSm * 2
    color: Theme.surfaceElevated

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: Theme.border
    }

    RowLayout {
        id: actions

        anchors.fill: parent
        anchors.leftMargin: Theme.panelPadding
        anchors.rightMargin: Theme.panelPadding
        anchors.topMargin: Theme.spaceSm
        anchors.bottomMargin: Theme.spaceSm
        spacing: Theme.spaceXs

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            objectName: 'dialogCancelButton'
            text: root.cancelText
            compact: true
            onClicked: root.cancelRequested()
        }

        FluentButton {
            objectName: 'dialogAcceptButton'
            text: root.acceptText
            compact: true
            highlighted: root.acceptHighlighted
            danger: root.acceptDanger
            enabled: root.acceptEnabled
            onClicked: root.acceptRequested()
        }
    }
}
