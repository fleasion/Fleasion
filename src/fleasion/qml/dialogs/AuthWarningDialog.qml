import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Components
import Fleasion.Theme

FluentDialog {
    id: root

    property string heading
    property string message
    property string detail
    property bool canOpenLogin: false
    property string continueText
    property string loginText
    property string exitText
    property int countdownSeconds: 5
    signal continueRequested
    signal loginRequested
    signal exitRequested
    signal linkRequested(string url)

    width: Math.min(640, parent ? parent.width - Theme.spaceXxl : 640)
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    title: heading
    standardButtons: Dialog.NoButton

    Timer {
        interval: 1000
        repeat: true
        running: root.opened && root.countdownSeconds > 0
        onTriggered: root.countdownSeconds--
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: root.heading
            color: Theme.textPrimary
            font.pointSize: TypeScale.title
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.Wrap
        }

        FluentScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(280, detailText.implicitHeight + Theme.spaceMd)
            clip: true

            Text {
                id: detailText

                width: parent.width
                text: root.detail
                textFormat: Text.RichText
                color: Theme.textSecondary
                font.pointSize: TypeScale.caption
                wrapMode: Text.Wrap
                onLinkActivated: link => root.linkRequested(link)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            FluentButton {
                text: root.exitText
                danger: true
                onClicked: root.exitRequested()
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                visible: root.canOpenLogin
                enabled: root.countdownSeconds <= 0
                text: root.loginText
                onClicked: root.loginRequested()
            }

            FluentButton {
                enabled: root.countdownSeconds <= 0
                text: root.countdownSeconds > 0 ? root.continueText + " (" + root.countdownSeconds + ")" : root.continueText
                highlighted: true
                onClicked: root.continueRequested()
            }
        }
    }
}
