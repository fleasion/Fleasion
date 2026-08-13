pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
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
    signal exportRequested

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
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.border
        clip: true

        Loader {
            anchors.fill: parent
            anchors.margins: Theme.spaceSm
            sourceComponent: root.previewUrl.length > 0 ? imagePreview : root.previewKind === "text" && root.available ? textPreview : summaryPreview
        }
    }

    FluentButton {
        Layout.alignment: Qt.AlignRight
        text: qsTr("Export…")
        enabled: root.available
        onClicked: root.exportRequested()
    }

    Component {
        id: imagePreview

        Image {
            source: root.previewUrl
            sourceSize.width: 720
            sourceSize.height: 480
            asynchronous: true
            fillMode: Image.PreserveAspectFit
            Accessible.name: qsTr("%1 preview").arg(root.title)
        }
    }

    Component {
        id: textPreview

        ScrollView {
            contentWidth: availableWidth
            clip: true

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
