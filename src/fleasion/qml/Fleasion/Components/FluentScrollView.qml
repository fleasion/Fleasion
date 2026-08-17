import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.ScrollView {
    id: root

    property bool horizontalScrollBarEnabled: true
    property bool verticalScrollBarEnabled: true
    property alias horizontalScrollBar: horizontalBar
    property alias verticalScrollBar: verticalBar

    clip: true

    Controls.ScrollBar.horizontal: FluentScrollBar {
        id: horizontalBar

        parent: root
        x: root.leftPadding
        y: root.height - height
        width: root.availableWidth
        accessibleName: qsTr('Horizontal content scroll bar')
        policy: root.horizontalScrollBarEnabled ? Controls.ScrollBar.AsNeeded : Controls.ScrollBar.AlwaysOff
        active: verticalBar.active
    }

    Controls.ScrollBar.vertical: FluentScrollBar {
        id: verticalBar

        parent: root
        x: root.mirrored ? 0 : root.width - width
        y: root.topPadding
        height: root.availableHeight
        accessibleName: qsTr('Vertical content scroll bar')
        policy: root.verticalScrollBarEnabled ? Controls.ScrollBar.AsNeeded : Controls.ScrollBar.AlwaysOff
        active: horizontalBar.active
    }

    background: Item {
        Accessible.ignored: true
    }
}
