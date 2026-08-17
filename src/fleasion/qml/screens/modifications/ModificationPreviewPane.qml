pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtMultimedia
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property string title
    required property bool available
    required property string sizeText
    required property string previewUrl
    required property string previewKind
    required property string summary
    property bool convertedAvailable: false
    property var meshGeometry: null
    signal exportRequested
    signal convertedExportRequested

    function formatTime(milliseconds) {
        const seconds = Math.floor(milliseconds / 1000);
        return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    }

    spacing: Theme.spaceXs

    RowLayout {
        Layout.fillWidth: true

        Label {
            Layout.fillWidth: true
            text: root.title
            color: Theme.textPrimary
            font.pointSize: TypeScale.subtitle
            font.weight: TypeScale.semibold
        }

        StatusPill {
            text: root.available ? root.sizeText : qsTr("Unavailable")
            status: root.available ? "neutral" : "warning"
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.fillHeight: true
        color: Theme.surfaceSubtle
        radius: 0
        border.width: 1
        border.color: Theme.border
        clip: true

        Loader {
            anchors.fill: parent
            anchors.margins: Theme.spaceSm
            sourceComponent: root.previewKind === "image" && root.previewUrl.length > 0 ? imagePreview : root.previewKind === "audio" && root.previewUrl.length > 0 ? audioPreview : root.previewKind === "font" && root.previewUrl.length > 0 ? fontPreview : root.previewKind === "mesh" && root.meshGeometry ? meshPreview : root.previewKind === "text" && root.available ? textPreview : summaryPreview
        }
    }

    RowLayout {
        Layout.fillWidth: true

        Item {
            Layout.fillWidth: true
        }

        FluentButton {
            visible: root.convertedAvailable
            text: qsTr("Export converted…")
            onClicked: root.convertedExportRequested()
        }

        FluentButton {
            text: qsTr("Export file…")
            enabled: root.available
            onClicked: root.exportRequested()
        }
    }

    Component {
        id: imagePreview

        Item {
            Image {
                id: previewImage

                anchors.fill: parent
                source: root.previewUrl
                sourceSize.width: 720
                sourceSize.height: 480
                asynchronous: true
                cache: false
                fillMode: Image.PreserveAspectFit
                Accessible.name: qsTr("%1 preview").arg(root.title)
            }

            Label {
                anchors.centerIn: parent
                visible: previewImage.status === Image.Error
                text: qsTr("Qt could not render this image.")
                color: Theme.textSecondary
                font.pointSize: TypeScale.body
            }
        }
    }

    Component {
        id: audioPreview

        ColumnLayout {
            spacing: Theme.spaceXs

            Item {
                Layout.fillHeight: true
            }

            Label {
                Layout.alignment: Qt.AlignHCenter
                text: audioPlayer.playbackState === MediaPlayer.PlayingState ? "♫" : "♪"
                color: Theme.accent
                font.pointSize: TypeScale.title
                Accessible.ignored: true
            }

            FluentSlider {
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, audioPlayer.duration)
                value: audioPlayer.position
                Accessible.name: qsTr("Audio position")
                onMoved: audioPlayer.position = value
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter

                FluentButton {
                    compact: true
                    text: audioPlayer.playbackState === MediaPlayer.PlayingState ? qsTr("Pause") : qsTr("Play")
                    onClicked: {
                        if (audioPlayer.playbackState === MediaPlayer.PlayingState)
                            audioPlayer.pause();
                        else
                            audioPlayer.play();
                    }
                }

                Label {
                    text: qsTr("%1 / %2").arg(root.formatTime(audioPlayer.position)).arg(root.formatTime(audioPlayer.duration))
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }
            }

            Label {
                Layout.fillWidth: true
                visible: audioPlayer.error !== MediaPlayer.NoError
                text: audioPlayer.errorString || qsTr("Qt could not play this audio file.")
                color: Theme.danger
                font.pointSize: TypeScale.label
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }

            Item {
                Layout.fillHeight: true
            }

            MediaPlayer {
                id: audioPlayer

                source: root.previewUrl
                audioOutput: AudioOutput {}
            }
        }
    }

    Component {
        id: fontPreview

        ColumnLayout {
            spacing: Theme.spaceXs

            FontLoader {
                id: previewFont

                source: root.previewUrl
            }

            Label {
                Layout.fillWidth: true
                text: previewFont.status === FontLoader.Ready ? previewFont.name : previewFont.status === FontLoader.Error ? qsTr("Qt could not load this font.") : qsTr("Loading font…")
                color: previewFont.status === FontLoader.Error ? Theme.danger : Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideRight
            }

            FluentTextArea {
                Layout.fillWidth: true
                Layout.fillHeight: true
                readOnly: true
                text: qsTr("The quick brown fox jumps over the lazy dog.\nABCDEFGHIJKLMNOPQRSTUVWXYZ · 0123456789")
                wrapMode: TextEdit.Wrap
                font.family: previewFont.status === FontLoader.Ready ? previewFont.name : ""
                font.pointSize: TypeScale.display
                Accessible.name: qsTr("Font specimen")
            }
        }
    }

    Component {
        id: textPreview

        FluentScrollView {
            contentWidth: availableWidth
            Label {
                width: parent.width
                text: root.summary
                color: Theme.textSecondary
                font.family: "monospace"
                font.pointSize: TypeScale.label
                wrapMode: Text.WrapAnywhere
            }
        }
    }

    Component {
        id: meshPreview

        ModificationMeshPreview {
            geometry: root.meshGeometry
            accessibleName: qsTr("%1 interactive mesh preview").arg(root.title)
        }
    }

    Component {
        id: summaryPreview

        ColumnLayout {
            spacing: Theme.spaceSm

            Item {
                Layout.fillHeight: true
            }

            Label {
                Layout.alignment: Qt.AlignHCenter
                text: root.available ? root.previewKind === "audio" ? "♫" : root.previewKind === "mesh" ? "◇" : root.previewKind === "font" ? "Aa" : "◫" : "!"
                color: root.available ? Theme.accent : Theme.warning
                font.pointSize: TypeScale.title
                Accessible.ignored: true
            }

            Label {
                Layout.fillWidth: true
                text: root.summary
                color: Theme.textSecondary
                font.pointSize: TypeScale.body
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }

            Item {
                Layout.fillHeight: true
            }
        }
    }
}
