pragma ComponentBehavior: Bound

import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property int defaultTimeout: 4500
    property int maximumVisible: 4
    readonly property int count: toastModel.count
    property int nextToastId: 0

    function show(first, second, third, fourth) {
        let title = '';
        let message = '';
        let tone = 'info';
        let duration = root.defaultTimeout;
        const tones = ['info', 'success', 'warning', 'error', 'danger'];
        if (third !== undefined && typeof third === 'string') {
            title = String(first);
            message = String(second);
            tone = third;
            duration = fourth === undefined ? root.defaultTimeout : fourth;
        } else if (typeof second === 'string' && tones.includes(second)) {
            message = String(first);
            tone = second;
            duration = third === undefined ? root.defaultTimeout : third;
        } else if (typeof second === 'string') {
            title = String(first);
            message = second;
            duration = third === undefined ? root.defaultTimeout : third;
        } else {
            message = String(first);
            duration = second === undefined ? root.defaultTimeout : second;
        }
        const normalizedTone = tone === 'danger' ? 'error' : tone;
        if (toastModel.count >= root.maximumVisible)
            toastModel.remove(0);

        toastModel.append({
            "toastId": ++root.nextToastId,
            "title": title,
            "message": message,
            "tone": normalizedTone,
            "timeout": duration
        });
    }

    function success(first, second, duration) {
        if (typeof second === 'string')
            show(first, second, 'success', duration);
        else
            show(first, 'success', second);
    }

    function warning(first, second, duration) {
        if (typeof second === 'string')
            show(first, second, 'warning', duration);
        else
            show(first, 'warning', second);
    }

    function error(first, second, duration) {
        if (typeof second === 'string')
            show(first, second, 'error', duration);
        else
            show(first, 'error', second);
    }

    function dismiss(toastId) {
        for (let index = 0; index < toastModel.count; ++index) {
            if (toastModel.get(index).toastId === toastId) {
                toastModel.remove(index);
                return;
            }
        }
    }

    implicitWidth: 360
    implicitHeight: toastColumn.implicitHeight
    z: 1000

    ListModel {
        id: toastModel
    }

    Column {
        id: toastColumn

        width: root.width
        spacing: Theme.spaceXs

        Repeater {
            model: toastModel

            delegate: Rectangle {
                id: toast

                required property int toastId
                required property string title
                required property string message
                required property string tone
                required property int timeout
                readonly property color toneColor: {
                    if (tone === 'success')
                        return Theme.success;

                    if (tone === 'warning')
                        return Theme.warning;

                    if (tone === 'error')
                        return Theme.danger;

                    return Theme.info;
                }
                readonly property string toneIcon: {
                    if (tone === 'success')
                        return '\u2713';

                    if (tone === 'warning')
                        return '!';

                    if (tone === 'error')
                        return '\u00d7';

                    return 'i';
                }

                width: toastColumn.width
                implicitHeight: toastLayout.implicitHeight + Theme.spaceMd * 2
                color: Theme.surfaceElevated
                radius: Theme.radiusLg
                border.width: 1
                border.color: Theme.borderStrong
                Accessible.role: Accessible.AlertMessage
                Accessible.name: title.length > 0 ? qsTr('%1: %2').arg(title).arg(message) : message

                RowLayout {
                    id: toastLayout

                    anchors.fill: parent
                    anchors.margins: Theme.spaceMd
                    spacing: Theme.spaceSm

                    Rectangle {
                        color: Qt.rgba(toast.toneColor.r, toast.toneColor.g, toast.toneColor.b, 0.16)
                        radius: Theme.radiusPill
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 30
                        Layout.alignment: Qt.AlignTop
                        Accessible.ignored: true

                        Label {
                            anchors.centerIn: parent
                            text: toast.toneIcon
                            color: toast.toneColor
                            font.pointSize: TypeScale.label
                            font.weight: TypeScale.semibold
                        }
                    }

                    ColumnLayout {
                        spacing: Theme.spaceXxs
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignVCenter

                        Label {
                            visible: toast.title.length > 0
                            text: toast.title
                            color: Theme.textPrimary
                            font.pointSize: TypeScale.body
                            font.weight: TypeScale.semibold
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                            Accessible.ignored: true
                        }

                        Label {
                            text: toast.message
                            color: toast.title.length > 0 ? Theme.textSecondary : Theme.textPrimary
                            font.pointSize: toast.title.length > 0 ? TypeScale.label : TypeScale.body
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                            Accessible.ignored: true
                        }
                    }

                    IconButton {
                        text: qsTr('Dismiss notification')
                        iconText: '\u00d7'
                        flat: true
                        controlSize: 32
                        Layout.alignment: Qt.AlignTop
                        onClicked: root.dismiss(toast.toastId)
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: 4
                    color: toast.toneColor
                    radius: 2
                    Accessible.ignored: true
                }

                Timer {
                    interval: Math.max(1000, toast.timeout)
                    running: toast.visible && toast.timeout > 0
                    onTriggered: root.dismiss(toast.toastId)
                }
            }
        }
    }
}
