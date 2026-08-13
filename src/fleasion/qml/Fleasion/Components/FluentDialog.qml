import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.Dialog {
    id: root

    padding: Theme.panelPadding
    spacing: Theme.sectionGap
    closePolicy: Controls.Popup.CloseOnEscape

    background: Rectangle {
        color: Theme.surfaceElevated
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.borderStrong
    }

    header: Item {
        id: dialogHeader

        implicitHeight: root.title.length > 0 ? 48 : 0
        visible: root.title.length > 0

        Controls.Label {
            anchors.left: dialogHeader.left
            anchors.right: dialogHeader.right
            anchors.verticalCenter: dialogHeader.verticalCenter
            anchors.leftMargin: Theme.panelPadding
            anchors.rightMargin: Theme.panelPadding
            text: root.title
            color: Theme.textPrimary
            font.pointSize: TypeScale.title
            font.weight: TypeScale.semibold
            elide: Text.ElideRight
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.border
        }
    }

    footer: Controls.DialogButtonBox {
        id: buttonBox

        visible: count > 0
        spacing: Theme.spaceXs
        leftPadding: Theme.panelPadding
        rightPadding: Theme.panelPadding
        topPadding: Theme.spaceSm
        bottomPadding: Theme.spaceSm
        alignment: Qt.AlignRight

        delegate: FluentButton {
            compact: true
            highlighted: Controls.DialogButtonBox.buttonRole === Controls.DialogButtonBox.AcceptRole || Controls.DialogButtonBox.buttonRole === Controls.DialogButtonBox.YesRole || Controls.DialogButtonBox.buttonRole === Controls.DialogButtonBox.ApplyRole
            danger: Controls.DialogButtonBox.buttonRole === Controls.DialogButtonBox.DestructiveRole
        }

        background: Rectangle {
            color: Theme.surfaceElevated

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: 1
                color: Theme.border
            }
        }
    }

    Controls.Overlay.modal: Rectangle {
        color: Theme.overlay
    }

    Controls.Overlay.modeless: Rectangle {
        color: Qt.rgba(0, 0, 0, Theme.isDark ? 0.24 : 0.12)
    }
}
