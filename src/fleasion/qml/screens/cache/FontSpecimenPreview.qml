import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root

    required property var controller
    property int specimenSize: 28
    property string specimenText: qsTr('The quick brown fox jumps over the lazy dog.\nABCDEFGHIJKLMNOPQRSTUVWXYZ · 0123456789')

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceXs
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    text: root.controller.selectedFamily || qsTr('Font specimen')
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.subtitle
                    font.weight: TypeScale.semibold
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: root.controller.formatName
                    color: Theme.textTertiary
                    font.pointSize: TypeScale.caption
                    elide: Text.ElideRight
                }
            }

            FluentComboBox {
                Layout.preferredWidth: 150
                visible: root.controller.families.length > 1
                model: root.controller.families
                Accessible.name: qsTr('Font family')
                onActivated: root.controller.selectedFamily = currentText
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            Label {
                text: qsTr('Size')
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
            }

            FluentSlider {
                Layout.fillWidth: true
                from: 8
                to: 72
                stepSize: 1
                value: root.specimenSize
                Accessible.name: qsTr('Specimen size')
                onMoved: root.specimenSize = Math.round(value)
            }

            Label {
                Layout.preferredWidth: 42
                text: qsTr('%1 pt').arg(root.specimenSize)
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                horizontalAlignment: Text.AlignRight
            }
        }

        FluentTextArea {
            Layout.fillWidth: true
            Layout.fillHeight: true
            text: root.specimenText
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            color: Theme.textPrimary
            font.family: root.controller.selectedFamily
            font.pointSize: root.specimenSize
            Accessible.name: qsTr('Editable font specimen')
            Accessible.description: qsTr('Type any text to preview this cached font')
            onTextChanged: root.specimenText = text
        }
    }
}
