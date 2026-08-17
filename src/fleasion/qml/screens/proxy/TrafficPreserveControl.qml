import QtQuick
import QtQuick.Controls
import Fleasion.Components

FluentSwitch {
    id: root

    property var controller

    objectName: "trafficPreserveControl"
    text: qsTr("Preserve")
    enabled: Boolean(root.controller)
    checked: Boolean(root.controller && root.controller.trafficPreserve)
    Accessible.name: qsTr("Preserve traffic across restarts")
    ToolTip.text: qsTr("Keep a bounded, credential-redacted traffic history across app and proxy restarts.")
    ToolTip.visible: hovered
    ToolTip.delay: 500
    onToggled: {
        if (root.controller && root.checked !== root.controller.trafficPreserve)
            root.controller.setTrafficPreserve(root.checked);
    }
}
