import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AbstractButton {
    id: root

    property bool fillWidth: false
    property real preferredWidth: 120
    property bool sortable: false
    property string sortDirection: 'none'

    signal sortRequested

    Layout.fillWidth: fillWidth
    Layout.preferredWidth: preferredWidth
    Layout.minimumWidth: Math.min(preferredWidth, 72)
    implicitHeight: 28
    padding: Theme.spaceXs
    enabled: sortable
    hoverEnabled: sortable
    activeFocusOnTab: sortable
    Accessible.name: text
    Accessible.description: {
        if (!root.sortable)
            return '';

        if (root.sortDirection === 'ascending')
            return qsTr('Sorted ascending');

        if (root.sortDirection === 'descending')
            return qsTr('Sorted descending');

        return qsTr('Not sorted');
    }
    onClicked: sortRequested()

    contentItem: RowLayout {
        spacing: Theme.spaceXxs

        Label {
            text: root.text
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            Layout.fillWidth: true
            Accessible.ignored: true
        }

        Label {
            visible: root.sortable && root.sortDirection !== 'none'
            text: root.sortDirection === 'ascending' ? '\u2191' : '\u2193'
            color: Theme.accent
            font.pointSize: TypeScale.label
            Accessible.ignored: true
        }
    }

    background: Rectangle {
        color: {
            if (!root.sortable)
                return 'transparent';

            if (root.down)
                return Theme.surfacePressed;

            if (root.hovered)
                return Theme.surfaceHover;

            return 'transparent';
        }
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 0
        border.color: Theme.focusRing
    }
}
