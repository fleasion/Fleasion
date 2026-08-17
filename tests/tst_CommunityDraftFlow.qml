pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import "../src/fleasion/qml/screens/replacer" as Replacer

Item {
    id: root

    width: 320
    height: 120

    Replacer.CommunityDraftFlow {
        id: flow
    }

    TestCase {
        name: "CommunityDraftFlowTests"
        when: windowShown

        SignalSpy {
            id: closeSpy

            target: flow
            signalName: "closeCommunityViewerRequested"
        }

        SignalSpy {
            id: openSpy

            target: flow
            signalName: "openRuleEditorRequested"
        }

        SignalSpy {
            id: restoreSpy

            target: flow
            signalName: "restoreCommunityViewerRequested"
        }

        function init() {
            flow.hasDraft = false;
            flow.closeViewerOnReplace = true;
            flow.communityViewerOpen = false;
            flow.ruleEditorOpen = false;
            flow.editorAfterViewerClose = false;
            flow.editorRequestPending = false;
            closeSpy.clear();
            openSpy.clear();
            restoreSpy.clear();
        }

        function test_defaultClosesViewerBeforeOpeningEditor() {
            flow.communityViewerOpen = true;
            flow.hasDraft = true;
            flow.communityDraftPrepared();
            flow.communityDraftPrepared();

            compare(closeSpy.count, 1);
            compare(openSpy.count, 0);
            compare(flow.waitingForViewerClose, true);

            flow.communityViewerOpen = false;
            flow.communityViewerClosed();
            compare(openSpy.count, 1);
            compare(flow.waitingForViewerClose, false);
        }

        function test_disabledSettingKeepsViewerForSaveOrCancelReturn() {
            flow.closeViewerOnReplace = false;
            flow.communityViewerOpen = true;
            flow.hasDraft = true;
            flow.communityDraftPrepared();

            compare(closeSpy.count, 0);
            compare(openSpy.count, 1);

            flow.ruleEditorOpen = true;
            flow.hasDraft = false;
            flow.ruleEditorOpen = false;
            flow.ruleEditorClosed();
            compare(restoreSpy.count, 1);
        }

        function test_externalDraftIgnoresViewerPreferenceAndDoesNotDuplicateEditor() {
            flow.closeViewerOnReplace = false;
            flow.hasDraft = true;
            compare(openSpy.count, 1);

            flow.presentDraft();
            compare(openSpy.count, 1);

            flow.ruleEditorOpen = true;
            flow.hasDraft = false;
            flow.hasDraft = true;
            compare(openSpy.count, 1);

            flow.ruleEditorOpen = false;
            flow.ruleEditorClosed();
            compare(openSpy.count, 2);
            compare(closeSpy.count, 0);
        }
    }
}
