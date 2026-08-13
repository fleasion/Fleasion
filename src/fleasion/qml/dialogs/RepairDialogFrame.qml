pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    required property var appController
    property string statusLabel: qsTr("Startup issue")
    property string status: "warning"
    property bool showSnippet: controller.snippet.length > 0
    property bool showSupplemental: controller.supplementalText.length > 0
    signal actionRequested(string actionId, string label, bool requiresConfirmation, string confirmationTitle, string confirmationText)

    width: Math.min(660, parent ? parent.width - Theme.spaceXxl : 660)
    height: Math.min(650, parent ? parent.height - Theme.spaceXxl : 650)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: controller.title
    standardButtons: Dialog.NoButton
    onRejected: controller.dismiss()

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            StatusPill {
                text: root.statusLabel
                status: root.status
            }

            Label {
                Layout.fillWidth: true
                text: root.controller.summary
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.semibold
                wrapMode: Text.Wrap
            }
        }

        ScrollView {
            id: scrollView

            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: scrollView.availableWidth
                spacing: Theme.spaceSm

                Label {
                    Layout.fillWidth: true
                    text: root.controller.guidance
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                    wrapMode: Text.Wrap
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spaceXxs
                    implicitHeight: diagnosticColumn.implicitHeight + Theme.spaceMd
                    visible: root.controller.diagnostics.count > 0
                    radius: Theme.radiusSm
                    color: Theme.surfaceSubtle
                    border.width: 1
                    border.color: Theme.border

                    ColumnLayout {
                        id: diagnosticColumn

                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Theme.spaceSm
                        anchors.rightMargin: Theme.spaceSm
                        spacing: Theme.spaceXxs

                        Repeater {
                            model: root.controller.diagnostics

                            delegate: RowLayout {
                                id: diagnosticRow

                                required property string label
                                required property string value
                                required property bool copyable

                                Layout.fillWidth: true
                                spacing: Theme.spaceSm

                                Label {
                                    Layout.preferredWidth: 124
                                    Layout.alignment: Qt.AlignTop
                                    text: diagnosticRow.label
                                    color: Theme.textTertiary
                                    font.pointSize: TypeScale.caption
                                    elide: Text.ElideRight
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: diagnosticRow.value
                                    color: Theme.textSecondary
                                    font.pointSize: TypeScale.caption
                                    wrapMode: Text.WrapAnywhere
                                    textFormat: Text.PlainText
                                }

                                FluentButton {
                                    visible: diagnosticRow.copyable && diagnosticRow.value.length > 0
                                    text: qsTr("Copy")
                                    compact: true
                                    flat: true
                                    Accessible.description: qsTr("Copy %1").arg(diagnosticRow.label)
                                    onClicked: root.appController.copyText(diagnosticRow.value)
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: root.showSupplemental
                    spacing: Theme.spaceXs

                    Label {
                        Layout.fillWidth: true
                        text: root.controller.supplementalTitle
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.label
                        font.weight: TypeScale.semibold
                        wrapMode: Text.Wrap
                    }

                    Label {
                        Layout.fillWidth: true
                        text: root.controller.supplementalText
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.caption
                        wrapMode: Text.Wrap
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: root.showSnippet
                    implicitHeight: Math.min(190, Math.max(112, snippetArea.contentHeight + Theme.spaceMd))
                    radius: Theme.radiusSm
                    color: Theme.surfaceSubtle
                    border.width: 1
                    border.color: Theme.border

                    FluentTextArea {
                        id: snippetArea

                        anchors.fill: parent
                        anchors.margins: Theme.spaceXs
                        text: root.controller.snippet
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.NoWrap
                        font.family: "monospace"
                        Accessible.name: qsTr("Repair configuration snippet")
                    }

                    FluentButton {
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.spaceXs
                        text: qsTr("Copy snippet")
                        compact: true
                        highlighted: true
                        onClicked: root.appController.copyText(root.controller.snippet)
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.controller.task.busy
            spacing: Theme.spaceXs

            BusyIndicator {
                running: root.controller.task.busy
                implicitWidth: 22
                implicitHeight: 22
            }

            Label {
                Layout.fillWidth: true
                text: root.controller.task.message
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
            }
        }

        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            spacing: Theme.spaceXs

            Repeater {
                model: root.controller.actions

                delegate: FluentButton {
                    id: actionButton

                    required property string actionId
                    required property string label
                    required property string style
                    required property bool requiresConfirmation
                    required property string confirmationTitle
                    required property string confirmationText

                    text: actionButton.label
                    compact: true
                    highlighted: actionButton.style === "primary"
                    danger: actionButton.style === "danger"
                    enabled: !root.controller.task.busy
                    onClicked: root.actionRequested(actionButton.actionId, actionButton.label, actionButton.requiresConfirmation, actionButton.confirmationTitle, actionButton.confirmationText)
                }
            }

            FluentButton {
                text: qsTr("Not now")
                compact: true
                flat: true
                enabled: !root.controller.task.busy
                onClicked: root.controller.dismiss()
            }
        }
    }
}
