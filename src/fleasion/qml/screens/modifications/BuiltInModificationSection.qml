pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    property var sectionModel
    property string title
    property string subtitle
    property bool expanded: true
    property string primaryActionText
    property bool addHeadVariant: false
    property var headVariantModel

    signal sourceRequested(string catalogKey, string entryName, string targetPath, string fileFilter)
    signal inspectRequested(string entryName, string targetPath)
    signal muteRequested(string catalogKey)
    signal resetRequested(string catalogKey)
    signal removeRequested(string catalogKey)
    signal primaryActionRequested
    signal headVariantRequested(string catalogKey)

    spacing: 2

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: headerRow.implicitHeight + Theme.spaceSm
        color: headerPointer.hovered ? Theme.surfaceHover : Theme.surfaceSubtle
        radius: 0
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: root.expanded ? qsTr("Collapse %1").arg(root.title) : qsTr("Expand %1").arg(root.title)

        RowLayout {
            id: headerRow

            anchors.fill: parent
            anchors.leftMargin: Theme.spaceSm
            anchors.rightMargin: Theme.spaceXs
            spacing: Theme.spaceSm

            Label {
                text: root.expanded ? "⌄" : "›"
                color: Theme.textSecondary
                font.pointSize: TypeScale.subtitle
                Accessible.ignored: true
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Label {
                    Layout.fillWidth: true
                    text: root.title
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.subtitle
                    font.weight: TypeScale.semibold
                }

                Label {
                    Layout.fillWidth: true
                    visible: root.subtitle.length > 0
                    text: root.subtitle
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.label
                    elide: Text.ElideRight
                }
            }

            StatusPill {
                text: root.sectionModel ? qsTr("%n item(s)", "", root.sectionModel.count) : qsTr("0 items")
                status: "neutral"
            }

            FluentComboBox {
                id: variantPicker

                visible: root.addHeadVariant && root.headVariantModel && root.headVariantModel.count > 0
                Layout.preferredWidth: 156
                model: root.headVariantModel
                textRole: "name"
                Accessible.name: qsTr("Available head variant")
            }

            FluentButton {
                visible: variantPicker.visible
                text: qsTr("Add head")
                compact: true
                onClicked: {
                    const row = root.headVariantModel.get(variantPicker.currentIndex);
                    if (row.catalogKey)
                        root.headVariantRequested(row.catalogKey);
                }
            }

            FluentButton {
                visible: root.primaryActionText.length > 0
                text: root.primaryActionText
                compact: true
                onClicked: root.primaryActionRequested()
            }
        }

        TapHandler {
            onTapped: (eventPoint, button) => {
                if (button === Qt.LeftButton)
                    root.expanded = !root.expanded;
            }
        }
        HoverHandler {
            id: headerPointer
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.border
            Accessible.ignored: true
        }
        Keys.onReturnPressed: event => {
            root.expanded = !root.expanded;
            event.accepted = true;
        }
        Keys.onSpacePressed: event => {
            root.expanded = !root.expanded;
            event.accepted = true;
        }
    }

    Repeater {
        model: root.expanded ? root.sectionModel : null

        delegate: BuiltInModificationDelegate {
            required property var model

            Layout.fillWidth: true
            catalogKey: model.catalogKey
            entryName: model.name
            targetPath: model.targetPath
            fileFilter: model.fileFilter
            muteAvailable: model.muteAvailable
            supported: model.supported
            limitation: model.limitation
            configured: model.configured
            sourceName: model.sourceName
            statusText: model.status
            errorMessage: model.errorMessage
            optional: model.optional
            onEditRequested: (catalogKey, entryName, targetPath, fileFilter) => root.sourceRequested(catalogKey, entryName, targetPath, fileFilter)
            onInspectRequested: (entryName, targetPath) => root.inspectRequested(entryName, targetPath)
            onMuteRequested: catalogKey => root.muteRequested(catalogKey)
            onResetRequested: catalogKey => root.resetRequested(catalogKey)
            onRemoveRequested: catalogKey => root.removeRequested(catalogKey)
        }
    }
}
