import Fleasion.Components
import QtQuick
import QtQuick.Layouts

SettingRow {
    id: root

    required property var controller

    Layout.fillWidth: true
    title: qsTr("Roblox framerate cap")
    description: qsTr("Global player setting; 0 restores Roblox's default cap")
    iconText: "↗"

    FluentSpinBox {
        id: framerateCap

        Layout.preferredWidth: 150
        from: 0
        to: 1000
        value: root.controller.framerateCap
        editable: true
        Accessible.name: qsTr("Roblox framerate cap")
    }

    FluentButton {
        text: qsTr("Apply cap")
        onClicked: root.controller.setFramerateCap(framerateCap.value)
    }
}
