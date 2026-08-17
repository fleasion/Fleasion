pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

FluentDialog {
    id: root

    required property var controller
    property string selectedName

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent.width - Theme.spaceXxl)
    height: Math.min(580, parent.height - Theme.spaceXxl)
    modal: true
    title: qsTr("FastFlag profiles")
    standardButtons: Dialog.Close

    onOpened: controller.refreshProfiles()

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        Label {
            Layout.fillWidth: true
            text: qsTr("Save reusable JSON profiles, then replace or merge them into the current editor.")
            color: Theme.textSecondary
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true

            FluentTextField {
                id: profileName

                Layout.fillWidth: true
                text: root.selectedName
                placeholderText: qsTr("Profile name")
                Accessible.name: qsTr("FastFlag profile name")
            }

            FluentButton {
                text: qsTr("Save current")
                enabled: profileName.text.trim().length > 0
                onClicked: {
                    if (root.controller.saveProfile(profileName.text))
                        root.selectedName = profileName.text.trim();
                }
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: root.controller.profilesModel
            clip: true
            spacing: Theme.spaceXxs
            reuseItems: true

            delegate: FluentItemDelegate {
                id: profileDelegate

                required property string name
                width: ListView.view.width
                text: name
                highlighted: root.selectedName === name
                onClicked: root.selectedName = name
                onDoubleClicked: {
                    root.controller.loadProfile(name, replaceCheck.checked);
                    root.accept();
                }
            }

            ScrollBar.vertical: FluentScrollBar {}
        }

        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.controller.profilesModel.count === 0
            iconText: "⚑"
            title: qsTr("No saved profiles")
            description: qsTr("Enter a name above to save the current FastFlags.")
        }

        FluentCheckBox {
            id: replaceCheck
            text: qsTr("Replace current flags when loading")
            checked: true
        }

        RowLayout {
            Layout.fillWidth: true

            FluentButton {
                text: qsTr("Delete")
                enabled: root.selectedName.length > 0
                onClicked: {
                    if (root.controller.deleteProfile(root.selectedName)) {
                        root.selectedName = "";
                        profileName.clear();
                    }
                }
            }

            FluentButton {
                text: qsTr("Rename")
                enabled: root.selectedName.length > 0 && profileName.text.trim().length > 0 && profileName.text.trim() !== root.selectedName
                onClicked: {
                    if (root.controller.renameProfile(root.selectedName, profileName.text))
                        root.selectedName = profileName.text.trim();
                }
            }

            Item {
                Layout.fillWidth: true
            }

            FluentButton {
                text: qsTr("Load")
                highlighted: true
                enabled: root.selectedName.length > 0
                onClicked: {
                    if (root.controller.loadProfile(root.selectedName, replaceCheck.checked))
                        root.accept();
                }
            }
        }
    }
}
