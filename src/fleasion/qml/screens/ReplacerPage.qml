pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "replacer" as Replacer

FocusScope {
    id: root

    objectName: "replacerPage"
    required property var controller
    required property var appController
    property int selectedCount: 0
    property string pendingNameAction: "create"
    property string pendingRulePath
    property string pendingGroupPath
    property string pendingCurrentName
    property string pendingDeleteKind: "entries"
    property bool pendingExport: false
    property string pendingContextPath
    property string pendingContextKind
    property string pendingContextName
    property bool pendingContextExpanded: false
    property bool pendingContextCanMoveUp: false
    property bool pendingContextCanMoveDown: false
    property real pendingContextSceneX: 0
    property real pendingContextSceneY: 0
    property var pendingMovePaths: []
    property bool dragActive: false
    property var dragPaths: []
    property string dragTargetPath
    property string dragDropPosition
    property real dragIndicatorY: -1
    readonly property real stateColumnWidth: 80
    readonly property real actionColumnWidth: 110
    readonly property real sourceColumnWidth: 230
    readonly property real organizeColumnWidth: 88
    readonly property bool showSourceColumn: width >= 920

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

    function sortDirection(key) {
        if (controller.sortKey !== key)
            return "none";
        return controller.sortDescending ? "descending" : "ascending";
    }

    function openContextMenu(path, kind, name, expanded, canMoveUp, canMoveDown, sceneX, sceneY) {
        controller.selectForContext(path);
        pendingContextPath = path;
        pendingContextKind = kind;
        pendingContextName = name;
        pendingContextExpanded = expanded;
        pendingContextCanMoveUp = canMoveUp;
        pendingContextCanMoveDown = canMoveDown;
        pendingContextSceneX = sceneX;
        pendingContextSceneY = sceneY;
        contextMenuLoader.active = true;
    }

    function updateDragTarget(sceneX, sceneY) {
        const point = rulesView.mapFromItem(null, sceneX, sceneY);
        const rowIndex = rulesView.indexAt(point.x + rulesView.contentX, point.y + rulesView.contentY);
        if (rowIndex < 0) {
            dragTargetPath = "";
            dragDropPosition = "root";
            dragIndicatorY = Math.min(rulesView.height - 2, rulesView.contentHeight - rulesView.contentY);
            return;
        }
        const row = controller.model.get(rowIndex);
        const delegateItem = rulesView.itemAtIndex(rowIndex);
        if (!delegateItem) {
            dragTargetPath = "";
            dragDropPosition = "";
            dragIndicatorY = -1;
            return;
        }
        const delegatePoint = delegateItem.mapFromItem(null, sceneX, sceneY);
        dragTargetPath = String(row.path || "");
        if (row.kind === "group" && delegatePoint.y >= delegateItem.height * 0.25 && delegatePoint.y <= delegateItem.height * 0.75) {
            dragDropPosition = "into";
            dragIndicatorY = -1;
            return;
        }
        dragDropPosition = delegatePoint.y < delegateItem.height / 2 ? "before" : "after";
        const edge = delegateItem.mapToItem(rulesView, 0, dragDropPosition === "before" ? 0 : delegateItem.height);
        dragIndicatorY = edge.y;
    }

    function beginDrag(path, sceneX, sceneY) {
        controller.selectForContext(path);
        dragPaths = controller.selection.values();
        dragActive = true;
        updateDragTarget(sceneX, sceneY);
    }

    function finishDrag(sceneX, sceneY) {
        if (!dragActive)
            return;
        updateDragTarget(sceneX, sceneY);
        if (dragDropPosition.length > 0)
            controller.dropEntries(dragPaths, dragTargetPath, dragDropPosition);
        dragActive = false;
        dragPaths = [];
        dragTargetPath = "";
        dragDropPosition = "";
        dragIndicatorY = -1;
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
                    visible: root.selectedCount === 0
                    text: qsTr("%n item(s)", "", root.controller.model.count)
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                }

                Label {
                    visible: root.selectedCount > 0
                    text: qsTr("%n selected", "", root.selectedCount)
                    color: Theme.accent
                    font.pointSize: TypeScale.label
                    font.weight: TypeScale.semibold
                }

                IconButton {
                    visible: root.selectedCount > 0
                    controlSize: 32
                    flat: true
                    iconText: "×"
                    text: qsTr("Clear selection")
                    onClicked: root.controller.selection.clear()
                }

                FluentButton {
                    visible: root.controller.sortKey.length > 0
                    compact: true
                    flat: true
                    text: qsTr("Manual order")
                    Accessible.description: qsTr("Clear sorting to enable drag reordering")
                    onClicked: root.controller.clearSort()
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

            DataTableHeader {
                Layout.fillWidth: true

                DataTableHeaderCell {
                    preferredWidth: root.stateColumnWidth
                    text: qsTr("State")
                    sortable: true
                    sortDirection: root.sortDirection("state")
                    onSortRequested: root.controller.toggleSort("state")
                }

                DataTableHeaderCell {
                    fillWidth: true
                    text: qsTr("Replacement")
                    sortable: true
                    sortDirection: root.sortDirection("name")
                    onSortRequested: root.controller.toggleSort("name")
                }

                DataTableHeaderCell {
                    preferredWidth: root.actionColumnWidth
                    text: qsTr("Action")
                    sortable: true
                    sortDirection: root.sortDirection("action")
                    onSortRequested: root.controller.toggleSort("action")
                }

                DataTableHeaderCell {
                    preferredWidth: root.sourceColumnWidth
                    visible: root.showSourceColumn
                    text: qsTr("Source")
                    sortable: true
                    sortDirection: root.sortDirection("replacement")
                    onSortRequested: root.controller.toggleSort("replacement")
                }

                DataTableHeaderCell {
                    preferredWidth: root.organizeColumnWidth
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
                        id: ruleDelegate

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
                        showSource: root.showSourceColumn
                        manualOrder: root.controller.manualOrder
                        filtering: root.controller.query.length > 0
                        stateColumnWidth: root.stateColumnWidth
                        actionColumnWidth: root.actionColumnWidth
                        sourceColumnWidth: root.sourceColumnWidth
                        organizeColumnWidth: root.organizeColumnWidth
                        dropPosition: root.dragActive && root.dragTargetPath === model.path ? root.dragDropPosition : ""
                        onEnabledToggled: (path, enabled) => {
                            root.controller.setEntryEnabled(path, enabled);
                        }
                        onEditRequested: path => root.openRuleEditor(path)
                        onGroupRenameRequested: (path, name) => root.openNameDialog("renameGroup", path, name)
                        onExpansionToggled: (path, expanded) => root.controller.setGroupExpanded(path, expanded)
                        onSelectionRequested: (path, toggle, extend) => root.controller.selectEntry(path, toggle, extend)
                        onContextMenuRequested: (path, sceneX, sceneY) => root.openContextMenu(path, ruleDelegate.entryKind, ruleDelegate.entryName, ruleDelegate.entryExpanded, ruleDelegate.canMoveUp, ruleDelegate.canMoveDown, sceneX, sceneY)
                        onDragStarted: (path, sceneX, sceneY) => root.beginDrag(path, sceneX, sceneY)
                        onDragMoved: (sceneX, sceneY) => root.updateDragTarget(sceneX, sceneY)
                        onDragFinished: (sceneX, sceneY) => root.finishDrag(sceneX, sceneY)
                    }

                    Rectangle {
                        parent: rulesView
                        x: Theme.spaceSm
                        y: root.dragIndicatorY - 1
                        z: 10
                        width: rulesView.width - Theme.spaceSm * 2
                        height: 2
                        visible: root.dragActive && root.dragIndicatorY >= 0 && root.dragDropPosition !== "into"
                        color: Theme.accent
                        radius: 1
                        Accessible.ignored: true
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
        id: contextMenuLoader

        active: false
        sourceComponent: Component {
            Replacer.ReplacerContextMenu {
                controller: root.controller
                appController: root.appController
                hostItem: root
                onEditRequested: path => root.openRuleEditor(path)
                onRenameGroupRequested: (path, name) => root.openNameDialog("renameGroup", path, name)
                onGroupRequested: root.openNameDialog("groupSelection", "", "")
                onMoveToRequested: paths => {
                    root.pendingMovePaths = paths;
                    moveDialogLoader.active = true;
                }
                onDeleteRequested: root.openDeleteDialog("entries")
                onClosed: contextMenuLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ReplacerContextMenu).present(root.pendingContextPath, root.pendingContextKind, root.pendingContextName, root.pendingContextExpanded, root.pendingContextCanMoveUp, root.pendingContextCanMoveDown, root.pendingContextSceneX, root.pendingContextSceneY);
        }
    }

    Loader {
        id: moveDialogLoader

        active: false
        sourceComponent: Component {
            Replacer.ReplacerMoveDialog {
                controller: root.controller
                selectedPaths: root.pendingMovePaths
                onClosed: moveDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Replacer.ReplacerMoveDialog).open();
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

    Keys.onPressed: event => {
        const commandModifier = (event.modifiers & Qt.ControlModifier) !== 0 || (event.modifiers & Qt.MetaModifier) !== 0;
        if (commandModifier && event.key === Qt.Key_A) {
            root.controller.selectAllVisible();
            event.accepted = true;
        } else if (event.key === Qt.Key_Escape && root.selectedCount > 0) {
            root.controller.selection.clear();
            event.accepted = true;
        } else if (event.key === Qt.Key_Delete && root.selectedCount > 0) {
            root.openDeleteDialog("entries");
            event.accepted = true;
        }
    }
}
