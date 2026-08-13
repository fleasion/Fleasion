import QtQuick
import QtQuick.Dialogs

FileDialog {
    id: root

    required property var controller
    property bool exporting: false
    signal finished

    title: exporting ? qsTr("Export replacement profile") : qsTr("Import replacement profile")
    fileMode: exporting ? FileDialog.SaveFile : FileDialog.OpenFile
    nameFilters: [qsTr("JSON profiles (*.json)"), qsTr("All files (*)")]
    defaultSuffix: "json"

    onAccepted: {
        if (exporting)
            controller.exportConfig(controller.activeConfig, selectedFile.toString());
        else
            controller.importConfig(selectedFile.toString());
        finished();
    }
    onRejected: finished()
}
