import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string presetId
    property string name
    property string credit
    property string created
    property string updated
    property string placeId
    property bool hasOriginals
    property bool hasReplacements
    property bool isCustom
    property string thumbnailUrl

    signal sourceRequested(string presetId, string kind)
    signal deleteRequested(string presetId, string name)

    padding: Theme.spaceSm
    implicitWidth: 252
    implicitHeight: 294
    Accessible.role: Accessible.Grouping
    Accessible.name: name
    Accessible.description: credit.length > 0 ? qsTr('Community preset by %1').arg(credit) : qsTr('Community preset')

    HoverHandler {
        id: hover
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceXs

        Rectangle {
            color: Theme.surfaceSubtle
            radius: Theme.radiusMd
            clip: true
            Layout.fillWidth: true
            Layout.preferredHeight: 118
            Accessible.ignored: true

            Label {
                anchors.centerIn: parent
                visible: thumbnail.status !== Image.Ready
                text: root.placeId.length > 0 ? '\u25c8' : '\u2726'
                color: Theme.textTertiary
                font.pointSize: TypeScale.title
            }

            Image {
                id: thumbnail

                anchors.fill: parent
                source: root.thumbnailUrl
                sourceSize.width: 512
                sourceSize.height: 512
                asynchronous: true
                fillMode: Image.PreserveAspectCrop
                visible: status === Image.Ready
                Accessible.ignored: true
            }

            StatusPill {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: Theme.spaceXs
                visible: root.isCustom
                text: qsTr('Custom')
                status: 'info'
            }

            IconButton {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.spaceXxs
                visible: root.isCustom
                text: qsTr('Delete custom preset')
                iconText: '\u00d7'
                danger: true
                controlSize: 34
                onClicked: root.deleteRequested(root.presetId, root.name)
            }
        }

        Label {
            text: root.name
            color: Theme.textPrimary
            font.pointSize: TypeScale.body
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        Label {
            text: {
                if (root.credit.length > 0)
                    return qsTr('By %1').arg(root.credit);

                if (root.placeId.length > 0)
                    return qsTr('Place %1').arg(root.placeId);

                return qsTr('Community contribution');
            }
            color: Theme.textSecondary
            font.pointSize: TypeScale.caption
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        Label {
            visible: root.updated.length > 0 || root.created.length > 0
            text: root.updated.length > 0 ? qsTr('Updated %1').arg(root.updated) : qsTr('Created %1').arg(root.created)
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        Item {
            Layout.fillHeight: true
        }

        RowLayout {
            spacing: Theme.spaceXs
            Layout.fillWidth: true

            FluentButton {
                visible: root.hasOriginals
                text: qsTr('Originals')
                Layout.fillWidth: true
                Accessible.description: qsTr('Browse asset IDs from %1').arg(root.name)
                onClicked: root.sourceRequested(root.presetId, 'originals')
            }

            FluentButton {
                visible: root.hasReplacements
                text: qsTr('Replacements')
                highlighted: !root.hasOriginals
                Layout.fillWidth: true
                Accessible.description: qsTr('Browse replacement values from %1').arg(root.name)
                onClicked: root.sourceRequested(root.presetId, 'replacements')
            }
        }
    }

    background: Rectangle {
        color: hover.hovered ? Theme.surfaceHover : Theme.surface
        radius: Theme.radiusLg
        border.width: 1
        border.color: hover.hovered ? Theme.borderStrong : Theme.border

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
