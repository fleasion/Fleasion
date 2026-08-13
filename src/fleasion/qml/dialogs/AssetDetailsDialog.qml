import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    property var appController
    property var controller
    property string assetKey
    property var details: ({})

    function showAsset(key) {
        assetKey = key;
        details = controller ? controller.asset(key) : ({});
        open();
    }

    width: Math.min(620, parent ? parent.width - Theme.spaceXxl : 620)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    title: qsTr("Asset details")
    standardButtons: Dialog.Close

    contentItem: ColumnLayout {
        spacing: Theme.spaceLg

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                Label {
                    Layout.fillWidth: true
                    text: root.details.name || qsTr("Unnamed asset")
                    color: Theme.textPrimary
                    font.pointSize: TypeScale.title
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Asset %1").arg(root.details.assetId || "—")
                    color: Theme.textSecondary
                    font.pointSize: TypeScale.body
                }
            }

            StatusPill {
                text: root.details.typeName || qsTr("Unknown")
                status: "info"
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.spaceLg
            rowSpacing: Theme.spaceSm

            Label {
                text: qsTr("Creator")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }
            Label {
                Layout.fillWidth: true
                text: root.details.creator || qsTr("Unknown")
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                elide: Text.ElideRight
            }
            Label {
                text: qsTr("Size")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }
            Label {
                Layout.fillWidth: true
                text: root.details.sizeText || "—"
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
            }
            Label {
                text: qsTr("Cached")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }
            Label {
                Layout.fillWidth: true
                text: root.details.cachedAtText || "—"
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
            }
            Label {
                text: qsTr("Hash")
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }
            Label {
                Layout.fillWidth: true
                text: root.details.hash || "—"
                color: Theme.textPrimary
                font.family: "monospace"
                font.pixelSize: 11
                elide: Text.ElideMiddle
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentButton {
                text: qsTr("Copy asset ID")
                enabled: root.appController && root.details.assetId
                onClicked: root.appController.copyText(String(root.details.assetId))
            }

            FluentButton {
                text: qsTr("Use as target")
                enabled: root.controller && root.assetKey.length > 0
                onClicked: {
                    root.controller.sendToReplacer(root.assetKey, false);
                    root.close();
                }
            }

            FluentButton {
                text: qsTr("Use as replacement")
                enabled: root.controller && root.assetKey.length > 0
                onClicked: {
                    root.controller.sendToReplacer(root.assetKey, true);
                    root.close();
                }
            }
        }
    }
}
