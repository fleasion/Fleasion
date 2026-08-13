import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    required property var controller
    required property var appController
    property string assetKey
    signal exportRequested(string assetKey)

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(600, parent.width - Theme.spaceXl)
    height: Math.min(700, parent.height - Theme.spaceXl)
    modal: true
    focus: true
    title: qsTr('Cached asset')
    standardButtons: Dialog.NoButton
    closePolicy: Popup.CloseOnEscape

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        CacheDetailsPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            controller: root.controller
            appController: root.appController
            assetKey: root.assetKey
            onExportRequested: key => root.exportRequested(key)
        }

        RowLayout {
            Layout.fillWidth: true

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr('Close')
                onClicked: root.close()
            }
        }
    }
}
