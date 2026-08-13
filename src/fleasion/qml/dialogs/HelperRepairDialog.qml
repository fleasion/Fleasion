import QtQuick

RepairDialogFrame {
    statusLabel: controller.code.startsWith("macos") ? qsTr("macOS helper") : qsTr("Linux helper")
    status: "warning"
}
