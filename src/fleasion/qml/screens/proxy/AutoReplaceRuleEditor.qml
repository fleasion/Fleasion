import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    property string ruleKey
    readonly property bool editing: ruleKey.length > 0
    readonly property var directionValues: ["both", "request", "response"]
    readonly property var typeValues: ["plain", "regex", "json_path", "query_param", "header"]
    signal saved

    function matchPlaceholder() {
        switch (typeValues[typeCombo.currentIndex]) {
        case "json_path":
            return qsTr("assets[0].id");
        case "query_param":
            return qsTr("Query parameter name");
        case "header":
            return qsTr("Header name");
        case "regex":
            return qsTr("Regular expression");
        default:
            return qsTr("Text to find");
        }
    }

    function loadRule() {
        const values = editing ? controller.rule(ruleKey) : {};
        enabledSwitch.checked = values.enabled === undefined ? true : Boolean(values.enabled);
        directionCombo.currentIndex = Math.max(0, directionValues.indexOf(String(values.direction || "both")));
        typeCombo.currentIndex = Math.max(0, typeValues.indexOf(String(values.ruleType || "plain")));
        matchField.text = String(values.matchText || "");
        replacementField.text = String(values.replacement || "");
        hostField.text = String(values.hostFilter || "");
        pathField.text = String(values.pathFilter || "");
    }

    function saveRule() {
        const ruleType = typeValues[typeCombo.currentIndex];
        const direction = ruleType === "query_param" ? "request" : directionValues[directionCombo.currentIndex];
        const savedRule = editing ? controller.updateRule(ruleKey, enabledSwitch.checked, direction, ruleType, matchField.text, replacementField.text, hostField.text, pathField.text) : controller.addRule(enabledSwitch.checked, direction, ruleType, matchField.text, replacementField.text, hostField.text, pathField.text);
        if (savedRule) {
            saved();
            accept();
        }
    }

    width: Math.min(680, parent ? parent.width - Theme.spaceXl : 680)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: editing ? qsTr("Edit auto-replace rule") : qsTr("Add auto-replace rule")
    standardButtons: Dialog.NoButton
    onOpened: loadRule()

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("Rules run in order against matching proxy traffic. Empty host and path filters apply everywhere.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentSwitch {
                id: enabledSwitch

                text: qsTr("Enabled")
            }

            Item {
                Layout.fillWidth: true
            }

            Label {
                text: qsTr("Direction")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentComboBox {
                id: directionCombo

                model: [qsTr("Both"), qsTr("Request"), qsTr("Response")]
                enabled: root.typeValues[typeCombo.currentIndex] !== "query_param"
                Accessible.name: qsTr("Rule direction")
            }

            Label {
                text: qsTr("Type")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentComboBox {
                id: typeCombo

                model: [qsTr("Plain text"), qsTr("Regular expression"), qsTr("JSON path"), qsTr("Query parameter"), qsTr("Header")]
                Accessible.name: qsTr("Rule type")
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.spaceSm
            rowSpacing: Theme.spaceSm

            Label {
                text: root.typeValues[typeCombo.currentIndex] === "json_path" ? qsTr("JSON path") : root.typeValues[typeCombo.currentIndex] === "query_param" ? qsTr("Parameter") : root.typeValues[typeCombo.currentIndex] === "header" ? qsTr("Header") : qsTr("Match")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentTextField {
                id: matchField

                Layout.fillWidth: true
                placeholderText: root.matchPlaceholder()
                selectByMouse: true
                Accessible.name: qsTr("Rule match value")
            }

            Label {
                text: qsTr("Replacement")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentTextField {
                id: replacementField

                Layout.fillWidth: true
                placeholderText: qsTr("Leave empty to remove the matched value")
                selectByMouse: true
                Accessible.name: qsTr("Rule replacement value")
            }

            Label {
                text: qsTr("Host filter")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentTextField {
                id: hostField

                Layout.fillWidth: true
                placeholderText: qsTr("Optional substring or !=substring")
                selectByMouse: true
                Accessible.name: qsTr("Host filter")
            }

            Label {
                text: qsTr("Path filter")
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentTextField {
                id: pathField

                Layout.fillWidth: true
                placeholderText: qsTr("Optional substring or !=substring")
                selectByMouse: true
                Accessible.name: qsTr("Path filter")
                onAccepted: saveButton.clicked()
            }
        }

        Label {
            Layout.fillWidth: true
            visible: matchField.text.trim().length === 0
            text: qsTr("A match value is required. Filters may be left empty.")
            color: Theme.warning
            font.pointSize: TypeScale.caption
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.reject()
            }

            FluentButton {
                id: saveButton

                text: root.editing ? qsTr("Save rule") : qsTr("Add rule")
                enabled: matchField.text.trim().length > 0
                highlighted: true
                onClicked: root.saveRule()
            }
        }
    }
}
