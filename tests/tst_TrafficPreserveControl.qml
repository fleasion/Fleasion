pragma ComponentBehavior: Bound

import QtQuick
import QtTest

import "../src/fleasion/qml/screens/proxy" as Proxy

Item {
    id: root

    width: 320
    height: 80

    QtObject {
        id: controllerStub

        property bool trafficPreserve: false
        property int setCalls: 0

        function setTrafficPreserve(enabled) {
            trafficPreserve = enabled;
            setCalls += 1;
        }
    }

    Component {
        id: controlComponent

        Proxy.TrafficPreserveControl {
            controller: controllerStub
        }
    }

    TestCase {
        name: "TrafficPreserveControlTests"
        when: windowShown

        function test_toggleUsesControllerAndTracksExternalState() {
            const control = createTemporaryObject(controlComponent, root);
            verify(!!control, "Component exists");
            compare(control.checked, false);

            mouseClick(control);
            tryCompare(controllerStub, "trafficPreserve", true);
            compare(controllerStub.setCalls, 1);

            controllerStub.trafficPreserve = false;
            tryCompare(control, "checked", false);
        }
    }
}
