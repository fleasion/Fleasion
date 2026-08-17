import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    signal confirmed

    anchors.centerIn: parent
    title: qsTr("Disable proxy features?")
    width: Math.min(520, parent ? parent.width - Theme.spaceLg : 520)
    modal: true
    focus: true
    standardButtons: Dialog.NoButton

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("This immediately stops the local proxy and every workflow that depends on it.")
            color: Theme.textPrimary
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Replacer, cache scraping, subplace joining, reserved-server rejoin, username spoofing, and the subplace blacklist will remain unavailable until proxy features are enabled again.")
            color: Theme.textSecondary
            wrapMode: Text.Wrap
        }

        DialogActionBar {
            Layout.fillWidth: true
            acceptText: qsTr("Disable")
            acceptDanger: true
            onCancelRequested: root.reject()
            onAcceptRequested: {
                root.confirmed();
                root.accept();
            }
        }
    }
}
