pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import "../src/fleasion/qml/screens/subplaces" as Subplaces

Item {
    id: root

    width: 600
    height: 320

    ListModel {
        id: savedPlaces

        ListElement {
            placeId: "123"
            name: "Favorite world"
        }
    }

    QtObject {
        id: controllerStub

        property string toggledPlaceId

        function usePlace(_placeId) {
        }

        function toggleFavorite(placeId) {
            toggledPlaceId = placeId;
        }

        function removeRecent(_placeId) {
        }
    }

    Component {
        id: panelComponent

        Subplaces.SavedPlacesPanel {
            width: 560
            height: 240
            heading: "Favorites"
            savedModel: savedPlaces
            controller: controllerStub
            favoriteEntries: true
        }
    }

    TestCase {
        name: "SavedPlacesPanelTests"
        when: windowShown

        SignalSpy {
            id: renameSpy

            signalName: "renameRequested"
        }

        function init() {
            controllerStub.toggledPlaceId = "";
            renameSpy.target = null;
            renameSpy.clear();
        }

        function test_favoriteCanBeRenamedAndRemoved() {
            const panel = createTemporaryObject(panelComponent, root);
            verify(!!panel);
            renameSpy.target = panel;
            wait(0);

            const renameButton = findChild(panel, "renameSavedPlaceButton");
            const removeButton = findChild(panel, "removeSavedPlaceButton");
            verify(!!renameButton);
            verify(!!removeButton);
            compare(removeButton.visible, true);

            mouseClick(renameButton);
            compare(renameSpy.count, 1);
            compare(renameSpy.signalArguments[0][0], "123");
            compare(renameSpy.signalArguments[0][1], "Favorite world");

            mouseClick(removeButton);
            compare(controllerStub.toggledPlaceId, "123");
        }
    }
}
