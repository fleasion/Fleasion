pragma ComponentBehavior: Bound

import QtQuick

Item {
    id: root

    required property var controller

    Component.onCompleted: controller.checkAutomatic()

    Connections {
        target: root.controller

        function onUpdateAvailable() {
            updateLoader.active = true;
        }
    }

    Loader {
        id: updateLoader

        active: false
        sourceComponent: Component {
            UpdateDialog {
                controller: root.controller
                onClosed: updateLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as UpdateDialog).open();
        }
    }
}
