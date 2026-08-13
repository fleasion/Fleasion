pragma ComponentBehavior: Bound

import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.ComboBox {
    id: root

    implicitWidth: Math.max(150, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceSm
    rightPadding: Theme.spaceXl
    hoverEnabled: true
    activeFocusOnTab: true

    contentItem: Text {
        leftPadding: 0
        rightPadding: 0
        text: root.displayText
        font.pointSize: TypeScale.body
        color: root.enabled ? Theme.textPrimary : Theme.textDisabled
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        id: indicatorText

        x: root.mirrored ? Theme.spaceSm : root.width - indicatorText.width - Theme.spaceSm
        y: (root.height - indicatorText.height) / 2
        text: root.popup.visible ? '\u2303' : '\u2304'
        color: root.enabled ? Theme.textSecondary : Theme.textDisabled
        font.pointSize: TypeScale.caption
        Accessible.ignored: true
    }

    background: Rectangle {
        color: root.down ? Theme.surfacePressed : root.hovered ? Theme.surfaceHover : Theme.surfaceElevated
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.borderStrong
    }

    delegate: Controls.ItemDelegate {
        id: option

        required property var modelData
        required property int index
        width: ListView.view ? ListView.view.width : root.width
        implicitHeight: Theme.controlHeight
        highlighted: root.highlightedIndex === index
        hoverEnabled: true

        contentItem: Text {
            text: root.textRole.length > 0 ? String(option.modelData[root.textRole]) : String(option.modelData)
            color: option.enabled ? Theme.textPrimary : Theme.textDisabled
            font.pointSize: TypeScale.body
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        background: Rectangle {
            color: option.down ? Theme.surfacePressed : option.highlighted || option.hovered ? Theme.surfaceHover : 'transparent'
            radius: Theme.radiusSm
        }
    }

    popup: Controls.Popup {
        y: root.height + Theme.spaceXxs
        width: root.width
        implicitHeight: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, 320)
        padding: Theme.spaceXxs

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            Controls.ScrollIndicator.vertical: Controls.ScrollIndicator {}
        }

        background: Rectangle {
            color: Theme.surfaceElevated
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.borderStrong
        }
    }
}
