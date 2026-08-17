import Fleasion.Theme
import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.ScrollBar {
    id: root

    property string accessibleName: root.orientation === Qt.Vertical ? qsTr('Vertical scroll bar') : qsTr('Horizontal scroll bar')

    implicitWidth: root.orientation === Qt.Vertical ? 12 : 64
    implicitHeight: root.orientation === Qt.Vertical ? 64 : 12
    padding: 3
    stepSize: Math.max(0.01, Math.min(0.1, root.size / 4))
    hoverEnabled: true
    activeFocusOnTab: root.interactive && root.policy !== Controls.ScrollBar.AlwaysOff && root.size > 0 && root.size < 1
    Accessible.role: Accessible.ScrollBar
    Accessible.name: root.accessibleName
    Accessible.ignored: root.policy === Controls.ScrollBar.AlwaysOff || root.size <= 0 || root.size >= 1

    Keys.onPressed: event => {
        let handled = true;
        switch (event.key) {
        case Qt.Key_Up:
            if (root.orientation === Qt.Vertical)
                root.decrease();
            else
                handled = false;
            break;
        case Qt.Key_Down:
            if (root.orientation === Qt.Vertical)
                root.increase();
            else
                handled = false;
            break;
        case Qt.Key_Left:
            if (root.orientation === Qt.Horizontal) {
                if (root.mirrored)
                    root.increase();
                else
                    root.decrease();
            } else {
                handled = false;
            }
            break;
        case Qt.Key_Right:
            if (root.orientation === Qt.Horizontal) {
                if (root.mirrored)
                    root.decrease();
                else
                    root.increase();
            } else {
                handled = false;
            }
            break;
        case Qt.Key_PageUp:
            root.position = Math.max(0, root.position - Math.max(root.size, root.stepSize));
            break;
        case Qt.Key_PageDown:
            root.position = Math.min(1 - root.size, root.position + Math.max(root.size, root.stepSize));
            break;
        case Qt.Key_Home:
            root.position = 0;
            break;
        case Qt.Key_End:
            root.position = Math.max(0, 1 - root.size);
            break;
        default:
            handled = false;
        }
        event.accepted = handled;
    }

    contentItem: Rectangle {
        implicitWidth: root.orientation === Qt.Vertical ? 6 : 48
        implicitHeight: root.orientation === Qt.Vertical ? 48 : 6
        visible: root.policy === Controls.ScrollBar.AlwaysOn || root.size > 0 && root.size < 1
        radius: Math.min(width, height) / 2
        color: root.pressed ? Theme.accentPressed : root.hovered || root.activeFocus ? Theme.accent : Theme.textTertiary
        opacity: root.enabled ? root.active || root.hovered || root.activeFocus ? 1 : 0.66 : 0.3
        Accessible.ignored: true

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }

        Behavior on opacity {
            OpacityAnimator {
                duration: Motion.fast
            }
        }
    }

    background: Rectangle {
        visible: root.contentItem.visible
        color: root.hovered || root.pressed || root.activeFocus ? Theme.surfaceHover : 'transparent'
        radius: Theme.radiusSm
        border.width: root.activeFocus ? 1 : 0
        border.color: Theme.focusRing
        Accessible.ignored: true

        Behavior on color {
            ColorAnimation {
                duration: Motion.fast
            }
        }
    }
}
