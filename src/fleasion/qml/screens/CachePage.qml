pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "cache" as Cache

FocusScope {
    id: root

    required property var controller
    required property var appController
    property string currentAssetKey
    property string pendingExportKey
    property int selectedCount: 0

    function focusSearch() {
        searchBox.forceActiveFocus();
    }
    function syncSelection() {
        selectedCount = controller.selection.values().length;
    }
    function exportAsset(key) {
        pendingExportKey = key;
        exportDialogLoader.active = true;
    }
    function selectAsset(key) {
        currentAssetKey = key;
        if (width < 900)
            detailsDialogLoader.active = true;
    }
    function sortDirection(key) {
        if (controller.sortKey !== key)
            return 'none';
        return controller.sortDescending ? 'descending' : 'ascending';
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.surface
        Accessible.ignored: true
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
            title: qsTr("Cache browser")
            subtitle: qsTr("Inspect, export, and reuse assets captured by Fleasion's local proxy.")
            iconText: "▦"

            FluentButton {
                text: qsTr("Refresh")
                enabled: !root.controller.task.busy
                onClicked: root.controller.refresh()
            }
        }

        Cache.CacheOverview {
            Layout.fillWidth: true
            controller: root.controller
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.sectionGap

            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                flat: true
                padding: 0
                contentSpacing: Theme.spaceXs

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceSm

                    SearchBox {
                        id: searchBox
                        Layout.fillWidth: true
                        text: root.controller.query
                        placeholderText: qsTr("Search names, creators, IDs, or hashes")
                        accessibleName: qsTr("Search cached assets")
                        onTextEdited: root.controller.query = text
                        onCleared: root.controller.query = ""
                    }

                    FluentComboBox {
                        id: typePicker
                        Layout.preferredWidth: 180
                        model: [qsTr("All types")].concat(root.controller.assetTypes)
                        Accessible.name: qsTr("Filter by asset type")
                        onActivated: index => {
                            root.controller.typeFilter = index === 0 ? "" : currentText;
                        }
                    }

                    FluentButton {
                        text: qsTr("View")
                        compact: true
                        onClicked: viewOptionsDialogLoader.active = true
                    }
                }

                Cache.CacheActionsBar {
                    Layout.fillWidth: true
                    controller: root.controller
                    onBlacklistRequested: blacklistDialogLoader.active = true
                    onClearRequested: clearDialogLoader.active = true
                    onLoadRequested: loadDialogLoader.active = true
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: selectedRow.implicitHeight + Theme.spaceSm
                    visible: root.selectedCount > 0
                    radius: Theme.radiusMd
                    color: Theme.accentSubtle
                    border.width: 1
                    border.color: Theme.accent

                    RowLayout {
                        id: selectedRow
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spaceSm
                        anchors.rightMargin: Theme.spaceXs

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("%n asset(s) selected", "", root.selectedCount)
                            color: Theme.textPrimary
                            font.pointSize: TypeScale.body
                            font.weight: TypeScale.medium
                        }

                        FluentButton {
                            text: qsTr("Clear")
                            flat: true
                            onClicked: root.controller.selection.clear()
                        }

                        FluentButton {
                            text: qsTr("Export")
                            enabled: !root.controller.task.busy
                            onClicked: bulkExportDialogLoader.active = true
                        }

                        FluentButton {
                            visible: root.width >= 780
                            text: qsTr("Copy converted")
                            enabled: !root.controller.task.busy
                            onClicked: root.controller.copyConvertedAssets(root.controller.selection.values())
                        }

                        FluentButton {
                            text: root.width >= 900 ? qsTr("Use as targets") : qsTr("Replacer")
                            enabled: !root.controller.task.busy
                            Accessible.description: qsTr("Create one replacement rule targeting every selected asset ID")
                            onClicked: root.controller.sendSelectionToReplacer(root.controller.selection.values())
                        }

                        FluentButton {
                            text: qsTr("Delete")
                            onClicked: deleteDialogLoader.active = true
                        }
                    }
                }

                DataTableHeader {
                    Layout.fillWidth: true
                    DataTableHeaderCell {
                        preferredWidth: 150
                        visible: root.controller.visibleColumns.indexOf("type") >= 0
                        text: qsTr("Type")
                        sortable: true
                        sortDirection: root.sortDirection("typeName")
                        onSortRequested: root.controller.toggleSort("typeName")
                    }
                    DataTableHeaderCell {
                        fillWidth: true
                        text: qsTr("Asset")
                        sortable: true
                        sortDirection: root.sortDirection("name")
                        onSortRequested: root.controller.toggleSort("name")
                    }
                    DataTableHeaderCell {
                        preferredWidth: 72
                        visible: root.controller.visibleColumns.indexOf("size") >= 0
                        text: qsTr("Size")
                        sortable: true
                        sortDirection: root.sortDirection("size")
                        onSortRequested: root.controller.toggleSort("size")
                    }
                    DataTableHeaderCell {
                        preferredWidth: 96
                        visible: root.width >= 900 && root.controller.visibleColumns.indexOf("cached_at") >= 0
                        text: qsTr("Cached")
                        sortable: true
                        sortDirection: root.sortDirection("cachedAt")
                        onSortRequested: root.controller.toggleSort("cachedAt")
                    }
                    DataTableHeaderCell {
                        preferredWidth: Theme.controlHeight
                        text: ""
                        Accessible.name: qsTr("Actions")
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: root.controller.model.count > 0 ? 0 : 1

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: root.controller.model
                        spacing: 2
                        boundsBehavior: Flickable.StopAtBounds
                        clip: true
                        reuseItems: true
                        Accessible.name: qsTr("Cached assets")

                        delegate: Cache.CacheAssetDelegate {
                            required property var model

                            width: ListView.view.width
                            selectionModel: root.controller.selection
                            assetKey: model.key
                            assetId: model.assetId
                            typeName: model.typeName
                            assetName: model.name
                            creator: model.creator
                            sizeText: model.sizeText
                            cachedAtText: model.cachedAtText
                            showType: root.controller.visibleColumns.indexOf("type") >= 0
                            showSize: root.controller.visibleColumns.indexOf("size") >= 0
                            showCached: root.controller.visibleColumns.indexOf("cached_at") >= 0
                            current: root.currentAssetKey === model.key
                            onActivated: key => root.selectAsset(key)
                            onExportRequested: key => root.exportAsset(key)
                        }

                        ScrollBar.vertical: FluentScrollBar {}
                    }

                    EmptyState {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        iconText: root.controller.query.length > 0 ? "⌕" : "▦"
                        title: root.controller.query.length > 0 ? qsTr("No matching cached assets") : qsTr("No assets captured yet")
                        description: root.controller.query.length > 0 ? qsTr("Try a different search or asset type.") : qsTr("Keep Fleasion running while Roblox downloads assets. New captures will appear here automatically.")
                        actionText: root.controller.query.length > 0 ? qsTr("Clear filters") : ""
                        onActionTriggered: {
                            searchBox.clear();
                            typePicker.currentIndex = 0;
                            root.controller.query = "";
                            root.controller.typeFilter = "";
                        }
                    }
                }
            }

            Cache.CacheDetailsPanel {
                Layout.preferredWidth: 320
                Layout.fillHeight: true
                visible: root.width >= 900 && root.currentAssetKey.length > 0
                controller: root.controller
                appController: root.appController
                assetKey: root.currentAssetKey
                onExportRequested: key => root.exportAsset(key)
            }
        }
    }

    Loader {
        id: viewOptionsDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheViewOptionsDialog {
                controller: root.controller
                onClosed: viewOptionsDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheViewOptionsDialog).open();
        }
    }

    Loader {
        id: exportDialogLoader
        active: false
        sourceComponent: Component {
            Cache.CacheExportDialog {
                controller: root.controller
                assetKey: root.pendingExportKey
                onClosed: exportDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheExportDialog).open();
        }
    }

    Loader {
        id: deleteDialogLoader
        active: false
        sourceComponent: Component {
            Cache.CacheDeleteDialog {
                assetCount: root.selectedCount
                onConfirmed: root.controller.deleteAssets(root.controller.selection.values())
                onClosed: deleteDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheDeleteDialog).open();
        }
    }

    Loader {
        id: bulkExportDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheBulkExportDialog {
                controller: root.controller
                assetKeys: root.controller.selection.values()
                onClosed: bulkExportDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheBulkExportDialog).open();
        }
    }

    Loader {
        id: blacklistDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheBlacklistDialog {
                controller: root.controller
                onClosed: blacklistDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheBlacklistDialog).open();
        }
    }

    Loader {
        id: loadDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheLoadAssetsDialog {
                controller: root.controller
                onClosed: loadDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheLoadAssetsDialog).open();
        }
    }

    Loader {
        id: clearDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheClearDialog {
                controller: root.controller
                onClosed: clearDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheClearDialog).open();
        }
    }

    Loader {
        id: detailsDialogLoader

        active: false
        sourceComponent: Component {
            Cache.CacheDetailsDialog {
                controller: root.controller
                appController: root.appController
                assetKey: root.currentAssetKey
                onExportRequested: key => root.exportAsset(key)
                onClosed: detailsDialogLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Cache.CacheDetailsDialog).open();
        }
    }

    Connections {
        target: root.controller.selection
        function onSelectionChanged() {
            root.syncSelection();
        }
    }

    Connections {
        target: root.controller

        function onBlacklistChanged() {
            root.currentAssetKey = "";
        }
    }

    Connections {
        target: root.controller.task

        function onSucceeded(result) {
            if (result && result.action === "clear")
                root.currentAssetKey = "";
        }
    }

    Component.onCompleted: syncSelection()
}
