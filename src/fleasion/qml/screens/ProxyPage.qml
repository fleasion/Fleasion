pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "proxy" as Proxy

Item {
    id: root

    property var controller
    property var appController
    property string inspectorEntryKey

    function inspectTraffic(entryKey) {
        inspectorEntryKey = entryKey;
        inspectorLoader.active = true;
    }

    Component.onCompleted: {
        if (controller)
            controller.refresh();
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pageGutter
        anchors.rightMargin: Theme.pageGutter
        anchors.topMargin: Theme.pageTopGutter
        anchors.bottomMargin: Theme.pageBottomGutter
        spacing: Theme.sectionGap

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Proxy traffic")
            subtitle: qsTr("Capture, inspect, edit, and replay Roblox API traffic without leaving Fleasion.")
            iconText: "⇄"

            StatusPill {
                text: root.controller ? root.controller.statusText : qsTr("Unavailable")
                status: root.controller && root.controller.running ? "success" : root.controller && root.controller.lifecycleTask.busy ? "info" : "warning"
            }

            IconButton {
                iconText: "↯"
                text: qsTr("Manage auto-replace rules")
                enabled: Boolean(root.controller)
                onClicked: rulesLoader.active = true
            }

            IconButton {
                iconText: "↻"
                text: qsTr("Refresh traffic")
                enabled: Boolean(root.controller)
                onClicked: root.controller.refresh()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: privacyWarning.implicitHeight + Theme.spaceMd
            radius: Theme.radiusSm
            color: Theme.warningSubtle

            Text {
                id: privacyWarning

                anchors.fill: parent
                anchors.margins: Theme.spaceXs
                text: root.controller ? root.controller.trafficPrivacyWarning : ""
                color: Theme.warning
                font.pointSize: TypeScale.caption
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.sectionGap

            Proxy.ProxyServicePanel {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                controller: root.controller
            }

            Rectangle {
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                color: Theme.border
                Accessible.ignored: true
            }

            Proxy.InterceptionPanel {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                controller: root.controller
            }
        }

        Proxy.TrafficPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            controller: root.controller
            onInspectRequested: entryKey => root.inspectTraffic(entryKey)
        }
    }

    Loader {
        id: inspectorLoader

        anchors.fill: parent
        active: false
        sourceComponent: Component {
            Proxy.TrafficInspector {
                controller: root.controller
                appController: root.appController
                entryKey: root.inspectorEntryKey
                onClosed: inspectorLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Proxy.TrafficInspector).open();
        }
    }

    Loader {
        id: rulesLoader

        anchors.fill: parent
        active: false
        sourceComponent: Component {
            Proxy.AutoReplaceRulesDialog {
                controller: root.controller
                onClosed: rulesLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Proxy.AutoReplaceRulesDialog).open();
        }
    }
}
