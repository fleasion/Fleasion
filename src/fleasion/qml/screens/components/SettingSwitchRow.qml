import QtQuick
import Fleasion.Components

Item {
    id: root

    property string title
    property string description
    property bool checked
    property bool available: true
    signal toggled(bool checked)

    implicitWidth: 420
    implicitHeight: settingRow.implicitHeight

    SettingRow {
        id: settingRow

        anchors.fill: parent
        title: root.title
        description: root.description
        enabled: root.available

        FluentSwitch {
            id: toggle

            enabled: root.available
            checked: root.checked
            Accessible.name: root.title
            onToggled: root.toggled(checked)
        }
    }
}
