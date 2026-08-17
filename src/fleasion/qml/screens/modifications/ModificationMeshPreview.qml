pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick3D

FocusScope {
    id: root

    required property var geometry
    property string accessibleName: qsTr("Interactive mesh preview")
    property real orbitYaw: 25
    property real orbitPitch: -12
    property real cameraDistance: 4.5
    property point dragStart: Qt.point(0, 0)

    function orbitBy(yaw, pitch) {
        orbitYaw = (orbitYaw + yaw) % 360;
        orbitPitch = Math.max(-85, Math.min(85, orbitPitch + pitch));
    }

    function zoomBy(amount) {
        cameraDistance = Math.max(2.4, Math.min(9, cameraDistance + amount));
    }

    function resetView() {
        orbitYaw = 25;
        orbitPitch = -12;
        cameraDistance = 4.5;
    }

    activeFocusOnTab: true
    Accessible.role: Accessible.Pane
    Accessible.name: accessibleName

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Left)
            root.orbitBy(-10, 0);
        else if (event.key === Qt.Key_Right)
            root.orbitBy(10, 0);
        else if (event.key === Qt.Key_Up)
            root.orbitBy(0, -10);
        else if (event.key === Qt.Key_Down)
            root.orbitBy(0, 10);
        else if (event.key === Qt.Key_Plus || event.key === Qt.Key_Equal)
            root.zoomBy(-0.35);
        else if (event.key === Qt.Key_Minus)
            root.zoomBy(0.35);
        else if (event.key === Qt.Key_Home)
            root.resetView();
        else
            return;
        event.accepted = true;
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            View3D {
                anchors.fill: parent

                environment: SceneEnvironment {
                    backgroundMode: SceneEnvironment.Color
                    clearColor: Theme.surfaceSubtle
                    antialiasingMode: SceneEnvironment.MSAA
                    antialiasingQuality: SceneEnvironment.High
                }

                Node {
                    eulerRotation.x: root.orbitPitch
                    eulerRotation.y: root.orbitYaw

                    PerspectiveCamera {
                        position: Qt.vector3d(0, 0, root.cameraDistance)
                        clipNear: 0.1
                        clipFar: 100
                        fieldOfView: 35
                    }
                }

                DirectionalLight {
                    eulerRotation.x: -35
                    eulerRotation.y: -30
                    brightness: 1.25
                }

                DirectionalLight {
                    eulerRotation.x: 35
                    eulerRotation.y: 155
                    brightness: 0.55
                }

                Model {
                    geometry: root.geometry

                    materials: PrincipledMaterial {
                        baseColor: Theme.accent
                        roughness: 0.52
                        metalness: 0.04
                    }
                }

                DragHandler {
                    target: null
                    onActiveChanged: {
                        if (active) {
                            root.forceActiveFocus();
                            root.dragStart = Qt.point(root.orbitYaw, root.orbitPitch);
                        }
                    }
                    onTranslationChanged: {
                        root.orbitYaw = root.dragStart.x + translation.x * 0.4;
                        root.orbitPitch = Math.max(-85, Math.min(85, root.dragStart.y - translation.y * 0.4));
                    }
                }

                WheelHandler {
                    target: null
                    onWheel: event => root.zoomBy(event.angleDelta.y > 0 ? -0.35 : 0.35)
                }

                TapHandler {
                    onTapped: root.forceActiveFocus()
                    onDoubleTapped: root.resetView()
                }
            }

            Rectangle {
                anchors.fill: parent
                color: "transparent"
                border.width: root.activeFocus ? 2 : 0
                border.color: Theme.focusRing
                Accessible.ignored: true
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            color: Theme.surfaceSubtle
            border.width: 0

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: 1
                color: Theme.border
                Accessible.ignored: true
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.spaceXxs
                anchors.rightMargin: Theme.spaceXxs
                spacing: Theme.spaceXxs

                IconButton {
                    controlSize: 28
                    flat: true
                    iconText: "←"
                    text: qsTr("Orbit left")
                    onClicked: root.orbitBy(-10, 0)
                }

                IconButton {
                    controlSize: 28
                    flat: true
                    iconText: "↑"
                    text: qsTr("Orbit up")
                    onClicked: root.orbitBy(0, -10)
                }

                IconButton {
                    controlSize: 28
                    flat: true
                    iconText: "↓"
                    text: qsTr("Orbit down")
                    onClicked: root.orbitBy(0, 10)
                }

                IconButton {
                    controlSize: 28
                    flat: true
                    iconText: "→"
                    text: qsTr("Orbit right")
                    onClicked: root.orbitBy(10, 0)
                }

                FluentSlider {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 64
                    Layout.maximumWidth: 112
                    from: 2.4
                    to: 9
                    value: from + to - root.cameraDistance
                    Accessible.name: qsTr("Camera zoom")
                    onMoved: root.cameraDistance = from + to - value
                }

                Label {
                    visible: root.width >= 420
                    text: qsTr("%1×").arg((4.5 / root.cameraDistance).toFixed(1))
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }

                Item {
                    Layout.fillWidth: true
                    visible: root.width >= 420
                }

                IconButton {
                    controlSize: 28
                    flat: true
                    iconText: "↺"
                    text: qsTr("Reset camera")
                    onClicked: root.resetView()
                }
            }
        }
    }
}
