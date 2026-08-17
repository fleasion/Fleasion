import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller

    function submit() {
        const started = root.controller.importCustom(catalogSource.text, nameField.text, placeField.text, originalsSource.text, replacementsSource.text, creditField.text);
        if (started)
            root.accept();
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent ? parent.width - Theme.spaceXxl : 620)
    height: Math.min(690, parent ? parent.height - Theme.spaceXxl : 690)
    modal: true
    focus: true
    title: qsTr('Import custom community preset')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape
    onOpened: {
        catalogSource.text = '';
        nameField.clear();
        placeField.clear();
        originalsSource.text = '';
        replacementsSource.text = '';
        creditField.clear();
        catalogSource.forceActiveFocus();
    }

    footer: DialogActionBar {
        acceptText: qsTr('Import')
        acceptEnabled: !root.controller.task.busy
        onCancelRequested: root.reject()
        onAcceptRequested: root.submit()
    }

    contentItem: FluentScrollView {
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: Theme.spaceSm

            Label {
                text: qsTr('Import a complete CLOG-compatible definition')
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.semibold
                Layout.fillWidth: true
            }

            Label {
                text: qsTr('Choose a JSON file or URL containing a games object. Leave this blank to describe one preset manually below.')
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            FileDropField {
                id: catalogSource

                accessibleName: qsTr('Preset definition source')
                placeholderText: qsTr('CLOG-compatible JSON URL or file')
                dialogTitle: qsTr('Choose preset definition')
                nameFilters: [qsTr('JSON files (*.json)'), qsTr('All files (*)')]
                Layout.fillWidth: true
            }

            Rectangle {
                color: Theme.border
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                Layout.topMargin: Theme.spaceXs
                Layout.bottomMargin: Theme.spaceXs
                Accessible.ignored: true
            }

            Label {
                text: qsTr('Or describe one preset')
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.semibold
                Layout.fillWidth: true
            }

            RowLayout {
                spacing: Theme.spaceSm
                Layout.fillWidth: true

                ColumnLayout {
                    spacing: Theme.spaceXxs
                    Layout.fillWidth: true

                    Label {
                        text: qsTr('Name')
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.label
                    }

                    FluentTextField {
                        id: nameField

                        placeholderText: qsTr('My game preset')
                        Accessible.name: qsTr('Preset name')
                        Layout.fillWidth: true
                    }
                }

                ColumnLayout {
                    spacing: Theme.spaceXxs
                    Layout.preferredWidth: 180

                    Label {
                        text: qsTr('Place ID')
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.label
                    }

                    FluentTextField {
                        id: placeField

                        placeholderText: qsTr('Optional')
                        inputMethodHints: Qt.ImhDigitsOnly
                        Accessible.name: qsTr('Roblox place ID')
                        Layout.fillWidth: true

                        validator: RegularExpressionValidator {
                            regularExpression: /[0-9]*/
                        }
                    }
                }
            }

            Label {
                text: qsTr('Original asset IDs')
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                Layout.fillWidth: true
            }

            FileDropField {
                id: originalsSource

                accessibleName: qsTr('Original asset JSON source')
                placeholderText: qsTr('Originals JSON URL or file')
                dialogTitle: qsTr('Choose originals JSON')
                nameFilters: [qsTr('JSON files (*.json)'), qsTr('All files (*)')]
                Layout.fillWidth: true
            }

            Label {
                text: qsTr('Replacement values')
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                Layout.fillWidth: true
            }

            FileDropField {
                id: replacementsSource

                accessibleName: qsTr('Replacement JSON source')
                placeholderText: qsTr('Replacements JSON URL or file')
                dialogTitle: qsTr('Choose replacements JSON')
                nameFilters: [qsTr('JSON files (*.json)'), qsTr('All files (*)')]
                Layout.fillWidth: true
            }

            Label {
                text: qsTr('Credit')
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                Layout.fillWidth: true
            }

            FluentTextField {
                id: creditField

                placeholderText: qsTr('Optional contributor name')
                Accessible.name: qsTr('Preset credit')
                Layout.fillWidth: true
            }

            Rectangle {
                color: Theme.infoSubtle
                radius: Theme.radiusMd
                implicitHeight: safetyText.implicitHeight + Theme.spaceMd * 2
                Layout.fillWidth: true

                Label {
                    id: safetyText

                    anchors.fill: parent
                    anchors.margins: Theme.spaceMd
                    text: qsTr('Imported files are validated as JSON, copied into Fleasion storage with unique names, and limited to 32 MiB each.')
                    color: Theme.info
                    font.pointSize: TypeScale.caption
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
