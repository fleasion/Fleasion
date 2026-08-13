pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import "../src/fleasion/qml/screens/modifications" as Modifications

Item {
    id: root

    width: 900
    height: 700

    QtObject {
        id: selectionStub

        property var keys: []

        signal selectionChanged

        function contains(key) {
            return keys.indexOf(key) !== -1;
        }

        function setSelected(key, selected) {
            const nextKeys = keys.slice();
            const currentIndex = nextKeys.indexOf(key);
            if (selected && currentIndex === -1)
                nextKeys.push(key);
            else if (!selected && currentIndex !== -1)
                nextKeys.splice(currentIndex, 1);
            else
                return;
            keys = nextKeys;
            selectionChanged();
        }

        function values() {
            return keys.slice();
        }
    }

    ListModel {
        id: catalogModelStub
    }

    QtObject {
        id: catalogTaskStub

        property bool busy: false
        property string message: ""
    }

    QtObject {
        id: controllerStub

        property var catalogSelection: selectionStub
        property var catalogModel: catalogModelStub
        property var catalogTask: catalogTaskStub

        function loadFastFlagCatalog(_refresh) {
        }
        function filterFastFlagCatalog(_query) {
        }
        function addCatalogFlags(_names) {
            return 0;
        }
    }

    Component {
        id: dialogComponent

        Modifications.FastFlagCatalogDialog {
            controller: controllerStub
        }
    }

    TestCase {
        name: "FastFlagCatalogDialogTests"
        when: windowShown

        function init() {
            selectionStub.keys = [];
            selectionStub.selectionChanged();
            catalogModelStub.clear();
            catalogModelStub.append({
                "name": "FFlagAlpha",
                "value": "True",
                "family": "FFlag",
                "published": true
            });
            catalogModelStub.append({
                "name": "FFlagBeta",
                "value": "False",
                "family": "FFlag",
                "published": true
            });
        }

        function test_selectionFollowsReassignedDelegateRole() {
            const dialog = createTemporaryObject(dialogComponent, root);
            verify(!!dialog, "Component exists");
            dialog.open();
            tryCompare(dialog, "visible", true);

            const catalogList = findChild(dialog, "catalogList");
            verify(!!catalogList, "Object exists");
            tryCompare(catalogList, "count", 2);

            tryVerify(() => catalogList.itemAtIndex(0) !== null);
            const catalogDelegate = catalogList.itemAtIndex(0);
            verify(!!catalogDelegate, "Object exists");
            compare(catalogDelegate.name, "FFlagAlpha");
            compare(catalogDelegate.selected, false);

            selectionStub.setSelected("FFlagAlpha", true);
            tryCompare(catalogDelegate, "selected", true);

            catalogDelegate.name = "FFlagBeta";
            tryCompare(catalogDelegate, "selected", false);

            selectionStub.setSelected("FFlagBeta", true);
            tryCompare(catalogDelegate, "selected", true);
            dialog.close();
        }
    }
}
