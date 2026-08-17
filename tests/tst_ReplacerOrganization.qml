pragma ComponentBehavior: Bound

import QtQuick
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
        id: selectionBarComponent

        Replacer.ReplacerSelectionBar {
            width: 900
            height: implicitHeight
            controller: controllerStub
        }
    }

    Component {
        id: groupDelegateComponent

        Item {
            width: 700
            height: delegate.implicitHeight
            property int expansionCalls: 0
            property bool expandedValue: false
            property int moveDirection: 0

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
                onExpansionToggled: (path, expanded) => {
                    parent.expansionCalls += path === "0" ? 1 : 0;
                    parent.expandedValue = expanded;
                }
                onMoveRequested: (_path, direction) => parent.moveDirection = direction
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

        function test_selectionBarFoldsWithoutClipping() {
            const bar = createTemporaryObject(selectionBarComponent, root);
            verify(!!bar);
            compare(bar.compactLayout, false);

            bar.width = 680;
            tryCompare(bar, "compactLayout", true);
            tryVerify(() => bar.height > 64);

            const controls = [findChild(bar, "groupSelectionButton"), findChild(bar, "moveDestinationPicker"), findChild(bar, "moveSelectionButton"), findChild(bar, "clearSelectionButton"), findChild(bar, "deleteSelectionButton")];
            for (const control of controls) {
                verify(!!control);
                const topLeft = control.mapToItem(bar, 0, 0);
                verify(topLeft.x >= 0);
                verify(topLeft.y >= 0);
                verify(topLeft.x + control.width <= bar.width);
                verify(topLeft.y + control.height <= bar.height);
            }

            const moveButton = findChild(bar, "moveSelectionButton");
            mouseClick(moveButton);
            compare(controllerStub.lastDestination, "");
        }

        function test_groupDelegateExposesCollapseAndOrderingActions() {
            const wrapper = createTemporaryObject(groupDelegateComponent, root);
            verify(!!wrapper);
            const expansionButton = findChild(wrapper, "groupExpansionButton");
            const upButton = findChild(wrapper, "moveEntryUpButton");
            const downButton = findChild(wrapper, "moveEntryDownButton");
            verify(!!expansionButton);
            verify(!!upButton);
            verify(!!downButton);
            compare(upButton.enabled, false);
            compare(downButton.enabled, true);

            mouseClick(expansionButton);
            compare(wrapper.expansionCalls, 1);
            compare(wrapper.expandedValue, true);
            mouseClick(downButton);
            compare(wrapper.moveDirection, 1);
        }
    }
}
