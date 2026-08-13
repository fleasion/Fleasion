pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    property real playhead: 0

    function formatSeconds(seconds) {
        const safeSeconds = Math.max(0, seconds);
        const minutes = Math.floor(safeSeconds / 60);
        const remainder = safeSeconds - minutes * 60;
        return minutes + ':' + remainder.toFixed(2).padStart(5, '0');
    }

    Timer {
        id: playbackTimer

        interval: 16
        repeat: true
        onTriggered: {
            if (root.controller.duration <= 0) {
                stop();
                root.playhead = 0;
                return;
            }
            root.playhead = Math.min(root.controller.duration, root.playhead + interval / 1000);
            if (root.playhead >= root.controller.duration)
                stop();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceXs
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    text: root.controller.sourceLabel || qsTr('Animation')
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.subtitle
                    font.weight: TypeScale.semibold
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr('%n keyframe(s)', '', root.controller.keyframeCount) + qsTr(' · %n track(s)', '', root.controller.trackCount) + qsTr(' · %1').arg(root.formatSeconds(root.controller.duration))
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                    elide: Text.ElideRight
                }
            }

            StatusPill {
                visible: root.controller.converter.sourceLoaded
                text: root.controller.converter.detectedRig === 'unknown' ? qsTr('Rig unknown') : root.controller.converter.detectedRig
                status: root.controller.converter.detectedRig === 'unknown' ? 'warning' : 'info'
            }

            FluentButton {
                text: playbackTimer.running ? qsTr('Pause') : qsTr('Play')
                compact: true
                onClicked: {
                    if (playbackTimer.running) {
                        playbackTimer.stop();
                    } else {
                        if (root.playhead >= root.controller.duration)
                            root.playhead = 0;
                        playbackTimer.start();
                    }
                }
            }

            FluentButton {
                text: qsTr('Convert…')
                compact: true
                highlighted: root.controller.converter.canConvertToR6 || root.controller.converter.canConvertToR15
                enabled: root.controller.converter.sourceLoaded && (root.controller.converter.canConvertToR6 || root.controller.converter.canConvertToR15)
                onClicked: conversionDialog.open()
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 34

            Repeater {
                model: root.controller.keyframeMarkers

                Rectangle {
                    required property real modelData

                    x: Math.max(0, Math.min(parent.width - width, modelData * parent.width))
                    y: 2
                    width: 1
                    height: 8
                    color: Theme.textTertiary
                    Accessible.ignored: true
                }
            }

            FluentSlider {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                from: 0
                to: Math.max(0.001, root.controller.duration)
                value: root.playhead
                Accessible.name: qsTr('Animation timeline')
                onMoved: {
                    playbackTimer.stop();
                    root.playhead = value;
                }
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr('%1 / %2').arg(root.formatSeconds(root.playhead)).arg(root.formatSeconds(root.controller.duration))
            color: Theme.textSecondary
            font.family: 'monospace'
            font.pointSize: TypeScale.caption
            horizontalAlignment: Text.AlignRight
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: root.controller.tracksModel
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            reuseItems: true
            Accessible.name: qsTr('Animation pose tracks')

            delegate: AnimationTrackDelegate {
                required property string name
                required property int sampleCount
                required property string coverageText

                width: ListView.view.width
                trackName: name
                sampleCount: sampleCount
                coverageText: coverageText
            }

            ScrollBar.vertical: ScrollBar {}
        }

        Label {
            Layout.fillWidth: true
            visible: root.controller.converter.statusText.length > 0
            text: root.controller.converter.statusText
            color: root.controller.converter.task.busy ? Theme.accent : Theme.textSecondary
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }
    }

    FluentDialog {
        id: conversionDialog

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, parent.width - Theme.spaceXl)
        modal: true
        focus: true
        title: qsTr('Convert cached animation')
        standardButtons: Dialog.NoButton

        contentItem: ColumnLayout {
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: qsTr('Create a converted RBXMX copy. The cached animation is not modified.')
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }

            FluentComboBox {
                id: targetPicker

                Layout.fillWidth: true
                model: root.controller.converter.canConvertToR6 ? ['R6'] : root.controller.converter.canConvertToR15 ? ['R15'] : []
                Accessible.name: qsTr('Target rig')
                onCurrentTextChanged: {
                    if (conversionDialog.opened)
                        conversionDestination.text = root.controller.suggestedOutputUrl(currentText);
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                FluentTextField {
                    id: conversionDestination

                    Layout.fillWidth: true
                    placeholderText: qsTr('Choose a new RBXMX path')
                    Accessible.name: qsTr('Converted animation destination')
                }

                FluentButton {
                    text: qsTr('Browse…')
                    onClicked: conversionFileDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true

                Item {
                    Layout.fillWidth: true
                }

                FluentButton {
                    text: qsTr('Cancel')
                    onClicked: conversionDialog.close()
                }

                FluentButton {
                    text: qsTr('Convert')
                    highlighted: true
                    enabled: !root.controller.converter.task.busy && targetPicker.currentText.length > 0 && conversionDestination.text.length > 0
                    onClicked: {
                        if (root.controller.converter.convert(targetPicker.currentText, conversionDestination.text))
                            conversionDialog.close();
                    }
                }
            }
        }

        onOpened: conversionDestination.text = root.controller.suggestedOutputUrl(targetPicker.currentText)
    }

    FileDialog {
        id: conversionFileDialog

        title: qsTr('Choose converted animation path')
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr('Roblox XML model (*.rbxmx)')]
        onAccepted: conversionDestination.text = selectedFile.toString()
    }
}
