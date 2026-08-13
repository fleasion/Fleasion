import QtQuick

RepairDialogFrame {
    statusLabel: controller.code === "linux_hosts_read_only" ? qsTr("Read-only hosts") : qsTr("Hosts blocked")
    status: "warning"
}
