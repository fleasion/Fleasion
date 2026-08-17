pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme
import "../../dialogs" as Dialogs

FluentDialog {
    id: root

    required property var controller
    required property var appController
    required property string entryKey
    property var details: ({})
    property bool replaying: false

    function loadEntry(preserveRequest) {
        const updated = controller.trafficEntry(entryKey);
        if (!updated || updated.requestId === undefined)
            return;
        details = updated;
        if (!preserveRequest)
            requestArea.text = String(updated.requestText || "");
        responseArea.text = String(updated.responseText || "");
    }

    function editedPendingText() {
        return details.pendingStage === "response" ? responseArea.text : requestArea.text;
    }

    function resolveHeld(action) {
        if (!details.pending)
            return;
        if (controller.resolve(details.requestId, details.pendingStage, action, editedPendingText()))
            close();
    }

    width: Math.min(980, parent ? parent.width - Theme.spaceXl : 980)
    height: Math.min(720, parent ? parent.height - Theme.spaceXl : 720)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: qsTr("Traffic inspector")
    standardButtons: Dialog.NoButton
    onOpened: loadEntry(false)

    header: TrafficInspectorHeader {
        details: root.details
        onCloseRequested: root.close()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        TrafficMetadataBar {
            Layout.fillWidth: true
            details: root.details
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            PreviewEditor {
                id: requestArea

                SplitView.fillWidth: true
                SplitView.minimumWidth: 260
                heading: root.details.archived ? qsTr("Request · preserved read-only copy") : root.details.pendingStage === "request" ? qsTr("Request · held and editable") : qsTr("Request · editable for replay")
                accessibleName: qsTr("HTTP request content")
                readOnly: Boolean(root.details.archived)
            }

            PreviewEditor {
                id: responseArea

                SplitView.fillWidth: true
                SplitView.minimumWidth: 260
                heading: root.details.pendingStage === "response" ? qsTr("Response · held and editable") : qsTr("Response")
                accessibleName: qsTr("HTTP response content")
                readOnly: root.details.pendingStage !== "response"
            }
        }

        TrafficInspectorActions {
            Layout.fillWidth: true
            details: root.details
            requestText: requestArea.text
            responseText: responseArea.text
            replaying: root.replaying
            onCopyUrlRequested: root.appController.copyText("https://" + String(root.details.host || "") + String(root.details.path || ""))
            onCopyRequestRequested: root.appController.copyText(requestArea.text)
            onCopyResponseRequested: root.appController.copyText(responseArea.text)
            onReplayRequested: {
                if (root.controller.replay(root.details.requestId, requestArea.text))
                    root.replaying = true;
            }
            onForwardRequested: root.resolveHeld("forward")
            onDropRequested: dropLoader.active = true
        }
    }

    Connections {
        target: root.controller
        enabled: root.replaying

        function onModelChanged() {
            root.loadEntry(true);
            if (root.details.durationText)
                root.replaying = false;
        }
    }

    Loader {
        id: dropLoader

        active: false
        sourceComponent: Component {
            Dialogs.ConfirmDialog {
                heading: qsTr("Drop held traffic?")
                message: root.details.pendingStage === "response" ? qsTr("The held response will not be delivered to Roblox.") : qsTr("The held request will not be sent upstream.")
                details: qsTr("This can interrupt the active network operation and cannot be undone.")
                acceptText: qsTr("Drop")
                destructive: true
                onConfirmed: root.resolveHeld("drop")
                onClosed: dropLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.ConfirmDialog).open();
        }
    }
}
