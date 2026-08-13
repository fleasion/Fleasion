pragma ComponentBehavior: Bound

import QtQuick

Item {
    id: root

    required property var controller
    required property var appController
    property string pendingActionId: ""
    property string pendingActionLabel: ""
    property string pendingConfirmationTitle: ""
    property string pendingConfirmationText: ""

    function requestAction(actionId, label, requiresConfirmation, confirmationTitle, confirmationText) {
        if (!requiresConfirmation) {
            root.controller.performAction(actionId);
            return;
        }
        root.pendingActionId = actionId;
        root.pendingActionLabel = label;
        root.pendingConfirmationTitle = confirmationTitle;
        root.pendingConfirmationText = confirmationText;
        confirmationLoader.active = true;
    }

    function openRepairDialog() {
        if (repairLoader.item)
            (repairLoader.item as RepairDialogFrame).open();
    }

    Loader {
        id: repairLoader

        objectName: "startupRepairLoader"
        anchors.fill: parent
        asynchronous: true
        active: root.controller.active
        sourceComponent: {
            switch (root.controller.dialogKind) {
            case "port":
                return portDialog;
            case "hosts":
                return hostsDialog;
            case "helper":
                return helperDialog;
            case "certificate":
                return certificateDialog;
            case "tls":
                return tlsDialog;
            case "firewall":
                return firewallDialog;
            default:
                return null;
            }
        }
        onLoaded: root.openRepairDialog()
    }

    Loader {
        id: confirmationLoader

        objectName: "startupRepairConfirmationLoader"
        anchors.fill: parent
        asynchronous: true
        active: false
        sourceComponent: Component {
            RepairConfirmationDialog {
                title: root.pendingConfirmationTitle || qsTr("Confirm startup repair")
                message: root.pendingConfirmationText
                acceptText: root.pendingActionLabel || qsTr("Continue")
                onConfirmed: root.controller.performAction(root.pendingActionId)
                onClosed: {
                    root.pendingActionId = "";
                    confirmationLoader.active = false;
                }
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as RepairConfirmationDialog).open();
        }
    }

    Connections {
        target: root.controller

        function onRequestChanged() {
            if (root.controller.active)
                Qt.callLater(root.openRepairDialog);
        }
    }

    Component {
        id: portDialog

        PortBindRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }

    Component {
        id: hostsDialog

        HostsRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }

    Component {
        id: helperDialog

        HelperRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }

    Component {
        id: certificateDialog

        CertificateRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }

    Component {
        id: tlsDialog

        TlsRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }

    Component {
        id: firewallDialog

        FirewallRepairDialog {
            controller: root.controller
            appController: root.appController
            onActionRequested: (actionId, label, requiresConfirmation, title, message) => root.requestAction(actionId, label, requiresConfirmation, title, message)
        }
    }
}
