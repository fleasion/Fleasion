import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components

Card {
    id: root

    required property var controller
    required property var appController
    property string assetKey
    readonly property var details: assetKey.length > 0 ? controller.asset(assetKey) : ({})
    signal exportRequested(string assetKey)

    function previewHeight(kind) {
        const available = detailsScroll.availableHeight > 0 ? detailsScroll.availableHeight : root.height;
        switch (kind) {
        case 'document':
            return Math.min(500, Math.max(root.width < 520 ? 410 : 310, available * 0.72));
        case 'animation':
            return Math.min(390, Math.max(270, available * 0.58));
        case 'font':
            return Math.min(350, Math.max(250, available * 0.5));
        case 'texturepack':
            return Math.min(560, Math.max(390, available * 0.76));
        default:
            return 190;
        }
    }

    title: details.name || qsTr("Asset details")
    subtitle: details.typeName || ""
    flat: true
    padding: Theme.spaceXs
    contentSpacing: Theme.spaceXs

    ScrollView {
        id: detailsScroll

        Layout.fillWidth: true
        Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: detailsScroll.availableWidth
            spacing: Theme.spaceXs

            AssetPreview {
                Layout.fillWidth: true
                Layout.preferredHeight: root.previewHeight(root.controller.previewKind)
                controller: root.controller
                appController: root.appController
                assetKey: root.assetKey
            }

            CacheDetailRow {
                Layout.fillWidth: true
                labelText: qsTr("Asset ID")
                valueText: String(root.details.assetId || "")
                copyText: valueText
                onCopyRequested: value => root.appController.copyText(value)
            }

            CacheDetailRow {
                Layout.fillWidth: true
                labelText: qsTr("Creator")
                valueText: {
                    if (root.details.creator && root.details.creatorId)
                        return qsTr("%1 · ID %2").arg(root.details.creator).arg(root.details.creatorId);
                    return String(root.details.creator || root.details.creatorId || "");
                }
                copyText: String(root.details.creatorId || "")
                openUrl: String(root.details.creatorUrl || "")
                onCopyRequested: value => root.appController.copyText(value)
                onOpenRequested: value => root.appController.openUrl(value)
            }

            CacheDetailRow {
                Layout.fillWidth: true
                labelText: qsTr("Content hash")
                valueText: String(root.details.hash || "")
                copyText: valueText
                onCopyRequested: value => root.appController.copyText(value)
            }

            CacheDetailRow {
                Layout.fillWidth: true
                visible: Boolean(root.details.sourceUrl)
                labelText: qsTr("Source URL")
                valueText: String(root.details.sourceUrl || "")
                copyText: valueText
                openUrl: valueText
                onCopyRequested: value => root.appController.copyText(value)
                onOpenRequested: value => root.appController.openUrl(value)
            }

            CacheDetailRow {
                Layout.fillWidth: true
                labelText: qsTr("Cached")
                valueText: qsTr("%1 · %2").arg(root.details.cachedAtText || qsTr("Unknown time")).arg(root.details.sizeText || qsTr("Unknown size"))
            }

            FluentButton {
                Layout.fillWidth: true
                text: qsTr("Export asset")
                highlighted: true
                onClicked: root.exportRequested(root.assetKey)
            }

            FluentButton {
                Layout.fillWidth: true
                text: qsTr("Copy converted file")
                enabled: !root.controller.task.busy && root.controller.convertedCopyAvailable(root.assetKey)
                onClicked: root.controller.copyConvertedAssets([root.assetKey])
            }

            FluentButton {
                Layout.fillWidth: true
                text: qsTr("Use ID as a replacement")
                onClicked: root.controller.sendToReplacer(root.assetKey, true)
            }

            FluentButton {
                Layout.fillWidth: true
                text: qsTr("Create a rule targeting this asset")
                onClicked: root.controller.sendToReplacer(root.assetKey, false)
            }
        }
    }
}
