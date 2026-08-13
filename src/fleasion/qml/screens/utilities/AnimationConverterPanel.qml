pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Card {
    id: root

    required property var controller
    readonly property var converter: controller.animationConverter
    property string pendingTarget

    flat: true
    padding: Theme.panelPadding
    topPadding: Theme.spaceXs
    bottomPadding: Theme.spaceXs
    contentSpacing: Theme.spaceXs
    title: qsTr('R6 ↔ R15 animation converter')
    subtitle: qsTr('Convert local Roblox KeyframeSequence or CurveAnimation files without uploading them.')

    FileDialog {
        id: sourceDialog

        title: qsTr('Open Roblox animation')
        nameFilters: [qsTr('Roblox animations (*.rbxmx *.rbxm)'), qsTr('All files (*)')]
        onAccepted: root.converter.loadSource(selectedFile)
    }

    FileDialog {
        id: destinationDialog

        title: qsTr('Save converted animation')
        fileMode: FileDialog.SaveFile
        defaultSuffix: 'rbxmx'
        nameFilters: [qsTr('Roblox XML animations (*.rbxmx)'), qsTr('All files (*)')]
        onAccepted: root.converter.convert(root.pendingTarget, selectedFile)
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm

        FluentButton {
            text: root.converter.sourceLoaded ? qsTr('Choose another file…') : qsTr('Choose animation…')
            enabled: !root.converter.task.busy
            onClicked: sourceDialog.open()
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXxs

            Label {
                Layout.fillWidth: true
                text: root.converter.sourceLoaded ? root.converter.sourceName : qsTr('No animation loaded')
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideMiddle
            }

            Label {
                Layout.fillWidth: true
                text: root.converter.sourceLoaded ? qsTr('Detected rig: %1').arg(root.converter.detectedRig) : qsTr('Supported input: .rbxm and .rbxmx')
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                elide: Text.ElideMiddle
            }
        }

        StatusPill {
            visible: root.converter.sourceLoaded
            text: root.converter.detectedRig
            status: root.converter.detectedRig === 'unknown' ? 'warning' : 'success'
        }

        IconButton {
            visible: root.converter.sourceLoaded
            text: qsTr('Clear loaded animation')
            iconText: '×'
            enabled: !root.converter.task.busy
            onClicked: root.converter.clearSource()
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: conversionContent.implicitHeight + Theme.spaceSm * 2
        color: Theme.surfaceSubtle
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.border

        RowLayout {
            id: conversionContent

            anchors.fill: parent
            anchors.margins: Theme.spaceSm
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: {
                    if (!root.converter.sourceLoaded)
                        return qsTr('Choose an animation to inspect its rig and enable conversion.');
                    if (root.converter.detectedRig === 'unknown')
                        return qsTr('Conversion is disabled because this is not a recognizable player-rig animation.');
                    return root.converter.detectedRig === 'R6' ? qsTr('This R6 animation can be expanded to an R15 pose hierarchy.') : qsTr('This R15 animation can be mapped to an R6 pose hierarchy.');
                }
                color: root.converter.detectedRig === 'unknown' ? Theme.warning : Theme.textSecondary
                font.pointSize: TypeScale.label
                wrapMode: Text.Wrap
            }

            FluentButton {
                text: qsTr('Convert R15 → R6')
                enabled: root.converter.canConvertToR6 && !root.converter.task.busy
                onClicked: {
                    root.pendingTarget = 'R6';
                    destinationDialog.selectedFile = root.converter.suggestedOutputUrl('R6');
                    destinationDialog.open();
                }
            }

            FluentButton {
                text: qsTr('Convert R6 → R15')
                highlighted: root.converter.canConvertToR15
                enabled: root.converter.canConvertToR15 && !root.converter.task.busy
                onClicked: {
                    root.pendingTarget = 'R15';
                    destinationDialog.selectedFile = root.converter.suggestedOutputUrl('R15');
                    destinationDialog.open();
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.converter.task.busy || root.converter.statusText.length > 0
        spacing: Theme.spaceXs

        BusyIndicator {
            visible: root.converter.task.busy
            running: visible
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            Accessible.name: root.converter.task.message
        }

        Label {
            Layout.fillWidth: true
            text: root.converter.task.busy ? root.converter.task.message : root.converter.statusText
            color: root.converter.detectedRig === 'unknown' ? Theme.warning : Theme.textSecondary
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }
    }
}
