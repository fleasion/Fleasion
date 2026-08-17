pragma ComponentBehavior: Bound

import QtQuick
import QtQml.Models
import Qt.labs.platform as Platform

Platform.Menu {
    id: root

    required property var controller

    title: qsTr("Replacement profiles")
    enabled: Boolean(controller) && controller.configs.length > 0

    Instantiator {
        model: root.controller ? root.controller.configs : []

        delegate: Platform.MenuItem {
            required property string modelData

            text: modelData
            checkable: true
            checked: root.controller.enabledConfigs.indexOf(modelData) !== -1
            onTriggered: root.controller.setConfigEnabled(modelData, checked)
        }

        onObjectAdded: (index, object) => root.insertItem(index, object)
        onObjectRemoved: (index, object) => root.removeItem(object)
    }
}
