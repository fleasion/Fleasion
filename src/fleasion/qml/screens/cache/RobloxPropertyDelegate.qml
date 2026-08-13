pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    required property string ownerIdentity
    required property int rowIndex
    required property string propertyName
    required property string propertyTypeName
    required property string propertyValueText
    required property bool editableValue
    property bool editing: false
    property alias draftText: editField.text
    property string activeIdentity: ''
    readonly property string itemIdentity: [ownerIdentity, rowIndex, propertyName, propertyTypeName].join('\u001f')

    implicitHeight: editing ? 70 : 46

    function beginEditing() {
        if (!root.editableValue)
            return;
        root.activeIdentity = root.itemIdentity;
        editField.text = root.propertyValueText;
        root.editing = true;
        editField.forceActiveFocus();
        editField.selectAll();
    }

    function resetEditingState() {
        root.editing = false;
        root.activeIdentity = '';
        editField.text = '';
        editField.focus = false;
    }

    function commitEditing() {
        if (!root.editing || root.activeIdentity !== root.itemIdentity) {
            root.resetEditingState();
            return false;
        }
        const accepted = root.controller.updateProperty(root.rowIndex, editField.text);
        if (accepted)
            root.resetEditingState();
        return accepted;
    }

    onEditableValueChanged: {
        if (!editableValue)
            resetEditingState();
    }
    onItemIdentityChanged: resetEditingState()
    onPropertyValueTextChanged: {
        if (editing)
            resetEditingState();
    }
    ListView.onPooled: resetEditingState()
    ListView.onReused: resetEditingState()

    ColumnLayout {
        anchors.fill: parent
        spacing: 2

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    text: root.propertyName
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.label
                    font.weight: TypeScale.medium
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: root.propertyTypeName
                    color: Theme.textTertiary
                    font.pointSize: TypeScale.caption
                    elide: Text.ElideRight
                }
            }

            Label {
                Layout.fillWidth: true
                visible: !root.editing
                text: root.propertyValueText
                color: root.editableValue ? Theme.textSecondary : Theme.textTertiary
                font.family: 'monospace'
                font.pointSize: TypeScale.caption
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideMiddle
                ToolTip.visible: valueHover.hovered && root.propertyValueText.length > 28
                ToolTip.text: root.propertyValueText

                HoverHandler {
                    id: valueHover
                }
            }

            FluentButton {
                text: qsTr('Edit')
                compact: true
                flat: true
                visible: root.editableValue && !root.editing
                onClicked: root.beginEditing()
            }

            FluentButton {
                text: qsTr('Remove')
                compact: true
                flat: true
                visible: !root.editing
                onClicked: root.controller.removeProperty(root.rowIndex)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.editing
            spacing: Theme.spaceXs

            FluentTextField {
                id: editField

                Layout.fillWidth: true
                Accessible.name: qsTr('Edit %1').arg(root.propertyName)
                Keys.onEscapePressed: event => {
                    root.resetEditingState();
                    event.accepted = true;
                }
                onAccepted: root.commitEditing()
            }

            FluentButton {
                text: qsTr('Save')
                compact: true
                highlighted: true
                onClicked: root.commitEditing()
            }

            FluentButton {
                text: qsTr('Cancel')
                compact: true
                flat: true
                onClicked: root.resetEditingState()
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
    }
}
