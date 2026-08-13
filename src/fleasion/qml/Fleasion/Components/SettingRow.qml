import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string title: ''
    property string description: ''
    property string iconText: ''
    property bool interactive: false
    default property alias trailing: trailingArea.data

    signal activated

    padding: Theme.spaceXs
    implicitWidth: 420
    implicitHeight: Math.max(Theme.largeControlHeight, implicitContentHeight + topPadding + bottomPadding)
    activeFocusOnTab: interactive
    Accessible.role: interactive ? Accessible.Button : Accessible.Grouping
    Accessible.name: title
    Accessible.description: description
    Keys.onSpacePressed: event => {
        if (root.interactive) {
            root.activated();
            event.accepted = true;
        }
    }
    Keys.onReturnPressed: event => {
        if (root.interactive) {
            root.activated();
            event.accepted = true;
        }
    }

    HoverHandler {
        id: hoverHandler

        enabled: root.enabled && root.interactive
    }

    TapHandler {
        id: tapHandler

        enabled: root.enabled && root.interactive
        onTapped: root.activated()
    }

    contentItem: RowLayout {
        spacing: Theme.spaceSm

        Rectangle {
            visible: root.iconText.length > 0
            color: Theme.accentSubtle
            radius: Theme.radiusMd
            Layout.preferredWidth: 34
            Layout.preferredHeight: 34
            Accessible.ignored: true

            Label {
                anchors.centerIn: parent
                text: root.iconText
                color: Theme.accent
                font.pointSize: TypeScale.body
            }
        }

        ColumnLayout {
            spacing: Theme.spaceXxs
            Layout.fillWidth: true

            Label {
                text: root.title
                color: root.enabled ? Theme.textPrimary : Theme.textDisabled
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            Label {
                visible: root.description.length > 0
                text: root.description
                color: root.enabled ? Theme.textSecondary : Theme.textDisabled
                font.pointSize: TypeScale.label
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        RowLayout {
            id: trailingArea

            spacing: Theme.spaceXs
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
        }
    }

    background: Rectangle {
        color: {
            if (!root.interactive)
                return 'transparent';

            if (tapHandler.pressed)
                return Theme.surfacePressed;

            if (hoverHandler.hovered)
                return Theme.surfaceHover;

            return 'transparent';
        }
        radius: Theme.radiusMd
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
