pragma Singleton
import QtQuick

QtObject {
    id: theme

    property string colorScheme: 'system'
    property color accentColor: '#5b4cf0'
    property bool highContrast: false
    property bool reducedMotion: false
    readonly property string normalizedColorScheme: colorScheme.toLowerCase()
    readonly property bool systemIsDark: Application.styleHints.colorScheme === Qt.Dark
    readonly property bool isDark: normalizedColorScheme === 'dark' || (normalizedColorScheme === 'system' && systemIsDark)
    readonly property color window: highContrast ? (isDark ? '#000000' : '#ffffff') : (isDark ? '#15151a' : '#f3f3f3')
    readonly property color surface: highContrast ? (isDark ? '#080808' : '#ffffff') : (isDark ? '#1d1d24' : '#fbfbfb')
    readonly property color surfaceElevated: isDark ? '#25252e' : '#ffffff'
    readonly property color surfaceSubtle: isDark ? '#202028' : '#f7f7f8'
    readonly property color surfaceHover: isDark ? '#30303a' : '#f0f0f3'
    readonly property color surfacePressed: isDark ? '#393943' : '#e7e7ec'
    readonly property color border: highContrast ? (isDark ? '#ffffff' : '#000000') : (isDark ? '#3b3b46' : '#dddde4')
    readonly property color borderStrong: highContrast ? (isDark ? '#ffffff' : '#000000') : (isDark ? '#565665' : '#b9b9c4')
    readonly property color textPrimary: isDark ? '#f5f5f7' : '#1a1a1f'
    readonly property color textSecondary: isDark ? '#b7b7c1' : '#555560'
    readonly property color textTertiary: isDark ? '#8e8e9a' : '#747480'
    readonly property color textDisabled: isDark ? '#686875' : '#9898a3'
    readonly property color accent: accentColor
    readonly property color accentHover: isDark ? Qt.lighter(accentColor, 1.12) : Qt.darker(accentColor, 1.12)
    readonly property color accentPressed: isDark ? Qt.lighter(accentColor, 1.24) : Qt.darker(accentColor, 1.24)
    readonly property color accentSubtle: isDark ? '#302b5d' : '#ece9ff'
    readonly property color accentForeground: '#ffffff'
    readonly property color success: isDark ? '#6ccb91' : '#107c41'
    readonly property color successSubtle: isDark ? '#183c2a' : '#e4f4ea'
    readonly property color warning: isDark ? '#f2c94c' : '#8a6100'
    readonly property color warningSubtle: isDark ? '#453711' : '#fff4ce'
    readonly property color danger: isDark ? '#ff8a80' : '#c42b1c'
    readonly property color dangerSubtle: isDark ? '#4a211f' : '#fde7e9'
    readonly property color info: isDark ? '#75b6ff' : '#0067c0'
    readonly property color infoSubtle: isDark ? '#173653' : '#e5f1fb'
    readonly property color focusRing: isDark ? '#b9adff' : '#4f3fe1'
    readonly property color overlay: isDark ? '#99000000' : '#66000000'
    readonly property color shadow: isDark ? '#80000000' : '#26000000'
    readonly property int spaceXxs: 4
    readonly property int spaceXs: 8
    readonly property int spaceSm: 12
    readonly property int spaceMd: 16
    readonly property int spaceLg: 24
    readonly property int spaceXl: 32
    readonly property int spaceXxl: 48
    readonly property int pageGutter: 18
    readonly property int pageTopGutter: 8
    readonly property int pageBottomGutter: 12
    readonly property int sectionGap: 12
    readonly property int panelPadding: 14
    readonly property int radiusSm: 6
    readonly property int radiusMd: 10
    readonly property int radiusLg: 14
    readonly property int radiusXl: 20
    readonly property int radiusPill: 999
    readonly property int controlHeight: 38
    readonly property int largeControlHeight: 44
    readonly property int navigationWidth: 232
    readonly property int pageMaxWidth: 1200
    readonly property int minimumTouchTarget: 44
}
