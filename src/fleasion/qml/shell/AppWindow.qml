pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Fleasion.Theme
import Fleasion.Components
import "../screens" as Screens
import "../dialogs" as Dialogs

ApplicationWindow {
    id: root

    required property var appController
    property string currentPage: "replacer"
    readonly property bool compactNavigation: width < 960
    property bool cacheVisited: false
    property bool modificationsVisited: false
    property bool subplacesVisited: false
    property bool miscVisited: false
    property bool proxyVisited: false
    property bool logsVisited: false
    property bool settingsVisited: false
    property string migrationTitle: ""
    property string migrationMessage: ""
    property bool migrationCanApplyNow: false
    property string migrationAcceptText: ""
    property string migrationRejectText: ""
    property string authWarningTitle: ""
    property string authWarningMessage: ""
    property string authWarningDetail: ""
    property bool authWarningCanOpenLogin: false
    property string authWarningContinueText: ""
    property string authWarningLoginText: ""
    property string authWarningExitText: ""

    width: 1280
    height: 800
    minimumWidth: 720
    minimumHeight: 560
    visible: appController.showDashboardOnStart || appController.firstRun
    title: appController.appName
    flags: Qt.Window | (appController.settings.alwaysOnTop ? Qt.WindowStaysOnTopHint : 0)
    color: Theme.surface
    palette.window: Theme.surface
    palette.windowText: Theme.textPrimary
    palette.base: Theme.surface
    palette.text: Theme.textPrimary
    palette.button: Theme.surfaceElevated
    palette.buttonText: Theme.textPrimary
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.accentForeground
    LayoutMirroring.enabled: Qt.locale().textDirection === Qt.RightToLeft
    LayoutMirroring.childrenInherit: true

    function navigate(pageId) {
        const pages = ["replacer", "cache", "modifications", "subplaces", "misc", "proxy", "logs", "settings"];
        if (pages.indexOf(pageId) !== -1)
            root.currentPage = pageId;
    }

    function showDashboard() {
        show();
        raise();
        requestActivate();
    }

    function pageIndex(pageId) {
        return ["replacer", "cache", "modifications", "subplaces", "misc", "proxy", "logs", "settings"].indexOf(pageId);
    }

    function rememberPage(pageId) {
        switch (pageId) {
        case "cache":
            cacheVisited = true;
            break;
        case "modifications":
            modificationsVisited = true;
            break;
        case "subplaces":
            subplacesVisited = true;
            break;
        case "misc":
            miscVisited = true;
            break;
        case "proxy":
            proxyVisited = true;
            break;
        case "logs":
            logsVisited = true;
            break;
        case "settings":
            settingsVisited = true;
            break;
        }
    }

    function applyTheme(themeName) {
        const themes = {
            "Light": "light",
            "Dark": "dark"
        };
        Theme.colorScheme = themes[themeName] || "system";
    }

    function applyAppearance() {
        Theme.accentColor = appController.settings.accentColor;
        Theme.highContrast = appController.settings.highContrast;
        Theme.reducedMotion = appController.settings.reducedMotion;
    }

    Component.onCompleted: {
        root.applyTheme(appController.settings.theme);
        root.applyAppearance();
    }
    onCurrentPageChanged: rememberPage(currentPage)
    onClosing: close => {
        if (root.appController.settings.closeToTray && appTray.available) {
            close.accepted = false;
            root.hide();
        } else {
            close.accepted = false;
            root.appController.quitRequested();
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        NavigationRail {
            id: navigation

            Layout.fillHeight: true
            Layout.preferredWidth: preferredWidth
            compact: root.compactNavigation
            currentPage: root.currentPage
            onPageRequested: pageId => root.navigate(pageId)
            onAboutRequested: aboutLoader.active = true
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: Math.max(0, root.pageIndex(root.currentPage))

                Loader {
                    objectName: "pageLoader-replacer"
                    active: true
                    sourceComponent: replacerPage
                }

                Loader {
                    objectName: "pageLoader-cache"
                    active: root.currentPage === "cache" || root.cacheVisited
                    sourceComponent: cachePage
                }

                Loader {
                    objectName: "pageLoader-modifications"
                    active: root.currentPage === "modifications" || root.modificationsVisited
                    sourceComponent: modificationsPage
                }

                Loader {
                    objectName: "pageLoader-subplaces"
                    active: root.currentPage === "subplaces" || root.subplacesVisited
                    sourceComponent: subplacesPage
                }

                Loader {
                    objectName: "pageLoader-misc"
                    active: root.currentPage === "misc" || root.miscVisited
                    sourceComponent: miscPage
                }

                Loader {
                    objectName: "pageLoader-proxy"
                    active: root.currentPage === "proxy" || root.proxyVisited
                    sourceComponent: proxyPage
                }

                Loader {
                    objectName: "pageLoader-logs"
                    active: root.currentPage === "logs" || root.logsVisited
                    sourceComponent: logsPage
                }

                Loader {
                    objectName: "pageLoader-settings"
                    active: root.currentPage === "settings" || root.settingsVisited
                    sourceComponent: settingsPage
                }
            }

            AppStatusBar {
                Layout.fillWidth: true
                appController: root.appController
            }
        }
    }

    ToastHost {
        id: toastHost

        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: Theme.spaceLg
        anchors.bottomMargin: Theme.spaceXxl
    }

    AppTray {
        id: appTray

        appController: root.appController
        onDashboardRequested: root.showDashboard()
        onAboutRequested: {
            root.showDashboard();
            aboutLoader.active = true;
        }
    }

    Dialogs.UpdateCoordinator {
        anchors.fill: parent
        controller: root.appController.updates
    }

    Dialogs.StartupRepairCoordinator {
        anchors.fill: parent
        controller: root.appController.startupRepair
        appController: root.appController
    }

    Loader {
        id: migrationLoader

        active: false
        sourceComponent: root.migrationCanApplyNow ? migrationChoiceComponent : migrationNoticeComponent
    }

    Component {
        id: migrationNoticeComponent

        Dialogs.NotificationDialog {
            parent: Overlay.overlay
            anchors.centerIn: parent
            heading: root.migrationTitle
            message: root.migrationMessage
            closePolicy: Popup.NoAutoClose
            Component.onCompleted: open()
            onAccepted: root.appController.acknowledgeEnvProxyMigration(false)
            onClosed: migrationLoader.active = false
        }
    }

    Component {
        id: migrationChoiceComponent

        Dialogs.ConfirmDialog {
            parent: Overlay.overlay
            anchors.centerIn: parent
            heading: root.migrationTitle
            message: root.migrationMessage
            acceptText: root.migrationAcceptText
            rejectText: root.migrationRejectText
            closePolicy: Popup.NoAutoClose
            Component.onCompleted: open()
            onConfirmed: root.appController.acknowledgeEnvProxyMigration(true)
            onRejected: root.appController.acknowledgeEnvProxyMigration(false)
            onClosed: migrationLoader.active = false
        }
    }

    Loader {
        id: authWarningLoader

        active: false
        sourceComponent: Component {
            Dialogs.AuthWarningDialog {
                parent: Overlay.overlay
                anchors.centerIn: parent
                heading: root.authWarningTitle
                message: root.authWarningMessage
                detail: root.authWarningDetail
                canOpenLogin: root.authWarningCanOpenLogin
                continueText: root.authWarningContinueText
                loginText: root.authWarningLoginText
                exitText: root.authWarningExitText
                onContinueRequested: {
                    close();
                    authWarningLoader.active = false;
                }
                onLoginRequested: {
                    if (root.appController.settings.supportsBrowserAuthSource)
                        root.navigate("settings");
                    else
                        root.appController.openRobloxLogin();
                    close();
                    authWarningLoader.active = false;
                }
                onExitRequested: root.appController.quitRequested()
                onLinkRequested: url => root.appController.openUrl(url)
                Component.onCompleted: open()
                onClosed: authWarningLoader.active = false
            }
        }
    }

    Loader {
        id: aboutLoader

        active: false
        sourceComponent: Component {
            Dialogs.AboutDialog {
                appController: root.appController
                onClosed: aboutLoader.active = false
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.AboutDialog).open();
        }
    }

    Loader {
        id: welcomeLoader

        active: root.appController.firstRun
        sourceComponent: Component {
            Dialogs.WelcomeDialog {
                appController: root.appController
                onFinished: {
                    root.appController.completeFirstRun();
                    root.showDashboard();
                }
            }
        }
        onLoaded: {
            if (status === Loader.Ready)
                (item as Dialogs.WelcomeDialog).open();
        }
    }

    Component {
        id: replacerPage
        Screens.ReplacerPage {
            controller: root.appController.replacer
            appController: root.appController
        }
    }

    Component {
        id: cachePage
        Screens.CachePage {
            controller: root.appController.cache
            appController: root.appController
        }
    }

    Component {
        id: modificationsPage
        Screens.ModificationsPage {
            controller: root.appController.modifications
            appController: root.appController
        }
    }

    Component {
        id: subplacesPage
        Screens.SubplacesPage {
            controller: root.appController.subplaces
            appController: root.appController
        }
    }

    Component {
        id: miscPage
        Screens.MiscPage {
            controller: root.appController.utilities
            appController: root.appController
        }
    }

    Component {
        id: proxyPage
        Screens.ProxyPage {
            controller: root.appController.proxy
            appController: root.appController
        }
    }

    Component {
        id: logsPage
        Screens.LogsPage {
            controller: root.appController.logs
            appController: root.appController
        }
    }

    Component {
        id: settingsPage
        Screens.SettingsPage {
            controller: root.appController.settings
            appController: root.appController
        }
    }

    Connections {
        target: root.appController

        function onPageRequested(pageId) {
            root.navigate(pageId);
        }

        function onNotificationRequested(title, message, severity) {
            toastHost.show(qsTr("%1: %2").arg(title).arg(message), severity);
        }

        function onErrorOccurred(message) {
            toastHost.error(qsTr("Something went wrong: %1").arg(message));
        }

        function onEnvProxyMigrationRequested(title, message, canApplyNow, acceptText, rejectText) {
            root.migrationTitle = title;
            root.migrationMessage = message;
            root.migrationCanApplyNow = canApplyNow;
            root.migrationAcceptText = acceptText;
            root.migrationRejectText = rejectText;
            migrationLoader.active = true;
        }

        function onAuthWarningRequested(title, message, detail, canOpenLogin, continueText, loginText, exitText) {
            root.authWarningTitle = title;
            root.authWarningMessage = message;
            root.authWarningDetail = detail;
            root.authWarningCanOpenLogin = canOpenLogin;
            root.authWarningContinueText = continueText;
            root.authWarningLoginText = loginText;
            root.authWarningExitText = exitText;
            authWarningLoader.active = true;
        }

        function onDashboardVisibilityRequested(visible) {
            if (visible)
                root.showDashboard();
            else
                root.hide();
        }
    }

    Connections {
        target: root.appController.settings

        function onThemeChanged() {
            root.applyTheme(root.appController.settings.theme);
        }

        function onAppearanceChanged() {
            root.applyAppearance();
        }
    }

    Shortcut {
        sequence: "Ctrl+1"
        onActivated: root.navigate("replacer")
    }
    Shortcut {
        sequence: "Ctrl+2"
        onActivated: root.navigate("cache")
    }
    Shortcut {
        sequence: "Ctrl+3"
        onActivated: root.navigate("modifications")
    }
    Shortcut {
        sequence: "Ctrl+,"
        onActivated: root.navigate("settings")
    }
}
