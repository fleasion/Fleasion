import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Rectangle {
    id: root

    required property var selectionModel
    required property string assetKey
    required property string assetId
    required property string typeName
    required property string assetName
    required property string creator
    required property string sizeText
    required property string cachedAtText
    property bool showType: true
    property bool showSize: true
    property bool showCached: true
    property bool selected: false
    property bool current: false
    signal activated(string assetKey)
    signal exportRequested(string assetKey)

    function syncSelection() {
        selected = selectionModel.contains(assetKey);
    }

    implicitHeight: 56
    color: current || selected ? Theme.accentSubtle : pointer.hovered ? Theme.surfaceHover : "transparent"
    radius: Theme.radiusMd
    border.width: activeFocus ? 2 : current ? 1 : 0
    border.color: activeFocus ? Theme.focusRing : Theme.accent
    activeFocusOnTab: true
    Accessible.role: Accessible.ListItem
    Accessible.name: qsTr("%1, %2, asset %3, %4").arg(assetName.length > 0 ? assetName : qsTr("Unnamed asset")).arg(typeName).arg(assetId).arg(sizeText)

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spaceSm
        anchors.rightMargin: Theme.spaceXs
        spacing: Theme.spaceSm

        FluentCheckBox {
            checked: root.selected
            Accessible.name: qsTr("Select asset %1").arg(root.assetId)
            onToggled: root.selectionModel.setSelected(root.assetKey, checked)
        }

        StatusPill {
            visible: root.showType
            text: root.typeName
            status: "info"
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            spacing: 2

            Label {
                Layout.fillWidth: true
                text: root.assetName.length > 0 ? root.assetName : qsTr("Unnamed asset")
                color: Theme.textPrimary
                font.pointSize: TypeScale.body
                font.weight: TypeScale.medium
                elide: Text.ElideRight
            }

            Label {
                Layout.fillWidth: true
                text: root.creator.length > 0 ? qsTr("%1 · ID %2").arg(root.creator).arg(root.assetId) : qsTr("Asset ID %1").arg(root.assetId)
                color: Theme.textSecondary
                font.pointSize: TypeScale.label
                elide: Text.ElideRight
            }
        }

        Label {
            Layout.preferredWidth: 72
            visible: root.showSize
            text: root.sizeText
            color: Theme.textSecondary
            font.pointSize: TypeScale.label
            horizontalAlignment: Text.AlignRight
        }

        Label {
            Layout.preferredWidth: 96
            visible: root.width >= 680 && root.showCached
            text: root.cachedAtText
            color: Theme.textTertiary
            font.pointSize: TypeScale.label
            horizontalAlignment: Text.AlignRight
        }

        IconButton {
            iconText: "↑"
            text: qsTr("Export asset %1").arg(root.assetId)
            onClicked: root.exportRequested(root.assetKey)
        }
    }

    HoverHandler {
        id: pointer
    }
    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: root.activated(root.assetKey)
    }
    Keys.onReturnPressed: event => {
        root.activated(root.assetKey);
        event.accepted = true;
    }
    Keys.onSpacePressed: event => {
        root.selectionModel.setSelected(root.assetKey, !root.selected);
        event.accepted = true;
    }

    Component.onCompleted: syncSelection()
    onAssetKeyChanged: syncSelection()

    Connections {
        target: root.selectionModel
        function onSelectionChanged() {
            root.syncSelection();
        }
    }
}
