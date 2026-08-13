pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "../../dialogs" as Dialogs

FluentDialog {
    id: root

    required property var controller
    property string editorRuleKey
    property string deleteRuleKey

    function openEditor(ruleKey) {
        editorRuleKey = ruleKey;
        editorLoader.active = true;
    }

    width: Math.min(920, parent ? parent.width - Theme.spaceXl : 920)
    height: Math.min(680, parent ? parent.height - Theme.spaceXl : 680)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: qsTr("Auto-replace rules")
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXxs

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Transform traffic automatically")
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.title
                    font.weight: TypeScale.semibold
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Rules are persisted and applied from top to bottom to matching requests and responses.")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                    wrapMode: Text.Wrap
                }
            }

            StatusPill {
                text: qsTr("%n rule(s)", "", root.controller.rulesModel.count)
                status: root.controller.rulesModel.count > 0 ? "info" : "neutral"
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 300

            ListView {
                anchors.fill: parent
                clip: true
                spacing: Theme.spaceXs
                model: root.controller.rulesModel
                reuseItems: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                delegate: AutoReplaceRuleDelegate {
                    required property string key

                    width: ListView.view.width
                    ruleKey: key
                    controller: root.controller
                    onEditRequested: ruleKey => root.openEditor(ruleKey)
                    onDuplicateRequested: ruleKey => root.controller.duplicateRule(ruleKey)
                    onDeleteRequested: ruleKey => {
                        root.deleteRuleKey = ruleKey;
                        deleteLoader.active = true;
                    }
                }
            }

            EmptyState {
                anchors.fill: parent
                visible: root.controller.rulesModel.count === 0
                iconText: "↯"
                title: qsTr("No automatic transforms")
                description: qsTr("Add a rule to replace body text, JSON values, query parameters, or headers while traffic passes through the proxy.")
                actionText: qsTr("Add rule")
                onActionTriggered: root.openEditor("")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Label {
                Layout.fillWidth: true
                text: qsTr("Tip: prefix a host or path filter with != to exclude matching traffic.")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }

            FluentButton {
                text: qsTr("Import…")
                onClicked: importDialog.open()
            }

            FluentButton {
                text: qsTr("Export…")
                enabled: root.controller.rulesModel.count > 0
                onClicked: exportDialog.open()
            }

            FluentButton {
                text: qsTr("Close")
                onClicked: root.close()
            }

            FluentButton {
                text: qsTr("Add rule")
                highlighted: true
                onClicked: root.openEditor("")
            }
        }
    }

    FileDialog {
        id: importDialog

        title: qsTr("Import auto-replace rules")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("JSON files (*.json)"), qsTr("All files (*)")]
        onAccepted: root.controller.importRules(String(selectedFile))
    }

    FileDialog {
        id: exportDialog

        title: qsTr("Export auto-replace rules")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [qsTr("JSON files (*.json)")]
        onAccepted: root.controller.exportRules(String(selectedFile))
    }

    Loader {
        id: editorLoader

        active: false
        sourceComponent: Component {
            AutoReplaceRuleEditor {
                controller: root.controller
                ruleKey: root.editorRuleKey
                onClosed: editorLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as AutoReplaceRuleEditor).open();
        }
    }

    Loader {
        id: deleteLoader

        active: false
        sourceComponent: Component {
            Dialogs.ConfirmDialog {
                heading: qsTr("Delete auto-replace rule?")
                message: qsTr("The selected transform will stop applying to proxy traffic.")
                details: qsTr("This action cannot be undone.")
                acceptText: qsTr("Delete rule")
                destructive: true
                onConfirmed: root.controller.deleteRule(root.deleteRuleKey)
                onClosed: deleteLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.ConfirmDialog).open();
        }
    }
}
