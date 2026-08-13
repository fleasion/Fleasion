import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Control {
    id: root

    property alias text: pathField.text
    property alias placeholderText: pathField.placeholderText
    property alias readOnly: pathField.readOnly
    property string accessibleName: qsTr('File path')
    property string dialogTitle: qsTr('Choose a file')
    property var nameFilters: [qsTr('All files (*)')]
    property bool browseEnabled: true
    property bool acceptDrops: true
    readonly property bool dragActive: dropArea.containsDrag

    signal browseRequested
    signal fileChosen(string path)
    signal fileDropped(string path)

    function localPath(urlValue) {
        let path = String(urlValue);
        if (!path.startsWith('file:'))
            return path;

        path = decodeURIComponent(path.replace(/^file:\/\//, ''));
        if (Qt.platform.os === 'windows' && path.length > 2 && path[0] === '/' && path[2] === ':')
            path = path.substring(1);

        return path;
    }

    function applyUrl(urlValue, wasDropped) {
        const path = localPath(urlValue);
        if (path.length === 0)
            return;

        pathField.text = path;
        root.fileChosen(path);
        if (wasDropped)
            root.fileDropped(path);
    }

    padding: 2
    implicitWidth: 420
    implicitHeight: Theme.largeControlHeight
    Accessible.ignored: true

    DropArea {
        id: dropArea

        anchors.fill: parent
        enabled: root.enabled && root.acceptDrops
        z: 2
        onEntered: drag => {
            return drag.accepted = drag.hasUrls;
        }
        onDropped: drop => {
            if (!drop.hasUrls || drop.urls.length === 0)
                return;

            root.applyUrl(drop.urls[0], true);
            drop.acceptProposedAction();
        }
    }

    FileDialog {
        id: fileDialog

        title: root.dialogTitle
        nameFilters: root.nameFilters
        onAccepted: root.applyUrl(selectedFile, false)
    }

    contentItem: RowLayout {
        spacing: Theme.spaceXs

        Label {
            text: '\u21e9'
            color: root.dragActive ? Theme.accent : Theme.textSecondary
            font.pointSize: TypeScale.body
            horizontalAlignment: Text.AlignHCenter
            Layout.preferredWidth: 32
            Accessible.ignored: true
        }

        TextField {
            id: pathField

            placeholderText: qsTr('Drop a file here or browse')
            selectByMouse: true
            activeFocusOnTab: true
            color: Theme.textPrimary
            placeholderTextColor: Theme.textTertiary
            selectionColor: Theme.accent
            selectedTextColor: Theme.accentForeground
            font.pointSize: TypeScale.body
            leftPadding: 0
            rightPadding: 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            Accessible.name: root.accessibleName
            Accessible.description: qsTr('Enter a local path or drop a file')
            background: null
        }

        FluentButton {
            id: browseButton

            visible: root.browseEnabled
            text: qsTr('Browse\u2026')
            activeFocusOnTab: true
            Layout.preferredHeight: Theme.controlHeight
            onClicked: {
                root.browseRequested();
                fileDialog.open();
            }

            contentItem: Label {
                text: browseButton.text
                color: browseButton.enabled ? Theme.textPrimary : Theme.textDisabled
                font.pointSize: TypeScale.label
                font.weight: TypeScale.medium
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                color: browseButton.down ? Theme.surfacePressed : (browseButton.hovered ? Theme.surfaceHover : Theme.surfaceSubtle)
                radius: Theme.radiusMd
                border.width: browseButton.activeFocus ? 2 : 1
                border.color: browseButton.activeFocus ? Theme.focusRing : Theme.border
            }
        }
    }

    background: Rectangle {
        color: root.dragActive ? Theme.accentSubtle : Theme.surfaceElevated
        radius: Theme.radiusMd
        border.width: pathField.activeFocus || root.dragActive ? 2 : 1
        border.color: pathField.activeFocus || root.dragActive ? Theme.focusRing : Theme.borderStrong

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
