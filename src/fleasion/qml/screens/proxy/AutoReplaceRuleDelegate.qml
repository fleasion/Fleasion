import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property string ruleKey
    required property bool ruleEnabled
    required property string directionLabel
    required property string typeLabel
    required property string matchText
    required property string replacement
    required property string hostFilter
    required property string pathFilter
    property var controller
    signal editRequested(string ruleKey)
    signal duplicateRequested(string ruleKey)
    signal deleteRequested(string ruleKey)

    implicitHeight: 100
    color: pointer.hovered ? Theme.surfaceHover : Theme.surfaceSubtle
    radius: Theme.radiusMd
    border.width: 1
    border.color: Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceSm
        spacing: Theme.spaceSm

        FluentSwitch {
            id: enabledSwitch

            Accessible.name: qsTr("Enable auto-replace rule")
            onToggled: {
                if (root.controller && checked !== root.ruleEnabled)
                    root.controller.setRuleEnabled(root.ruleKey, checked);
            }
        }

        Binding {
            target: enabledSwitch
            property: "checked"
            value: root.ruleEnabled
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXxs

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                StatusPill {
                    text: root.typeLabel
                    status: root.ruleEnabled ? "info" : "neutral"
                }

                Label {
                    text: root.directionLabel
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.caption
                }

                Label {
                    Layout.fillWidth: true
                    visible: root.hostFilter.length > 0 || root.pathFilter.length > 0
                    text: qsTr("Filters: %1 %2").arg(root.hostFilter || qsTr("any host")).arg(root.pathFilter || qsTr("any path"))
                    color: Theme.textTertiary
                    font.pointSize: TypeScale.caption
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideMiddle
                }
            }

            Label {
                Layout.fillWidth: true
                text: root.replacement.length > 0 ? qsTr("%1  →  %2").arg(root.matchText).arg(root.replacement) : qsTr("Match %1 and replace with an empty value").arg(root.matchText)
                color: root.ruleEnabled ? Theme.textPrimary : Theme.textDisabled
                font.family: "monospace"
                font.pointSize: TypeScale.body
                elide: Text.ElideMiddle
            }
        }

        IconButton {
            iconText: "✎"
            text: qsTr("Edit rule")
            onClicked: root.editRequested(root.ruleKey)
        }

        IconButton {
            iconText: "⧉"
            text: qsTr("Duplicate rule")
            onClicked: root.duplicateRequested(root.ruleKey)
        }

        IconButton {
            iconText: "×"
            text: qsTr("Delete rule")
            onClicked: root.deleteRequested(root.ruleKey)
        }
    }

    HoverHandler {
        id: pointer
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onDoubleTapped: root.editRequested(root.ruleKey)
    }
}
