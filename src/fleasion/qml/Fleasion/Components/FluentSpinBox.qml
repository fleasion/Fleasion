import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.SpinBox {
    id: root

    implicitWidth: 120
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceSm
    rightPadding: 64
    activeFocusOnTab: true

    contentItem: TextInput {
        id: numberInput

        text: String(root.value)
        validator: root.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        readOnly: !root.editable
        selectByMouse: true
        color: root.enabled ? Theme.textPrimary : Theme.textDisabled
        selectionColor: Theme.accent
        selectedTextColor: Theme.accentForeground
        font.pointSize: TypeScale.body
        verticalAlignment: TextInput.AlignVCenter
        onEditingFinished: {
            const parsed = Number(text);
            if (Number.isFinite(parsed))
                root.value = Math.round(parsed);
            text = String(root.value);
        }

        Connections {
            target: root

            function onValueChanged() {
                if (!numberInput.activeFocus)
                    numberInput.text = String(root.value);
            }
        }
    }

    up.indicator: Rectangle {
        x: root.width - width
        y: 0
        implicitWidth: 32
        implicitHeight: root.height
        color: root.up.pressed ? Theme.surfacePressed : 'transparent'
        radius: Theme.radiusSm

        Text {
            anchors.fill: parent
            text: '+'
            color: root.enabled ? Theme.textPrimary : Theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    down.indicator: Rectangle {
        x: root.width - width * 2
        y: 0
        implicitWidth: 32
        implicitHeight: root.height
        color: root.down.pressed ? Theme.surfacePressed : 'transparent'
        radius: Theme.radiusSm

        Text {
            anchors.fill: parent
            text: '\u2212'
            color: root.enabled ? Theme.textPrimary : Theme.textDisabled
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    background: Rectangle {
        color: Theme.surfaceElevated
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.borderStrong
    }
}
