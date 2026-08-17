pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtTest

import "../src/fleasion/qml/screens/replacer" as Replacer

Item {
    id: root

    width: 1000
    height: 300

    QtObject {
        id: selectionStub

        property var keys: ["0", "1"]
        signal selectionChanged

        function values() {
            return keys;
        }

        function clear() {
            keys = [];
            selectionChanged();
        }

        function contains(key) {
            return keys.indexOf(key) !== -1;
        }

        function setSelected(key, selected) {
            const next = keys.slice();
            const currentIndex = next.indexOf(key);
            if (selected && currentIndex === -1)
                next.push(key);
            else if (!selected && currentIndex !== -1)
                next.splice(currentIndex, 1);
            keys = next;
            selectionChanged();
        }
    }

    QtObject {
        id: appControllerStub

        property string copiedText

        function copyText(value) {
            copiedText = value;
        }
    }

    QtObject {
        id: controllerStub

        property var selection: selectionStub
        property var groupDestinations: [
            {
                "path": "",
                "label": "Profile root"
            },
            {
                "path": "2",
                "label": "› Audio"
            }
        ]
        property string lastDestination: "unset"
        property bool lastEnabled: false
        property bool profileRequestResult: false
        property int profileRequestCount: 0
        property bool manualOrder: true
        signal modelChanged

        function canGroupEntries(paths) {
            return paths.length > 0;
        }

        function setEntriesEnabled(_paths, enabled) {
            lastEnabled = enabled;
        }

        function moveEntries(_paths, destination, _index) {
            lastDestination = destination;
        }

        function renameConfig(_oldName, _newName) {
            profileRequestCount += 1;
            return profileRequestResult;
        }

        function entry(_path) {
            return {
                "targets": "1, 2",
                "replacement": "3"
            };
        }

        function setGroupExpanded(_path, _expanded) {
        }

        function selectAllVisible() {
        }

        function moveEntry(_path, _direction) {
        }
    }

    Component {
        id: contextWindowComponent

        ApplicationWindow {
            id: contextWindow

            width: 720
            height: 560
            visible: true
            property alias contextMenu: contextMenu

            Replacer.ReplacerContextMenu {
                id: contextMenu

                objectName: "replacementContextMenu"
                controller: controllerStub
                appController: appControllerStub
                hostItem: contextWindow.contentItem
            }
        }
    }

    Component {
        id: profileDialogComponent

        Replacer.ProfileNameDialog {
            controller: controllerStub
            action: "rename"
            currentName: "Existing"
        }
    }

    Component {
        id: groupDelegateComponent

        Item {
            width: 700
            height: delegate.implicitHeight
            property int expansionCalls: 0
            property bool expandedValue: false
            property int selectionCalls: 0
            property bool selectionToggle: false
            property bool selectionExtend: false
            property int contextCalls: 0

            Replacer.ReplacerRuleDelegate {
                id: delegate

                width: parent.width
                height: implicitHeight
                selectionModel: selectionStub
                entryPath: "0"
                entryKind: "group"
                entryDepth: 0
                entryName: "Audio"
                entryEnabled: false
                entryState: "mixed"
                entryExpanded: false
                childCount: 2
                canMoveUp: false
                canMoveDown: true
                actionText: "Group"
                replacementText: ""
                targetsText: ""
                targetCount: 3
                showSource: true
                manualOrder: true
                filtering: false
                stateColumnWidth: 80
                actionColumnWidth: 110
                sourceColumnWidth: 230
                organizeColumnWidth: 72
                onExpansionToggled: (path, expanded) => {
                    parent.expansionCalls += path === "0" ? 1 : 0;
                    parent.expandedValue = expanded;
                }
                onSelectionRequested: (_path, toggle, extend) => {
                    parent.selectionCalls += 1;
                    parent.selectionToggle = toggle;
                    parent.selectionExtend = extend;
                }
                onContextMenuRequested: (_path, _sceneX, _sceneY) => parent.contextCalls += 1
            }
        }
    }

    TestCase {
        name: "ReplacerOrganizationTests"
        when: windowShown

        function init() {
            selectionStub.keys = ["0", "1"];
            selectionStub.selectionChanged();
            controllerStub.lastDestination = "unset";
            controllerStub.profileRequestResult = false;
            controllerStub.profileRequestCount = 0;
        }

        function test_profileDialogStaysOpenWhenBridgeRejectsName() {
            const dialog = createTemporaryObject(profileDialogComponent, root);
            verify(!!dialog);
            dialog.open();
            tryCompare(dialog, "visible", true);

            dialog.submit();
            compare(controllerStub.profileRequestCount, 1);
            compare(dialog.visible, true);

            controllerStub.profileRequestResult = true;
            dialog.submit();
            compare(controllerStub.profileRequestCount, 2);
            tryCompare(dialog, "visible", false);
        }

        function test_groupDelegateExposesSelectionAndContextActions() {
            const wrapper = createTemporaryObject(groupDelegateComponent, root);
            verify(!!wrapper);
            const expansionButton = findChild(wrapper, "groupExpansionButton");
            const contextButton = findChild(wrapper, "entryContextMenuButton");
            verify(!!expansionButton);
            verify(!!contextButton);

            mouseClick(expansionButton);
            compare(wrapper.expansionCalls, 1);
            compare(wrapper.expandedValue, true);
            mouseClick(contextButton);
            compare(wrapper.contextCalls, 1);

            const delegate = findChild(wrapper, "replacerRuleDelegate");
            delegate.forceActiveFocus();
            keyClick(Qt.Key_Space);
            compare(wrapper.selectionCalls, 1);
            compare(wrapper.selectionToggle, true);
            compare(wrapper.selectionExtend, false);

            mouseClick(delegate, 360, delegate.height / 2, Qt.LeftButton, Qt.ControlModifier | Qt.ShiftModifier);
            compare(wrapper.selectionCalls, 2);
            compare(wrapper.selectionToggle, true);
            compare(wrapper.selectionExtend, true);
        }

        function test_contextMenuExposesBulkActionsAndFitsMinimumWindow() {
            selectionStub.keys = ["0"];
            selectionStub.selectionChanged();
            controllerStub.lastEnabled = true;
            const window = createTemporaryObject(contextWindowComponent, root);
            verify(!!window);
            tryCompare(window, "visible", true);

            window.contextMenu.present("0", "rule", "Face", false, false, true, 680, 520);
            tryCompare(window.contextMenu, "visible", true);
            tryVerify(() => window.contextMenu.x >= 0);
            tryVerify(() => window.contextMenu.y >= 0);
            tryVerify(() => window.contextMenu.x + window.contextMenu.width <= window.width);
            tryVerify(() => window.contextMenu.y + window.contextMenu.height <= window.height);

            const expectedActions = ["editEntryAction", "enableSelectionAction", "disableSelectionAction", "groupSelectionAction", "moveSelectionAction", "selectAllAction", "deleteSelectionAction"];
            for (const objectName of expectedActions)
                verify(!!findChild(window.contextMenu, objectName), objectName);

            const disableAction = findChild(window.contextMenu, "disableSelectionAction");
            mouseClick(disableAction);
            compare(controllerStub.lastEnabled, false);
            tryCompare(window.contextMenu, "visible", false);
        }
    }
}
