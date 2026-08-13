pragma ComponentBehavior: Bound

import Fleasion.Components
import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root

    required property var controller
    signal inspectRequested(string name, string targetPath)
    spacing: Theme.spaceSm

    property string pendingKey
    property string pendingName
    property string pendingTarget
    property string pendingFilter
    property bool pendingBulkSky: false

    function openSource(catalogKey, entryName, targetPath, fileFilter, bulkSky) {
        pendingKey = catalogKey;
        pendingName = entryName;
        pendingTarget = targetPath;
        pendingFilter = fileFilter;
        pendingBulkSky = bulkSky || false;
        sourceLoader.active = true;
    }

    SectionHeader {
        Layout.fillWidth: true
        title: qsTr("Built-in Roblox resources")
        subtitle: qsTr("Common replacement targets with platform-aware paths, original-file backups, and typed source handling.")
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.skyboxModel
        title: qsTr("Outdoor skybox")
        subtitle: qsTr("Six directional sky textures")
        primaryActionText: qsTr("Apply to all faces")
        onPrimaryActionRequested: root.openSource("", qsTr("Outdoor skybox"), "", qsTr("Image files (*.png *.jpg *.jpeg *.tex);;All files (*)"), true)
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onResetRequested: key => root.controller.resetBuiltIn(key)
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.indoorSkyboxModel
        title: qsTr("Indoor skybox")
        subtitle: qsTr("Six indoor directional textures")
        expanded: false
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onResetRequested: key => root.controller.resetBuiltIn(key)
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.texturesModel
        title: qsTr("Textures and cursors")
        subtitle: qsTr("Stud surfaces, mouse cursors, sun, and moon")
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onResetRequested: key => root.controller.resetBuiltIn(key)
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: soberWarning.implicitHeight + Theme.spaceSm
        visible: root.controller.soberMeshLimitation.length > 0
        color: Theme.warningSubtle
        radius: Theme.radiusMd

        Controls.Label {
            id: soberWarning

            anchors.fill: parent
            anchors.margins: Theme.spaceXs
            text: root.controller.soberMeshLimitation
            color: Theme.warning
            font.pointSize: TypeScale.label
            wrapMode: Text.Wrap
        }
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.avatarMeshesModel
        title: qsTr("R6 avatar meshes")
        subtitle: qsTr("Default limbs, torso, head, and optional head variants")
        expanded: !root.controller.soberMeshLimitation.length
        addHeadVariant: true
        headVariantModel: root.controller.availableHeadVariantsModel
        onHeadVariantRequested: key => root.controller.addHeadVariant(key)
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onResetRequested: key => root.controller.resetBuiltIn(key)
        onRemoveRequested: key => root.controller.removeHeadVariant(key)
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.soundsModel
        title: qsTr("Sounds")
        subtitle: qsTr("Replace common player sounds or use the bundled silent assets")
        expanded: false
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onMuteRequested: key => root.controller.muteBuiltIn(key)
        onResetRequested: key => root.controller.resetBuiltIn(key)
    }

    BuiltInModificationSection {
        Layout.fillWidth: true
        sectionModel: root.controller.fontsModel
        title: qsTr("Custom font")
        subtitle: qsTr("Validate and replace Roblox's font family resources")
        onSourceRequested: (key, name, target, filter) => root.openSource(key, name, target, filter, false)
        onInspectRequested: (name, target) => root.inspectRequested(name, target)
        onResetRequested: key => root.controller.resetBuiltIn(key)
    }

    Loader {
        id: sourceLoader

        objectName: "builtInSourceLoader"
        active: false
        asynchronous: true
        sourceComponent: Component {
            BuiltInModificationSourceDialog {
                controller: root.controller
                catalogKey: root.pendingKey
                entryName: root.pendingName
                targetPath: root.pendingTarget
                fileFilter: root.pendingFilter
                bulkSky: root.pendingBulkSky
                onClosed: sourceLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as BuiltInModificationSourceDialog).open();
        }
    }
}
