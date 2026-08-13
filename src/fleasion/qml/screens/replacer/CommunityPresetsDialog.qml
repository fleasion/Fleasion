pragma ComponentBehavior: Bound
import "../../dialogs" as Dialogs
import Fleasion.Components
import Fleasion.Theme

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller
    property string pendingDeleteId
    property string pendingDeleteName

    signal draftPrepared

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(940, parent ? parent.width - Theme.spaceXxl : 940)
    height: Math.min(720, parent ? parent.height - Theme.spaceXxl : 720)
    modal: true
    focus: true
    title: qsTr('Community presets')
    standardButtons: Dialog.Close
    closePolicy: Popup.CloseOnEscape
    onOpened: controller.ensureLoaded()
    onClosed: controller.closePayload()

    Loader {
        id: customDialogLoader

        active: false
        onLoaded: {
            if (status === Loader.Ready)
                (item as CustomCommunityPresetDialog).open();
        }

        sourceComponent: Component {
            CustomCommunityPresetDialog {
                controller: root.controller
                onClosed: customDialogLoader.active = false
            }
        }
    }

    Loader {
        id: deleteDialogLoader

        active: false
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.ConfirmDialog).open();
        }

        sourceComponent: Component {
            Dialogs.ConfirmDialog {
                parent: Overlay.overlay
                anchors.centerIn: parent
                heading: qsTr('Delete custom preset?')
                message: qsTr('The custom catalog entry will be removed from Fleasion.')
                details: root.pendingDeleteName
                acceptText: qsTr('Delete preset')
                destructive: true
                onConfirmed: root.controller.removeCustom(root.pendingDeleteId)
                onClosed: deleteDialogLoader.active = false
            }
        }
    }

    contentItem: StackLayout {
        currentIndex: root.controller.payloadOpen ? 1 : 0

        CommunityPresetCatalogView {
            controller: root.controller
            onSourceRequested: (presetId, kind) => {
                return root.controller.openPreset(presetId, kind);
            }
            onCustomImportRequested: customDialogLoader.active = true
            onDeleteRequested: (presetId, name) => {
                root.pendingDeleteId = presetId;
                root.pendingDeleteName = name;
                deleteDialogLoader.active = true;
            }
        }

        CommunityPresetValuesView {
            controller: root.controller
            onBackRequested: root.controller.closePayload()
            onDraftPrepared: {
                root.draftPrepared();
                root.close();
            }
        }
    }
}
