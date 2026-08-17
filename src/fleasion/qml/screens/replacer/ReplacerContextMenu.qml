import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Controls.Popup {
    id: root

    objectName: "replacerContextMenu"
    component MenuItem: FluentItemDelegate {
        Layout.fillWidth: true
        Layout.preferredHeight: 36
    }

    required property var controller
    required property var appController
    required property Item hostItem
    property string entryPath
    property string entryKind
    property string entryName
    property bool entryExpanded: false
    property bool canMoveUp: false
    property bool canMoveDown: false
    property var selectedPaths: []
    property var entryDetails: ({})
    property real requestedSceneX: 0
    property real requestedSceneY: 0
    property real hostAvailableWidth: hostItem.width
    property real hostAvailableHeight: hostItem.height
    readonly property int selectedCount: selectedPaths.length
    readonly property bool singleSelection: selectedCount === 1
    readonly property bool canGroupSelection: controller.canGroupEntries(selectedPaths)
    signal editRequested(string entryPath)
    signal renameGroupRequested(string entryPath, string entryName)
    signal groupRequested
    signal moveToRequested(var selectedPaths)
    signal deleteRequested

    function present(path, kind, name, expanded, moveUp, moveDown, sceneX, sceneY) {
        entryPath = path;
        entryKind = kind;
        entryName = name;
        entryExpanded = expanded;
        canMoveUp = moveUp;
        canMoveDown = moveDown;
        selectedPaths = controller.selection.values();
        entryDetails = controller.entry(path);
        updateHostBounds();
        const localPoint = parent.mapFromItem(null, sceneX, sceneY);
        requestedSceneX = localPoint.x;
        requestedSceneY = localPoint.y;
        open();
    }

    function updateHostBounds() {
        const window = hostItem.Window.window;
        if (!window) {
            hostAvailableWidth = hostItem.width;
            hostAvailableHeight = hostItem.height;
            return;
        }
        const origin = hostItem.mapToItem(null, 0, 0);
        hostAvailableWidth = Math.min(hostItem.width, window.width - origin.x);
        hostAvailableHeight = Math.min(hostItem.height, window.height - origin.y);
    }

    parent: hostItem
    z: 1000
    x: Math.max(Theme.spaceXs, Math.min(requestedSceneX, hostAvailableWidth - width - Theme.spaceXs))
    y: Math.max(Theme.spaceXs, Math.min(requestedSceneY, hostAvailableHeight - height - Theme.spaceXs))
    width: 276
    implicitHeight: menuColumn.implicitHeight + topPadding + bottomPadding
    height: Math.min(implicitHeight, hostAvailableHeight - Theme.spaceSm * 2)
    padding: Theme.spaceXxs
    focus: true
    popupType: Controls.Popup.Item
    closePolicy: Controls.Popup.CloseOnEscape | Controls.Popup.CloseOnPressOutside

    contentItem: Flickable {
        id: menuFlickable

        clip: true
        contentWidth: width
        contentHeight: menuColumn.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        Accessible.role: Accessible.PopupMenu
        Accessible.name: qsTr("Replacement actions")

        ColumnLayout {
            id: menuColumn

            width: menuFlickable.width
            spacing: 0

            Controls.Label {
                Layout.fillWidth: true
                Layout.leftMargin: Theme.spaceSm
                Layout.rightMargin: Theme.spaceSm
                Layout.topMargin: Theme.spaceXs
                Layout.bottomMargin: Theme.spaceXxs
                text: root.selectedCount > 1 ? qsTr("%n replacement(s) selected", "", root.selectedCount) : root.entryName
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                font.weight: TypeScale.semibold
                elide: Text.ElideRight
            }

            MenuItem {
                id: editAction

                objectName: "editEntryAction"
                visible: root.singleSelection
                text: root.entryKind === "group" ? qsTr("Rename group") : qsTr("Edit replacement")
                onClicked: {
                    root.close();
                    if (root.entryKind === "group")
                        root.renameGroupRequested(root.entryPath, root.entryName);
                    else
                        root.editRequested(root.entryPath);
                }
            }

            MenuItem {
                visible: root.singleSelection && root.entryKind === "group"
                text: root.entryExpanded ? qsTr("Collapse group") : qsTr("Expand group")
                onClicked: {
                    root.controller.setGroupExpanded(root.entryPath, !root.entryExpanded);
                    root.close();
                }
            }

            MenuItem {
                visible: root.singleSelection && root.entryKind === "rule" && String(root.entryDetails.targets || "").length > 0
                text: qsTr("Copy target IDs")
                onClicked: {
                    root.appController.copyText(String(root.entryDetails.targets || ""));
                    root.close();
                }
            }

            MenuItem {
                visible: root.singleSelection && root.entryKind === "rule" && String(root.entryDetails.replacement || "").length > 0
                text: qsTr("Copy replacement source")
                onClicked: {
                    root.appController.copyText(String(root.entryDetails.replacement || ""));
                    root.close();
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                Layout.topMargin: Theme.spaceXxs
                Layout.bottomMargin: Theme.spaceXxs
                color: Theme.border
            }

            MenuItem {
                id: enableAction

                objectName: "enableSelectionAction"
                text: root.selectedCount > 1 ? qsTr("Enable selected") : root.entryKind === "group" ? qsTr("Enable group") : qsTr("Enable replacement")
                onClicked: {
                    root.controller.setEntriesEnabled(root.selectedPaths, true);
                    root.close();
                }
            }

            MenuItem {
                objectName: "disableSelectionAction"
                text: root.selectedCount > 1 ? qsTr("Disable selected") : root.entryKind === "group" ? qsTr("Disable group") : qsTr("Disable replacement")
                onClicked: {
                    root.controller.setEntriesEnabled(root.selectedPaths, false);
                    root.close();
                }
            }

            MenuItem {
                objectName: "groupSelectionAction"
                visible: root.canGroupSelection
                text: qsTr("Create group from selection…")
                onClicked: {
                    root.close();
                    root.groupRequested();
                }
            }

            MenuItem {
                objectName: "moveSelectionAction"
                text: qsTr("Move to group…")
                onClicked: {
                    const paths = root.selectedPaths.slice();
                    root.close();
                    root.moveToRequested(paths);
                }
            }

            MenuItem {
                visible: root.singleSelection && root.controller.manualOrder
                enabled: root.canMoveUp
                text: qsTr("Move up")
                onClicked: {
                    root.controller.moveEntry(root.entryPath, -1);
                    root.close();
                }
            }

            MenuItem {
                visible: root.singleSelection && root.controller.manualOrder
                enabled: root.canMoveDown
                text: qsTr("Move down")
                onClicked: {
                    root.controller.moveEntry(root.entryPath, 1);
                    root.close();
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                Layout.topMargin: Theme.spaceXxs
                Layout.bottomMargin: Theme.spaceXxs
                color: Theme.border
            }

            MenuItem {
                objectName: "selectAllAction"
                text: qsTr("Select all visible")
                onClicked: {
                    root.controller.selectAllVisible();
                    root.close();
                }
            }

            MenuItem {
                visible: root.selectedCount > 1
                text: qsTr("Clear selection")
                onClicked: {
                    root.controller.selection.clear();
                    root.close();
                }
            }

            MenuItem {
                objectName: "deleteSelectionAction"
                text: root.selectedCount > 1 ? qsTr("Delete selected…") : root.entryKind === "group" ? qsTr("Delete group…") : qsTr("Delete replacement…")
                onClicked: {
                    root.close();
                    root.deleteRequested();
                }
            }
        }

        Controls.ScrollBar.vertical: FluentScrollBar {}
    }

    background: Rectangle {
        color: Theme.surfaceElevated
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.borderStrong
    }

    onOpened: {
        if (editAction.visible)
            editAction.forceActiveFocus();
        else
            enableAction.forceActiveFocus();
    }
}
