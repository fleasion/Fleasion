pragma ComponentBehavior: Bound

import QtQml

QtObject {
    id: root

    property bool hasDraft: false
    property bool closeViewerOnReplace: true
    property bool communityViewerOpen: false
    property bool ruleEditorOpen: false

    readonly property bool waitingForViewerClose: editorAfterViewerClose

    property bool editorAfterViewerClose: false
    property bool editorRequestPending: false

    signal closeCommunityViewerRequested
    signal openRuleEditorRequested
    signal restoreCommunityViewerRequested

    function requestRuleEditor() {
        if (!hasDraft || ruleEditorOpen || editorRequestPending)
            return;
        editorRequestPending = true;
        openRuleEditorRequested();
    }

    function presentDraft() {
        if (communityViewerOpen || editorAfterViewerClose)
            return;
        requestRuleEditor();
    }

    function communityDraftPrepared() {
        if (!hasDraft || ruleEditorOpen || editorRequestPending)
            return;
        if (!closeViewerOnReplace) {
            requestRuleEditor();
            return;
        }
        if (editorAfterViewerClose)
            return;
        editorAfterViewerClose = true;
        closeCommunityViewerRequested();
    }

    function communityViewerClosed() {
        const shouldOpenEditor = editorAfterViewerClose || hasDraft;
        editorAfterViewerClose = false;
        if (shouldOpenEditor)
            requestRuleEditor();
    }

    function ruleEditorClosed() {
        editorRequestPending = false;
        if (hasDraft) {
            requestRuleEditor();
            return;
        }
        if (communityViewerOpen)
            restoreCommunityViewerRequested();
    }

    onHasDraftChanged: {
        if (hasDraft)
            presentDraft();
    }
    onRuleEditorOpenChanged: {
        if (ruleEditorOpen)
            editorRequestPending = false;
    }
}
