import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    signal confirmed
    property int secondsRemaining: 15

    anchors.centerIn: parent
    title: qsTr("Enable unsupported FastFlags?")
    width: Math.min(540, parent ? parent.width - Theme.spaceLg : 540)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton

    onOpened: {
        secondsRemaining = 15;
        countdown.restart();
    }
    onClosed: countdown.stop()

    Timer {
        id: countdown

        interval: 1000
        repeat: true
        onTriggered: {
            root.secondsRemaining = Math.max(0, root.secondsRemaining - 1);
            if (root.secondsRemaining === 0)
                stop();
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("Custom FastFlags bypass Roblox's local allowlist and change ClientSettings responses while Roblox is running.")
            color: Theme.textPrimary
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: warningText.implicitHeight + Theme.spaceMd * 2
            radius: Theme.radiusSm
            color: Theme.warningSubtle

            Label {
                id: warningText

                anchors.fill: parent
                anchors.margins: Theme.spaceMd
                text: qsTr("Unsupported values can break the client and may carry account moderation risk. Fleasion cannot determine whether a flag is safe. Review every value before enabling this feature.")
                color: Theme.textPrimary
                wrapMode: Text.Wrap
            }
        }

        Label {
            Layout.fillWidth: true
            text: root.secondsRemaining > 0 ? qsTr("Read the warning to continue in %n second(s).", "", root.secondsRemaining) : qsTr("You can now accept the risk and enable custom FastFlags.")
            color: root.secondsRemaining > 0 ? Theme.textSecondary : Theme.success
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }

        DialogActionBar {
            Layout.fillWidth: true
            acceptText: root.secondsRemaining > 0 ? qsTr("I understand (%1)").arg(root.secondsRemaining) : qsTr("I understand")
            acceptEnabled: root.secondsRemaining === 0
            onCancelRequested: root.reject()
            onAcceptRequested: {
                root.confirmed();
                root.accept();
            }
        }
    }
}
