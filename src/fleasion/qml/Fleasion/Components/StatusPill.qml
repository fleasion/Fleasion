import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Control {
    id: root

    property string text: ''
    property string status: 'neutral'
    readonly property color statusColor: {
        if (status === 'success')
            return Theme.success;

        if (status === 'warning')
            return Theme.warning;

        if (status === 'error' || status === 'danger')
            return Theme.danger;

        if (status === 'info')
            return Theme.info;

        return Theme.textSecondary;
    }
    readonly property color statusBackground: {
        if (status === 'success')
            return Theme.successSubtle;

        if (status === 'warning')
            return Theme.warningSubtle;

        if (status === 'error' || status === 'danger')
            return Theme.dangerSubtle;

        if (status === 'info')
            return Theme.infoSubtle;

        return Theme.surfacePressed;
    }
    readonly property string statusIcon: {
        if (status === 'success')
            return '\u2713';

        if (status === 'warning')
            return '!';

        if (status === 'error' || status === 'danger')
            return '\u00d7';

        if (status === 'info')
            return 'i';

        return '\u2022';
    }

    leftPadding: Theme.spaceXs
    rightPadding: Theme.spaceXs
    topPadding: Theme.spaceXxs
    bottomPadding: Theme.spaceXxs
    implicitHeight: 26
    Accessible.role: Accessible.StaticText
    Accessible.name: text

    contentItem: RowLayout {
        spacing: 5

        Label {
            text: root.statusIcon
            color: root.statusColor
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.semibold
            horizontalAlignment: Text.AlignHCenter
            Layout.preferredWidth: 13
            Accessible.ignored: true
        }

        Label {
            text: root.text
            color: root.statusColor
            font.pointSize: TypeScale.caption
            font.weight: TypeScale.medium
            elide: Text.ElideRight
            Accessible.ignored: true
        }
    }

    background: Rectangle {
        color: root.statusBackground
        radius: Theme.radiusPill
        border.width: 1
        border.color: Qt.rgba(root.statusColor.r, root.statusColor.g, root.statusColor.b, 0.35)
    }
}
