pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

ColumnLayout {
    id: root

    property var controller
    property alias currentIndex: accountList.currentIndex
    readonly property bool hasAccounts: accountList.count > 0

    spacing: Theme.spaceXs

    Label {
        Layout.fillWidth: true
        text: root.controller && root.controller.selectedUsername.length > 0 ? qsTr("Selected: %1").arg(root.controller.selectedUsername) : qsTr("No account selected")
        color: Theme.textSecondary
        font.pointSize: TypeScale.label
    }

    ListView {
        id: accountList

        Layout.fillWidth: true
        Layout.preferredHeight: count > 0 ? Math.min(140, Math.max(52, contentHeight)) : 0
        visible: count > 0
        clip: true
        model: root.controller ? root.controller.accountsModel : null
        spacing: Theme.spaceXxs
        reuseItems: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: FluentScrollBar {}

        delegate: FluentItemDelegate {
            id: accountDelegate

            required property int index
            required property string username
            required property string userId
            required property string status
            required property string statusText

            width: ListView.view.width
            highlighted: root.controller && root.controller.selectedUsername === username
            onClicked: {
                accountList.currentIndex = index;
                root.controller.selectAccount(index);
            }

            contentItem: RowLayout {
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXxs

                    Label {
                        Layout.fillWidth: true
                        text: accountDelegate.username
                        color: Theme.textPrimary
                        font.pointSize: TypeScale.body
                        font.weight: TypeScale.medium
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.fillWidth: true
                        text: accountDelegate.userId.length > 0 ? qsTr("%1 · User %2").arg(accountDelegate.statusText).arg(accountDelegate.userId) : accountDelegate.statusText
                        color: Theme.textSecondary
                        font.pointSize: TypeScale.caption
                        elide: Text.ElideRight
                    }
                }

                StatusPill {
                    text: accountDelegate.statusText
                    status: accountDelegate.status === "expired" ? "warning" : "success"
                }

                FluentButton {
                    text: qsTr("Remove")
                    flat: true
                    compact: true
                    onClicked: root.controller.removeAccount(accountDelegate.index)
                }
            }
        }
    }
}
