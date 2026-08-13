pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

FluentDialog {
    id: root

    required property var controller
    property string catalogKey
    property string entryName
    property string targetPath
    property string fileFilter: qsTr("All files (*)")
    property bool bulkSky: false

    parent: Controls.Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent.width - Theme.spaceXxl)
    modal: true
    title: bulkSky ? qsTr("Apply one source to every sky face") : qsTr("Replace %1").arg(entryName)
    closePolicy: Controls.Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Controls.Label {
            Layout.fillWidth: true
            text: root.bulkSky ? qsTr("The replacement is applied to Back, Down, Front, Left, Right, and Up.") : root.targetPath
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        FileDropField {
            id: sourceField

            Layout.fillWidth: true
            placeholderText: qsTr("Local file, Roblox asset ID, or public CDN URL")
            dialogTitle: root.title
            nameFilters: [root.fileFilter]
            accessibleName: qsTr("Modification source")
        }

        Controls.Label {
            Layout.fillWidth: true
            text: qsTr("Local files can be dropped here. Numeric IDs use Roblox asset delivery; HTTP and HTTPS URLs are downloaded to Fleasion's modification cache.")
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }

            FluentButton {
                text: root.bulkSky ? qsTr("Apply to six faces") : qsTr("Apply replacement")
                highlighted: true
                enabled: sourceField.text.trim().length > 0
                onClicked: {
                    const applied = root.bulkSky ? root.controller.applySkyToAll(sourceField.text) : root.controller.applyBuiltIn(root.catalogKey, sourceField.text);
                    if (applied)
                        root.close();
                }
            }
        }
    }

    onOpened: {
        sourceField.text = "";
        sourceField.forceActiveFocus();
    }
}
