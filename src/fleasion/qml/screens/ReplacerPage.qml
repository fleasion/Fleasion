pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "replacer" as Replacer

FocusScope {
    id: root

    required property var controller
    required property var appController
    property int selectedCount: 0
    property string pendingNameAction: "create"
    property string pendingRulePath
    property string pendingDeleteKind: "entries"
    property bool pendingExport: false

    function focusSearch() {
        searchBox.forceActiveFocus();
    }

    function syncSelection() {
        selectedCount = controller.selection.values().length;
    }

    function openNameDialog(action) {
        pendingNameAction = action;
        nameDialogLoader.active = true;
    }

    function openRuleEditor(path) {
        pendingRulePath = path;
        ruleEditorLoader.active = true;
    }

    function openDeleteDialog(kind) {
        pendingDeleteKind = kind;
        deleteDialogLoader.active = true;
    }

    Component.onCompleted: {
        root.syncSelection();
        if (controller.hasDraft)
            root.openRuleEditor("");
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
            title: qsTr("Asset replacer")
            subtitle: qsTr("Build reusable profiles that transform Roblox assets as they are downloaded.")
            iconText: "⇄"

            FluentButton {
                text: qsTr("New group")
                onClicked: root.openNameDialog("group")
            }

            FluentButton {
                text: qsTr("Add replacement")
                highlighted: true
                onClicked: root.openRuleEditor("")
            }
        }

        Replacer.ReplacerToolbar {
            Layout.fillWidth: true
            controller: root.controller
            onCreateProfileRequested: root.openNameDialog("create")
            onRenameProfileRequested: root.openNameDialog("rename")
            onDuplicateProfileRequested: root.openNameDialog("duplicate")
            onDeleteProfileRequested: root.openDeleteDialog("profile")
            onImportProfileRequested: {
                root.pendingExport = false;
                transferDialogLoader.active = true;
            }
            onExportProfileRequested: {
                root.pendingExport = true;
                transferDialogLoader.active = true;
            }
            onCommunityPresetsRequested: communityPresetDialogLoader.active = true
        }

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            flat: true
            padding: 0
            contentSpacing: Theme.spaceXs

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                SearchBox {
                    id: searchBox

                    Layout.fillWidth: true
                    placeholderText: qsTr("Search names, targets, or replacement sources")
                    text: root.controller.query
                    Accessible.name: qsTr("Search replacement rules")
                    onTextEdited: root.controller.query = text
                }

                Label {
                    text: qsTr("%n item(s)", "", root.controller.model.count)
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: selectionRow.implicitHeight + Theme.spaceSm
                visible: root.selectedCount > 0
                radius: Theme.radiusMd
                color: Theme.accentSubtle
                border.width: 1
                border.color: Theme.accent

                RowLayout {
                    id: selectionRow

                    anchors.fill: parent
                    anchors.leftMargin: Theme.spaceSm
                    anchors.rightMargin: Theme.spaceXs
                    spacing: Theme.spaceSm

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("%n selected", "", root.selectedCount)
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.body
                        font.weight: TypeScale.medium
                    }

                    FluentButton {
                        text: qsTr("Clear selection")
                        flat: true
                        onClicked: root.controller.selection.clear()
                    }

                    FluentButton {
                        text: qsTr("Delete")
                        onClicked: root.openDeleteDialog("entries")
                    }
                }
            }

            DataTableHeader {
                Layout.fillWidth: true

                DataTableHeaderCell {
                    preferredWidth: 80
                    text: qsTr("State")
                }

                DataTableHeaderCell {
                    fillWidth: true
                    text: qsTr("Replacement")
                }

                DataTableHeaderCell {
                    preferredWidth: 110
                    text: qsTr("Action")
                }

                DataTableHeaderCell {
                    preferredWidth: 230
                    visible: root.width >= 920
                    text: qsTr("Source")
                }

                DataTableHeaderCell {
                    preferredWidth: Theme.controlHeight
                    text: ""
                    Accessible.name: qsTr("Actions")
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: root.controller.model.count > 0 ? 0 : 1

                ListView {
                    id: rulesView

                    objectName: "replacerRulesViewport"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: root.controller.model
                    spacing: 2
                    boundsBehavior: Flickable.StopAtBounds
                    clip: true
                    reuseItems: true
                    Accessible.name: qsTr("Replacement rules")

                    delegate: Replacer.ReplacerRuleDelegate {
                        required property var model

                        width: ListView.view.width
                        selectionModel: root.controller.selection
                        entryPath: model.path
                        entryKind: model.kind
                        entryDepth: model.depth
                        entryName: model.name
                        entryEnabled: model.enabled
                        entryState: model.state
                        actionText: model.action
                        replacementText: model.replacement
                        targetsText: model.targets
                        targetCount: model.targetCount
                        onEnabledToggled: (path, enabled) => {
                            root.controller.setEntryEnabled(path, enabled);
                        }
                        onEditRequested: path => root.openRuleEditor(path)
                    }

                    ScrollBar.vertical: ScrollBar {}
                }

                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    iconText: root.controller.query.length > 0 ? "⌕" : "⇄"
                    title: root.controller.query.length > 0 ? qsTr("No matching replacements") : qsTr("This profile is ready")
                    description: root.controller.query.length > 0 ? qsTr("Try a broader search or clear the filter.") : qsTr("Add a replacement rule to intercept an asset, or create a group to organize related rules.")
                    actionText: root.controller.query.length > 0 ? qsTr("Clear search") : qsTr("Add replacement")
                    onActionTriggered: {
                        if (root.controller.query.length > 0) {
                            searchBox.clear();
                            root.controller.query = "";
                        } else {
                            root.openRuleEditor("");
                        }
                    }
                }
            }
        }
    }

    Loader {
        id: nameDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.ProfileNameDialog {
                action: root.pendingNameAction
                currentName: root.controller.activeConfig
                onSubmitted: (action, name) => {
                    switch (action) {
                    case "rename":
                        root.controller.renameConfig(root.controller.activeConfig, name);
                        break;
                    case "duplicate":
                        root.controller.duplicateConfig(root.controller.activeConfig, name);
                        break;
                    case "group":
                        root.controller.addGroup(name);
                        break;
                    default:
                        root.controller.createConfig(name);
                    }
                }
                onClosed: nameDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ProfileNameDialog).open();
        }
    }

    Connections {
        target: root.controller

        function onDraftChanged() {
            if (root.controller.hasDraft && !communityPresetDialogLoader.active)
                root.openRuleEditor("");
        }
    }

    Loader {
        id: communityPresetDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.CommunityPresetsDialog {
                controller: root.controller.communityPresets
                onClosed: {
                    communityPresetDialogLoader.active = false;
                    if (root.controller.hasDraft)
                        Qt.callLater(() => root.openRuleEditor(""));
                }
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.CommunityPresetsDialog).open();
        }
    }

    Loader {
        id: ruleEditorLoader

        active: false
        sourceComponent: Component {
            Replacer.RuleEditorDialog {
                controller: root.controller
                entryPath: root.pendingRulePath
                onClosed: ruleEditorLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.RuleEditorDialog).open();
        }
    }

    Loader {
        id: transferDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.ProfileTransferDialog {
                controller: root.controller
                exporting: root.pendingExport
                onFinished: transferDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ProfileTransferDialog).open();
        }
    }

    Loader {
        id: deleteDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.ReplacerDeleteDialog {
                targetKind: root.pendingDeleteKind
                profileName: root.controller.activeConfig
                entryCount: root.selectedCount
                onConfirmed: kind => {
                    if (kind === "profile")
                        root.controller.deleteConfig(root.controller.activeConfig);
                    else
                        root.controller.deleteEntries(root.controller.selection.values());
                }
                onClosed: deleteDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ReplacerDeleteDialog).open();
        }
    }

    Connections {
        target: root.controller.selection

        function onSelectionChanged() {
            root.syncSelection();
        }
    }
}
