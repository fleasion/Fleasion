import QtQuick
import QtTest

import "../src/fleasion/qml/screens/modifications" as Modifications

Item {
    id: root

    width: 720
    height: 560

    Component {
        id: riskDialogComponent

        Modifications.CustomFastFlagRiskDialog {}
    }

    TestCase {
        name: "SafetyDialogTests"
        when: windowShown

        function test_fastFlagRiskRequiresCountdown() {
            const dialog = createTemporaryObject(riskDialogComponent, root);
            verify(!!dialog);
            confirmedSpy.target = dialog;
            confirmedSpy.clear();

            dialog.open();
            tryCompare(dialog, "visible", true);
            const acceptButton = findChild(dialog, "dialogAcceptButton");
            verify(!!acceptButton);
            compare(dialog.secondsRemaining, 15);
            compare(acceptButton.enabled, false);

            dialog.secondsRemaining = 0;
            tryCompare(acceptButton, "enabled", true);
            mouseClick(acceptButton);
            compare(confirmedSpy.count, 1);
            tryCompare(dialog, "visible", false);
        }

        SignalSpy {
            id: confirmedSpy

            signalName: "confirmed"
        }
    }
}
