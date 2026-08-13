pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FocusScope {
    id: root

    property string currentPage: "replacer"
    property bool compact: false
    readonly property real preferredWidth: compact ? 72 : Theme.navigationWidth
    signal pageRequested(string pageId)
    signal aboutRequested

    implicitWidth: preferredWidth
    Accessible.role: Accessible.Pane
    Accessible.name: qsTr("Main navigation")

    Rectangle {
        anchors.fill: parent
        color: Theme.surfaceSubtle
        border.width: 0

        Rectangle {
            anchors.right: parent.right
            width: 1
            height: parent.height
            color: Theme.border
            Accessible.ignored: true
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceSm
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spaceSm
            Layout.rightMargin: Theme.spaceSm
            Layout.topMargin: Theme.spaceSm
            Layout.bottomMargin: Theme.spaceMd
            spacing: Theme.spaceSm

            Rectangle {
                Layout.preferredWidth: 36
                Layout.preferredHeight: 36
                radius: Theme.radiusMd
                color: Theme.accent
                Accessible.ignored: true

                Label {
                    anchors.centerIn: parent
                    text: "F"
                    color: Theme.accentForeground
                    font.pointSize: TypeScale.subtitle
                    font.weight: Font.DemiBold
                }
            }

            Label {
                Layout.fillWidth: true
                visible: !root.compact
                text: qsTr("Fleasion")
                color: Theme.textPrimary
                font.pointSize: TypeScale.subtitle
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spaceSm
            Layout.topMargin: Theme.spaceXs
            visible: !root.compact
            text: qsTr("WORKSPACE")
            color: Theme.textTertiary
            font.pointSize: TypeScale.caption
            font.weight: Font.DemiBold
            font.letterSpacing: 1
        }

        ListView {
            id: navigationView

            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 2
            boundsBehavior: Flickable.StopAtBounds
            activeFocusOnTab: true
            Accessible.name: qsTr("Workspace pages")

            model: ListModel {
                ListElement {
                    pageId: "replacer"
                    label: qsTr("Replacer")
                    glyph: "⇄"
                }
                ListElement {
                    pageId: "cache"
                    label: qsTr("Cache browser")
                    glyph: "▦"
                }
                ListElement {
                    pageId: "modifications"
                    label: qsTr("Modifications")
                    glyph: "✦"
                }
                ListElement {
                    pageId: "subplaces"
                    label: qsTr("Subplaces")
                    glyph: "◈"
                }
                ListElement {
                    pageId: "misc"
                    label: qsTr("Utilities")
                    glyph: "◇"
                }
                ListElement {
                    pageId: "proxy"
                    label: qsTr("Proxy traffic")
                    glyph: "◉"
                }
                ListElement {
                    pageId: "logs"
                    label: qsTr("Logs")
                    glyph: "≡"
                }
                ListElement {
                    pageId: "settings"
                    label: qsTr("Settings")
                    glyph: "⚙"
                }
            }

            delegate: NavItem {
                required property var model
                required property string pageId

                width: ListView.view.width
                text: root.compact ? "" : model.label
                iconText: model.glyph
                selected: root.currentPage === model.pageId
                Accessible.name: model.label
                onClicked: root.pageRequested(model.pageId)
            }

            ScrollBar.vertical: ScrollBar {
                policy: navigationView.contentHeight > navigationView.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            Layout.topMargin: Theme.spaceXs
            Layout.bottomMargin: Theme.spaceXs
            color: Theme.border
            Accessible.ignored: true
        }

        NavItem {
            Layout.fillWidth: true
            text: root.compact ? "" : qsTr("About Fleasion")
            iconText: "ⓘ"
            Accessible.name: qsTr("About Fleasion")
            onClicked: root.aboutRequested()
        }
    }
}
