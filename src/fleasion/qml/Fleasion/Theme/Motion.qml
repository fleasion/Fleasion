pragma Singleton
import QtQuick

QtObject {
    readonly property bool reducedMotion: Theme.reducedMotion
    readonly property int fast: reducedMotion ? 0 : 110
    readonly property int normal: reducedMotion ? 0 : 190
    readonly property int slow: reducedMotion ? 0 : 280
    readonly property int enterEasing: Easing.OutCubic
    readonly property int exitEasing: Easing.InCubic
    readonly property int emphasizedEasing: Easing.OutBack
}
