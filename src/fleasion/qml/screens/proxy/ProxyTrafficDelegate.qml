import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property string entryKey
    required property string timeText
    required property string method
    required property string host
    required property string pathText
    required property string statusText
    required property string sizeText
    required property string durationText
    required property bool pending
    required property bool dropped
    required property bool intercepted
    required property bool archived
    signal activated(string entryKey)

    implicitHeight: 54
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: root.archived ? qsTr("Preserved %1 request to %2, %3").arg(root.method).arg(root.host).arg(root.statusText) : qsTr("%1 request to %2, %3").arg(root.method).arg(root.host).arg(root.statusText)
    color: root.pending ? Theme.warningSubtle : pointer.hovered || activeFocus ? Theme.surfaceHover : "transparent"
    radius: Theme.radiusMd
    border.width: activeFocus ? 2 : root.pending ? 1 : 0
    border.color: activeFocus ? Theme.focusRing : Theme.warning

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceSm
        spacing: Theme.spaceSm

        Label {
            Layout.preferredWidth: 56
            text: root.timeText
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
        }

        Label {
            Layout.preferredWidth: 54
            text: root.method
            color: root.archived ? Theme.textTertiary : root.pending ? Theme.warning : Theme.accent
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.semibold
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Label {
                Layout.fillWidth: true
                text: root.host
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: root.pathText
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                elide: Text.ElideMiddle
            }
        }

        StatusPill {
            Layout.preferredWidth: 106
            text: root.statusText
            status: root.pending ? "warning" : root.dropped ? "danger" : root.statusText.indexOf("2") === 0 ? "success" : root.statusText.indexOf("4") === 0 || root.statusText.indexOf("5") === 0 ? "warning" : root.intercepted ? "info" : "neutral"
        }

        Label {
            Layout.preferredWidth: 70
            visible: root.width > 680
            text: root.sizeText
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            horizontalAlignment: Text.AlignRight
        }

        Label {
            Layout.preferredWidth: 62
            visible: root.width > 760
            text: root.durationText
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            horizontalAlignment: Text.AlignRight
        }
    }

    HoverHandler {
        id: pointer
    }

    TapHandler {
        onTapped: root.activated(root.entryKey)
    }

    Keys.onReturnPressed: event => {
        root.activated(root.entryKey);
        event.accepted = true;
    }
    Keys.onSpacePressed: event => {
        root.activated(root.entryKey);
        event.accepted = true;
    }
}
