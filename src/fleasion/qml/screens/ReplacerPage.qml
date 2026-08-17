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
    property string pendingGroupPath
    property string pendingCurrentName
    property string pendingDeleteKind: "entries"
    property bool pendingExport: false

    function focusSearch() {
        searchBox.forceActiveFocus();
    }

    function syncSelection() {
        selectedCount = controller.selection.values().length;
    }

    function openNameDialog(action, path, currentName) {
        pendingNameAction = action;
        pendingGroupPath = path || "";
        pendingCurrentName = currentName || "";
        nameDialogLoader.active = true;
    }

    function openRuleEditor(path) {
        if (ruleEditorLoader.active)
            return;
        pendingRulePath = path;
        ruleEditorLoader.active = true;
    }

    function openDeleteDialog(kind) {
        pendingDeleteKind = kind;
        deleteDialogLoader.active = true;
    }

    Component.onCompleted: {
        root.syncSelection();
        communityDraftFlow.presentDraft();
    }

    Replacer.CommunityDraftFlow {
        id: communityDraftFlow

        hasDraft: root.controller.hasDraft
        closeViewerOnReplace: root.appController && root.appController.settings ? root.appController.settings.closeViewerOnReplace : true
        communityViewerOpen: communityPresetDialogLoader.active
        ruleEditorOpen: ruleEditorLoader.active
        onCloseCommunityViewerRequested: {
            if (communityPresetDialogLoader.status === Loader.Ready)
                (communityPresetDialogLoader.item as Replacer.CommunityPresetsDialog).close();
        }
        onOpenRuleEditorRequested: root.openRuleEditor("")
        onRestoreCommunityViewerRequested: {
            if (communityPresetDialogLoader.status === Loader.Ready)
                (communityPresetDialogLoader.item as Replacer.CommunityPresetsDialog).restoreViewerFocus();
        }
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

                IconButton {
                    controlSize: 32
                    flat: true
                    iconText: "⌄"
                    text: qsTr("Expand all groups")
                    onClicked: root.controller.setAllGroupsExpanded(true)
                }

                IconButton {
                    controlSize: 32
                    flat: true
                    iconText: "›"
                    text: qsTr("Collapse all groups")
                    onClicked: root.controller.setAllGroupsExpanded(false)
                }
            }

            Replacer.ReplacerSelectionBar {
                Layout.fillWidth: true
                visible: selectedCount > 0
                controller: root.controller
                onGroupRequested: root.openNameDialog("groupSelection", "", "")
                onDeleteRequested: root.openDeleteDialog("entries")
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
                    preferredWidth: 92
                    text: qsTr("Organize")
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
                    spacing: 0
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
                        entryExpanded: model.expanded
                        childCount: model.childCount
                        canMoveUp: model.canMoveUp
                        canMoveDown: model.canMoveDown
                        actionText: model.action
                        replacementText: model.replacement
                        targetsText: model.targets
                        targetCount: model.targetCount
                        onEnabledToggled: (path, enabled) => {
                            root.controller.setEntryEnabled(path, enabled);
                        }
                        onEditRequested: path => root.openRuleEditor(path)
                        onGroupRenameRequested: (path, name) => root.openNameDialog("renameGroup", path, name)
                        onExpansionToggled: (path, expanded) => root.controller.setGroupExpanded(path, expanded)
                        onMoveRequested: (path, direction) => root.controller.moveEntry(path, direction)
                    }

                    ScrollBar.vertical: FluentScrollBar {}
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
                currentName: root.pendingCurrentName.length > 0 ? root.pendingCurrentName : root.controller.activeConfig
                groupPath: root.pendingGroupPath
                controller: root.controller
                onClosed: nameDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ProfileNameDialog).open();
        }
    }

    Loader {
        id: communityPresetDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.CommunityPresetsDialog {
                controller: root.controller.communityPresets
                appController: root.appController
                onDraftPrepared: communityDraftFlow.communityDraftPrepared()
                onClosed: {
                    communityPresetDialogLoader.active = false;
                    communityDraftFlow.communityViewerClosed();
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
                onClosed: {
                    ruleEditorLoader.active = false;
                    communityDraftFlow.ruleEditorClosed();
                }
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
