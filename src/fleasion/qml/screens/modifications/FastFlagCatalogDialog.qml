pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    property var selectedCatalogNames: controller.catalogSelection.values()

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(860, parent ? parent.width - Theme.spaceXxl : 860)
    height: Math.min(640, parent ? parent.height - Theme.spaceXxl : 640)
    modal: true
    focus: true
    title: qsTr("Browse Roblox FastFlags")
    standardButtons: Dialog.Cancel

    onOpened: {
        if (root.controller.catalogModel.count === 0)
            root.controller.loadFastFlagCatalog(false);
    }

    Connections {
        target: root.controller.catalogSelection

        function onSelectionChanged() {
            root.selectedCatalogNames = root.controller.catalogSelection.values();
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceSm

        Label {
            Layout.fillWidth: true
            text: qsTr("Published ClientSettings values are shown when available. Tracker-only variables are added with an empty value.")
            color: Theme.textSecondary
            font.pointSize: TypeScale.body
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            SearchBox {
                id: catalogSearch

                Layout.fillWidth: true
                placeholderText: qsTr("Search names or published values")
                accessibleName: qsTr("Search the FastFlag catalog")
                onTextEdited: root.controller.filterFastFlagCatalog(text)
            }

            FluentButton {
                text: qsTr("Refresh")
                enabled: !root.controller.catalogTask.busy
                onClicked: root.controller.loadFastFlagCatalog(true)
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: catalogList

                objectName: "catalogList"
                anchors.fill: parent
                clip: true
                spacing: 2
                model: root.controller.catalogModel
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: true
                Accessible.name: qsTr("Available FastFlags")

                delegate: Rectangle {
                    id: catalogDelegate

                    required property string name
                    required property string value
                    required property string family
                    required property bool published
                    readonly property bool selected: root.selectedCatalogNames.indexOf(name) !== -1

                    width: ListView.view.width
                    height: Theme.largeControlHeight + Theme.spaceXs
                    color: selected ? Theme.accentSubtle : hover.hovered ? Theme.surfaceHover : 'transparent'

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: Theme.border
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spaceXs
                        anchors.rightMargin: Theme.spaceSm
                        spacing: Theme.spaceSm

                        FluentCheckBox {
                            checked: catalogDelegate.selected
                            Accessible.name: qsTr("Select %1").arg(catalogDelegate.name)
                            onToggled: root.controller.catalogSelection.setSelected(catalogDelegate.name, checked)
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1

                            Label {
                                Layout.fillWidth: true
                                text: catalogDelegate.name
                                color: Theme.textPrimary
                                font.family: "monospace"
                                font.pointSize: TypeScale.label
                                elide: Text.ElideMiddle
                            }

                            Label {
                                Layout.fillWidth: true
                                text: catalogDelegate.published ? qsTr("Published value: %1").arg(catalogDelegate.value) : qsTr("Known to the client · no published value")
                                color: Theme.textSecondary
                                font.pointSize: TypeScale.caption
                                elide: Text.ElideRight
                            }
                        }

                        StatusPill {
                            text: catalogDelegate.family
                            status: catalogDelegate.published ? "info" : "neutral"
                        }
                    }

                    HoverHandler {
                        id: hover
                    }

                    TapHandler {
                        onTapped: root.controller.catalogSelection.setSelected(catalogDelegate.name, !catalogDelegate.selected)
                    }
                }

                ScrollBar.vertical: FluentScrollBar {}
            }

            BusyIndicator {
                anchors.centerIn: parent
                visible: root.controller.catalogTask.busy
                running: visible
                Accessible.name: root.controller.catalogTask.message
            }

            EmptyState {
                anchors.fill: parent
                visible: !root.controller.catalogTask.busy && root.controller.catalogModel.count === 0
                iconText: "⚑"
                title: catalogSearch.text.length > 0 ? qsTr("No matching FastFlags") : qsTr("Catalog unavailable")
                description: catalogSearch.text.length > 0 ? qsTr("Try a shorter name or clear the search.") : qsTr("Refresh to retrieve ClientSettings and tracker data.")
                actionText: qsTr("Refresh")
                onActionTriggered: root.controller.loadFastFlagCatalog(true)
            }
        }

        RowLayout {
            Layout.fillWidth: true

            Label {
                Layout.fillWidth: true
                text: qsTr("%n result(s)", "", root.controller.catalogModel.count)
                color: Theme.textTertiary
                font.pointSize: TypeScale.caption
            }

            FluentButton {
                text: qsTr("Add selected")
                highlighted: true
                onClicked: {
                    const names = root.controller.catalogSelection.values();
                    if (root.controller.addCatalogFlags(names) > 0)
                        root.close();
                }
            }
        }
    }
}
