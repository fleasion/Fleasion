pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "modifications" as Modifications

FocusScope {
    id: root

    required property var controller
    required property var appController
    property string pendingEntryId
    property string pendingOperation: "restore"
    property bool pendingFastFlagsEnabled: false
    property string pendingTargetPath
    property string pendingRecoveryKind: "backup"
    property string inspectName
    property string inspectTargetPath

    function openEditor(entryId) {
        pendingEntryId = entryId;
        editorLoader.active = true;
    }

    function confirm(operation, enabling) {
        pendingOperation = operation;
        pendingFastFlagsEnabled = enabling || false;
        confirmLoader.active = true;
    }

    function inspect(entryName, targetPath) {
        inspectName = entryName;
        inspectTargetPath = targetPath;
        inspectLoader.active = true;
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.surface
        Accessible.ignored: true
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pageGutter
        anchors.rightMargin: Theme.pageGutter
        anchors.topMargin: Theme.pageTopGutter
        anchors.bottomMargin: Theme.pageBottomGutter
        spacing: Theme.sectionGap

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Modifications")
            subtitle: qsTr("Manage restorable Roblox file overrides and custom ClientSettings flags.")
            iconText: "✦"

            FluentButton {
                text: qsTr("Restore originals")
                enabled: root.controller.model.count > 0
                onClicked: root.confirm("restore", false)
            }

            FluentButton {
                text: qsTr("Reapply all")
                highlighted: true
                enabled: root.controller.model.count > 0
                onClicked: root.controller.reapplyAll()
            }
        }

        Modifications.ModificationStatusStrip {
            Layout.fillWidth: true
            controller: root.controller
        }

        TabBar {
            id: sectionTabs
            Layout.fillWidth: true
            Accessible.name: qsTr("Modification sections")

            FluentTabButton {
                text: qsTr("File changes")
            }
            FluentTabButton {
                text: qsTr("FastFlags")
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: sectionTabs.currentIndex

            FluentScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.sectionGap

                    Modifications.BuiltInModificationsPanel {
                        Layout.fillWidth: true
                        controller: root.controller
                        onInspectRequested: (name, targetPath) => root.inspect(name, targetPath)
                    }

                    Modifications.OrphanedStashPanel {
                        Layout.fillWidth: true
                        controller: root.controller
                        onInspectRequested: (name, targetPath) => root.inspect(name, targetPath)
                        onRestoreRequested: (targetPath, recoveryKind) => {
                            root.pendingTargetPath = targetPath;
                            root.pendingRecoveryKind = recoveryKind;
                            root.confirm("orphan", false);
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Theme.borderStrong
                        Accessible.ignored: true
                    }

                    Modifications.CustomModificationsPanel {
                        Layout.fillWidth: true
                        controller: root.controller
                        onEditRequested: entryId => root.openEditor(entryId)
                        onInspectRequested: (name, targetPath) => root.inspect(name, targetPath)
                        onResetRequested: entryId => {
                            root.pendingEntryId = entryId;
                            root.confirm("reset", false);
                        }
                    }
                }
            }

            FluentScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth

                Modifications.FastFlagsPanel {
                    width: parent.width
                    controller: root.controller
                }
            }
        }
    }

    Loader {
        id: editorLoader
        active: false
        sourceComponent: Component {
            Modifications.ModificationEditorDialog {
                controller: root.controller
                entryId: root.pendingEntryId
                onClosed: editorLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Modifications.ModificationEditorDialog).open();
        }
    }

    Loader {
        id: confirmLoader
        active: false
        sourceComponent: Component {
            Modifications.ModificationConfirmDialog {
                operation: root.pendingOperation
                enabling: root.pendingFastFlagsEnabled
                recoveryKind: root.pendingRecoveryKind
                onConfirmed: (operation, enabling) => {
                    if (operation === "fastFlags")
                        root.controller.fastFlagsEnabled = enabling;
                    else if (operation === "orphan")
                        root.controller.restoreOrphanedStash(root.pendingTargetPath);
                    else if (operation === "reset")
                        root.controller.resetEntry(root.pendingEntryId);
                    else
                        root.controller.restoreAll();
                }
                onClosed: confirmLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Modifications.ModificationConfirmDialog).open();
        }
    }

    Loader {
        id: inspectLoader

        active: false
        asynchronous: true
        sourceComponent: Component {
            Modifications.ModificationInspectDialog {
                controller: root.controller
                entryName: root.inspectName
                targetPath: root.inspectTargetPath
                onClosed: inspectLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Modifications.ModificationInspectDialog).open();
        }
    }
}
