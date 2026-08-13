import Fleasion.Components
import Fleasion.Theme
import QtMultimedia
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick3D

Rectangle {
    id: root

    required property var controller
    required property var appController
    property string assetKey
    readonly property bool canPreview: controller.previewKind !== 'none'
    property real meshYaw: 25
    property real meshPitch: -12
    property point dragStart: Qt.point(0, 0)

    function formatTime(milliseconds) {
        const seconds = Math.floor(milliseconds / 1000);
        return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
    }

    implicitHeight: 240
    radius: Theme.radiusMd
    color: Theme.surfaceSubtle
    border.color: Theme.border
    clip: true

    onAssetKeyChanged: controller.loadPreview(assetKey)

    StackLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceXs
        currentIndex: {
            const kinds = ['image', 'audio', 'mesh', 'text', 'hex', 'font', 'document', 'animation', 'texturepack'];
            const index = kinds.indexOf(root.controller.previewKind);
            return index >= 0 ? index : kinds.length;
        }

        Image {
            Layout.fillWidth: true
            Layout.fillHeight: true
            source: root.controller.previewSource
            asynchronous: true
            cache: true
            fillMode: Image.PreserveAspectFit
            mipmap: true
            Accessible.name: qsTr('Cached image preview')
        }

        ColumnLayout {
            spacing: Theme.spaceSm

            Item {
                Layout.fillHeight: true
            }

            Label {
                Layout.alignment: Qt.AlignHCenter
                text: mediaPlayer.playbackState === MediaPlayer.PlayingState ? '♫' : '♪'
                color: Theme.accent
                font.pointSize: TypeScale.display * 2
            }

            FluentSlider {
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, mediaPlayer.duration)
                value: mediaPlayer.position
                Accessible.name: qsTr('Audio position')
                onMoved: mediaPlayer.position = value
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter

                FluentButton {
                    text: mediaPlayer.playbackState === MediaPlayer.PlayingState ? qsTr('Pause') : qsTr('Play')
                    onClicked: {
                        if (mediaPlayer.playbackState === MediaPlayer.PlayingState)
                            mediaPlayer.pause();
                        else
                            mediaPlayer.play();
                    }
                }

                Label {
                    text: qsTr('%1 / %2').arg(root.formatTime(mediaPlayer.position)).arg(root.formatTime(mediaPlayer.duration))
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }
            }

            Item {
                Layout.fillHeight: true
            }

            MediaPlayer {
                id: mediaPlayer

                source: root.controller.previewKind === 'audio' ? root.controller.previewSource : ''
                audioOutput: AudioOutput {}
            }
        }

        View3D {
            environment: SceneEnvironment {
                backgroundMode: SceneEnvironment.Color
                clearColor: Theme.surfaceSubtle
                antialiasingMode: SceneEnvironment.MSAA
                antialiasingQuality: SceneEnvironment.High
            }

            PerspectiveCamera {
                id: camera

                position: Qt.vector3d(0, 0.25, 4.5)
                eulerRotation.x: -4
            }

            DirectionalLight {
                eulerRotation.x: -35
                eulerRotation.y: -30
                brightness: 1.2
                castsShadow: true
            }

            DirectionalLight {
                eulerRotation.x: 30
                eulerRotation.y: 160
                brightness: 0.5
            }

            Model {
                id: meshModel

                geometry: root.controller.meshGeometry
                eulerRotation.y: root.meshYaw
                eulerRotation.x: root.meshPitch

                materials: PrincipledMaterial {
                    baseColor: Theme.accent
                    roughness: 0.5
                    metalness: 0.05
                }
            }

            DragHandler {
                target: null
                onActiveChanged: {
                    if (active)
                        root.dragStart = Qt.point(root.meshYaw, root.meshPitch);
                }
                onTranslationChanged: {
                    root.meshYaw = root.dragStart.x + translation.x * 0.4;
                    root.meshPitch = Math.max(-85, Math.min(85, root.dragStart.y - translation.y * 0.4));
                }
            }
        }

        ScrollView {
            clip: true

            FluentTextArea {
                text: root.controller.previewText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.NoWrap
                color: Theme.textPrimary
                font.family: 'monospace'
                font.pointSize: TypeScale.caption
                Accessible.name: qsTr('Cached text preview')
            }
        }

        ScrollView {
            clip: true

            FluentTextArea {
                text: root.controller.previewText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.WrapAnywhere
                color: Theme.textSecondary
                font.family: 'monospace'
                font.pointSize: TypeScale.caption
                Accessible.name: qsTr('Cached binary preview')
            }
        }

        FontSpecimenPreview {
            controller: root.controller.fontPreview
        }

        RobloxDocumentPreview {
            controller: root.controller.documentPreview
        }

        AnimationPreview {
            controller: root.controller.animationPreview
        }

        TexturePackPreview {
            controller: root.controller.texturePackPreview
            appController: root.appController
            cacheBusy: root.controller.task.busy
        }

        EmptyState {
            iconText: '◇'
            title: qsTr('Preview unavailable')
            description: qsTr('You can still export this asset in any supported format.')
        }
    }
}
