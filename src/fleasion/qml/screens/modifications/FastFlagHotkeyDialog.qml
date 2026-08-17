pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller
    property string flagName
    property bool captureReady: false

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, parent.width - Theme.spaceXxl)
    modal: true
    title: qsTr("Set a global FastFlag hotkey")
    closePolicy: Popup.NoAutoClose

    onOpened: {
        root.captureReady = root.controller.beginFastFlagHotkeyCapture(root.flagName);
        if (root.captureReady)
            captureSurface.forceActiveFocus();
    }
    onClosed: root.controller.cancelFastFlagHotkeyCapture()

    Connections {
        target: root.controller

        function onHotkeyCaptureCompleted(name, _label) {
            if (name === root.flagName)
                root.close();
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("Assign a physical key, mouse button, or wheel direction to toggle %1 while Roblox is focused. Escape can be assigned; use Cancel to leave unchanged.").arg(root.flagName)
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        Rectangle {
            id: captureSurface

            Layout.fillWidth: true
            Layout.preferredHeight: 116
            color: !root.captureReady ? Theme.warningSubtle : activeFocus ? Theme.accentSubtle : Theme.surfaceSubtle
            radius: Theme.radiusSm
            border.width: activeFocus ? 2 : 1
            border.color: activeFocus ? Theme.focusRing : Theme.border
            activeFocusOnTab: true
            Accessible.role: Accessible.EditableText
            Accessible.name: qsTr("Hotkey capture area")

            Label {
                anchors.centerIn: parent
                width: parent.width - Theme.spaceXl
                text: root.controller.hotkeyCaptureMessage || qsTr("Waiting for input…")
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }

            Keys.onPressed: event => {
                if (event.isAutoRepeat)
                    return;
                event.accepted = root.controller.captureFastFlagNativeKey(event.nativeScanCode, event.key, event.modifiers);
            }
            Keys.onReleased: event => {
                if (event.isAutoRepeat)
                    return;
                event.accepted = root.controller.releaseFastFlagNativeKey(event.nativeScanCode, event.key);
            }

            MouseArea {
                anchors.fill: parent
                enabled: root.captureReady
                acceptedButtons: Qt.AllButtons
                onPressed: mouse => {
                    let code = 0;
                    if (mouse.button === Qt.LeftButton)
                        code = 1;
                    else if (mouse.button === Qt.RightButton)
                        code = 2;
                    else if (mouse.button === Qt.MiddleButton)
                        code = 4;
                    else if (mouse.button === Qt.BackButton)
                        code = 5;
                    else if (mouse.button === Qt.ForwardButton)
                        code = 6;
                    if (code > 0)
                        root.controller.captureFastFlagPointer("mouse", code, mouse.modifiers);
                }
                onWheel: wheel => root.controller.captureFastFlagPointer("wheel", wheel.angleDelta.y > 0 ? 1 : -1, wheel.modifiers)
            }
        }

        RowLayout {
            Layout.fillWidth: true

            FluentButton {
                text: qsTr("Clear assignment")
                danger: true
                onClicked: {
                    root.controller.clearFastFlagHotkey(root.flagName);
                    root.close();
                }
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                visible: !root.captureReady && root.controller.linuxHotkeyPermissionSetupAvailable
                text: qsTr("Set up access…")
                onClicked: root.controller.setupLinuxHotkeyPermissions()
            }

            FluentButton {
                visible: !root.captureReady
                text: qsTr("Retry")
                onClicked: {
                    root.captureReady = root.controller.beginFastFlagHotkeyCapture(root.flagName);
                    if (root.captureReady)
                        captureSurface.forceActiveFocus();
                }
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }
        }
    }
}
