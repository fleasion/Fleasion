pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    signal editRequested(string entryId)
    signal inspectRequested(string name, string targetPath)
    signal resetRequested(string entryId)

    spacing: Theme.spaceXs

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Custom file mappings")
        subtitle: qsTr("Map any local file to a safe relative path inside Roblox's resources.")
        actionText: qsTr("Add custom mapping")
        onActionTriggered: root.editRequested("")
    }

    DataTableHeader {
        Layout.fillWidth: true
        visible: root.controller.customModel.count > 0

        DataTableHeaderCell {
            preferredWidth: 120
            text: qsTr("Status")
        }
        DataTableHeaderCell {
            fillWidth: true
            text: qsTr("File mapping")
        }
        DataTableHeaderCell {
            preferredWidth: Theme.controlHeight * 3 + Theme.spaceXs * 2
            text: qsTr("Actions")
        }
    }

    Repeater {
        model: root.controller.customModel

        delegate: ModificationEntryDelegate {
            required property var model

            Layout.fillWidth: true
            entryId: model.entryId
            entryName: model.name
            targetPath: model.targetPath
            sourceType: model.sourceType
            sourceValue: model.sourceValue
            sourceName: model.sourceName
            statusText: model.status
            errorMessage: model.errorMessage
            onReplaceRequested: entryId => root.editRequested(entryId)
            onInspectRequested: (name, targetPath) => root.inspectRequested(name, targetPath)
            onResetRequested: entryId => root.resetRequested(entryId)
        }
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.preferredHeight: 142
        visible: root.controller.customModel.count === 0
        iconText: "✦"
        title: qsTr("No custom mappings")
        description: qsTr("The built-in catalog covers common files. Add a custom mapping for another Roblox resource path.")
        actionText: qsTr("Add custom mapping")
        onActionTriggered: root.editRequested("")
    }
}
