"""Settings tab – mirrors all settings available in the system tray menu."""

import ctypes

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..gui.theme import ThemeManager
from .modifications_tab import CollapsibleSection
from ..utils.autostart import sync_autostart
from ..utils import CONFIG_DIR


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class SettingsTab(QWidget):
    """Settings tab exposing all options found in the system tray Settings menu."""

    def __init__(self, config_manager, system_tray=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._tray = system_tray
        self._setup_ui()

    # UI construction

    def _setup_ui(self):
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        container.setObjectName('_FleasionSettingsContainer')
        self._settings_container = container
        self._container_layout = QVBoxLayout()
        self._container_layout.setSpacing(10)
        self._container_layout.setContentsMargins(10, 10, 10, 10)

        self._container_layout.addWidget(self._build_appearance_section())
        self._container_layout.addWidget(self._build_convenience_section())
        self._container_layout.addWidget(self._build_scraper_section())
        self._container_layout.addWidget(self._build_scraped_games_section())
        self._container_layout.addWidget(self._build_export_section())
        self._container_layout.addStretch()

        container.setLayout(self._container_layout)

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._footer_widget = footer_widget
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        self._status_label = QLabel('')
        self._status_label.setStyleSheet('color: #888;')
        footer_layout.addWidget(self._status_label)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton('Clear Cache')
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)

        scroll.setWidget(container)
        outer.addWidget(scroll)
        outer.addWidget(footer_widget)
        self.setLayout(outer)
        self._update_container_bg()

    def changeEvent(self, a0: QEvent | None):
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.PaletteChange:
            self._update_container_bg()

    # Appearance

    def _build_appearance_section(self) -> CollapsibleSection:
        section = CollapsibleSection('Appearance', expanded=True)

        self._theme_buttons: dict[str, QCheckBox] = {}
        btn_group = QButtonGroup(self)
        btn_group.setExclusive(True)
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        current_theme = self._config.theme
        for name in ('System', 'Light', 'Dark'):
            chk = QCheckBox(name)
            chk.setChecked(name == current_theme)
            chk.toggled.connect(lambda checked, t=name: self._on_theme_toggled(checked, t))
            btn_group.addButton(chk)
            theme_row.addWidget(chk)
            self._theme_buttons[name] = chk
        theme_row.addStretch()

        row_widget = QWidget()
        row_widget.setLayout(theme_row)
        section.add_widget(row_widget)

        return section

    # Startup

    def _build_convenience_section(self) -> CollapsibleSection:
        section = CollapsibleSection('Convenience', expanded=True)

        admin = _is_admin()

        self._open_dashboard_chk = QCheckBox("Open Dashboard on Start")
        self._open_dashboard_chk.setChecked(self._config.open_dashboard_on_launch)
        self._open_dashboard_chk.toggled.connect(self._on_open_dashboard_toggled)
        section.add_widget(self._open_dashboard_chk)

        self._auto_clear_cache_chk = QCheckBox("Auto-Clear Cache on Exit")
        self._auto_clear_cache_chk.setChecked(self._config.auto_delete_cache_on_exit)
        self._auto_clear_cache_chk.toggled.connect(self._on_auto_clear_cache_toggled)
        section.add_widget(self._auto_clear_cache_chk)

        self._clear_cache_launch_chk = QCheckBox("Clear Cache on Launch")
        self._clear_cache_launch_chk.setChecked(self._config.clear_cache_on_launch)
        self._clear_cache_launch_chk.toggled.connect(self._on_clear_cache_launch_toggled)
        section.add_widget(self._clear_cache_launch_chk)

        boot_label = "Run on Boot" if admin else "Run on Boot  (requires administrator)"
        self._run_on_boot_chk = QCheckBox(boot_label)
        self._run_on_boot_chk.setChecked(self._config.run_on_boot)
        self._run_on_boot_chk.setEnabled(admin)
        self._run_on_boot_chk.toggled.connect(self._on_run_on_boot_toggled)
        section.add_widget(self._run_on_boot_chk)

        self._close_scraped_games_chk = QCheckBox("Close Roblox on Open")
        self._close_scraped_games_chk.setChecked(self._config.close_scraped_games_on_open)
        self._close_scraped_games_chk.toggled.connect(self._on_close_scraped_games_toggled)
        section.add_widget(self._close_scraped_games_chk)

        self._close_to_tray_chk = QCheckBox("Close to Tray")
        self._close_to_tray_chk.setChecked(self._config.close_to_tray)
        self._close_to_tray_chk.toggled.connect(self._on_close_to_tray_toggled)
        section.add_widget(self._close_to_tray_chk)

        self._always_on_top_chk = QCheckBox("Always on Top")
        self._always_on_top_chk.setChecked(self._config.always_on_top)
        self._always_on_top_chk.toggled.connect(self._on_always_on_top_toggled)
        section.add_widget(self._always_on_top_chk)

        return section

    def _build_scraper_section(self) -> CollapsibleSection:
        section = CollapsibleSection('Scraper', expanded=True)

        self._cache_scraper_chk = QCheckBox("Enable Cache Scraper")
        self._cache_scraper_chk.setChecked(self._is_cache_scraper_enabled())
        self._cache_scraper_chk.toggled.connect(self._on_cache_scraper_toggled)
        section.add_widget(self._cache_scraper_chk)

        self._show_names_chk = QCheckBox("Show Names")
        self._show_names_chk.setChecked(self._config.show_names)
        self._show_names_chk.toggled.connect(self._on_show_names_toggled)
        section.add_widget(self._show_names_chk)

        self._show_creator_id_chk = QCheckBox("Show User ID")
        self._show_creator_id_chk.setChecked(self._config.show_creator_id)
        self._show_creator_id_chk.toggled.connect(self._on_show_creator_id_toggled)
        section.add_widget(self._show_creator_id_chk)

        return section

    # Export naming

    def _build_export_section(self) -> CollapsibleSection:
        section = CollapsibleSection('Export Naming', expanded=True)

        self._export_chks: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for option in ("name", "id", "hash"):
            chk = QCheckBox(option.capitalize())
            chk.setChecked(self._config.is_export_naming_enabled(option))
            chk.toggled.connect(lambda checked, opt=option: self._on_export_naming_toggled(checked, opt))
            row.addWidget(chk)
            self._export_chks[option] = chk
        row.addStretch()
        row_widget = QWidget()
        row_widget.setLayout(row)
        section.add_widget(row_widget)

        return section

    def _build_scraped_games_section(self) -> CollapsibleSection:
        section = CollapsibleSection('Scraped Games', expanded=True)

        self._show_replacer_notifications_chk = QCheckBox("Show Replacer Notifications")
        self._show_replacer_notifications_chk.setChecked(self._config.show_replacer_notifications)
        self._show_replacer_notifications_chk.toggled.connect(self._on_show_replacer_notifications_toggled)
        section.add_widget(self._show_replacer_notifications_chk)

        self._close_viewer_on_replace_chk = QCheckBox("Close Viewer on Replace")
        self._close_viewer_on_replace_chk.setChecked(self._config.close_viewer_on_replace)
        self._close_viewer_on_replace_chk.toggled.connect(self._on_close_viewer_on_replace_toggled)
        section.add_widget(self._close_viewer_on_replace_chk)

        self._close_scraped_games_menu_on_open_chk = QCheckBox("Close Scraped Games Menu on Open")
        self._close_scraped_games_menu_on_open_chk.setChecked(self._config.close_scraped_games_menu_on_open)
        self._close_scraped_games_menu_on_open_chk.toggled.connect(self._on_close_scraped_games_menu_on_open_toggled)
        section.add_widget(self._close_scraped_games_menu_on_open_chk)

        return section

    def _update_container_bg(self):
        """Keep the Settings tab background aligned with the tab theme."""
        pal = self.palette()
        win_light = pal.window().color().lightness()
        alt_light = pal.alternateBase().color().lightness()
        if win_light < 128 and alt_light <= win_light:
            bg = 'background-color: rgb(64, 64, 64);'
        else:
            bg = 'background-color: palette(alternate-base);'
        self._settings_container.setStyleSheet(
            f'QWidget#_FleasionSettingsContainer {{ {bg} }}'
        )
        footer_widget = getattr(self, '_footer_widget', None)
        if footer_widget is not None:
            footer_widget.setStyleSheet(bg)

    # Public

    def refresh_from_config(self):
        """Re-read all settings from config and update widgets (no signals emitted)."""
        for name, rb in self._theme_buttons.items():
            rb.blockSignals(True)
            rb.setChecked(name == self._config.theme)
            rb.blockSignals(False)

        for chk, value in [
            (self._open_dashboard_chk, self._config.open_dashboard_on_launch),
            (self._auto_clear_cache_chk, self._config.auto_delete_cache_on_exit),
            (self._clear_cache_launch_chk, self._config.clear_cache_on_launch),
            (self._run_on_boot_chk, self._config.run_on_boot),
            (self._close_scraped_games_chk, self._config.close_scraped_games_on_open),
            (self._close_to_tray_chk, self._config.close_to_tray),
            (self._always_on_top_chk, self._config.always_on_top),
            (self._show_replacer_notifications_chk, self._config.show_replacer_notifications),
            (self._close_viewer_on_replace_chk, self._config.close_viewer_on_replace),
            (self._close_scraped_games_menu_on_open_chk, self._config.close_scraped_games_menu_on_open),
            (self._show_names_chk, self._config.show_names),
            (self._show_creator_id_chk, self._config.show_creator_id),
        ]:
            chk.blockSignals(True)
            chk.setChecked(value)
            chk.blockSignals(False)

        self.set_cache_scraper_enabled(self._is_cache_scraper_enabled())

        for option, chk in self._export_chks.items():
            chk.blockSignals(True)
            chk.setChecked(self._config.is_export_naming_enabled(option))
            chk.blockSignals(False)

    # Handlers

    def _clear_roblox_cache(self):
        from .delete_cache import DeleteCacheWindow
        window = DeleteCacheWindow()
        window.show()

    def _on_theme_toggled(self, checked: bool, theme: str):
        if not checked:
            return
        ThemeManager.apply_theme(theme)
        self._config.theme = theme
        if self._tray and hasattr(self._tray, 'theme_actions'):
            for name, action in self._tray.theme_actions.items():
                action.setChecked(name == theme)

    def _on_open_dashboard_toggled(self, checked: bool):
        self._config.open_dashboard_on_launch = checked
        if self._tray and hasattr(self._tray, 'open_dashboard_action'):
            self._tray.open_dashboard_action.setChecked(checked)

    def _on_run_on_boot_toggled(self, checked: bool):
        if not _is_admin():
            self._run_on_boot_chk.blockSignals(True)
            self._run_on_boot_chk.setChecked(not checked)
            self._run_on_boot_chk.blockSignals(False)
            return

        ok = sync_autostart(checked, CONFIG_DIR)
        if ok:
            self._config.run_on_boot = checked
            if self._tray and hasattr(self._tray, 'run_on_boot_action'):
                self._tray.run_on_boot_action.setChecked(checked)
        else:
            self._run_on_boot_chk.blockSignals(True)
            self._run_on_boot_chk.setChecked(not checked)
            self._run_on_boot_chk.blockSignals(False)
            QMessageBox.warning(
                self,
                'Run on Boot Failed',
                'Failed to register the autostart task.\n'
                'Check the application log for details.\n\n'
                'Ensure Fleasion is running as Administrator.',
            )

    def _on_always_on_top_toggled(self, checked: bool):
        self._config.always_on_top = checked
        if self._tray and hasattr(self._tray, 'always_on_top_action'):
            self._tray.always_on_top_action.setChecked(checked)
        if self._tray and hasattr(self._tray, 'open_windows'):
            for window in self._tray.open_windows:
                if window.isVisible():
                    flags = window.windowFlags()
                    if checked:
                        flags |= Qt.WindowType.WindowStaysOnTopHint
                    else:
                        flags &= ~Qt.WindowType.WindowStaysOnTopHint
                    window.setWindowFlags(flags)
                    window.show()

    def _on_close_to_tray_toggled(self, checked: bool):
        self._config.close_to_tray = checked
        if self._tray and hasattr(self._tray, 'close_to_tray_action'):
            self._tray.close_to_tray_action.setChecked(checked)

    def _on_auto_clear_cache_toggled(self, checked: bool):
        self._config.auto_delete_cache_on_exit = checked
        if self._tray and hasattr(self._tray, 'auto_delete_cache_action'):
            self._tray.auto_delete_cache_action.setChecked(checked)

    def _on_clear_cache_launch_toggled(self, checked: bool):
        self._config.clear_cache_on_launch = checked
        if self._tray and hasattr(self._tray, 'clear_cache_action'):
            self._tray.clear_cache_action.setChecked(checked)

    def _on_close_scraped_games_toggled(self, checked: bool):
        self._config.close_scraped_games_on_open = checked
        if self._tray and hasattr(self._tray, 'close_scraped_games_action'):
            self._tray.close_scraped_games_action.setChecked(checked)

    def _on_close_viewer_on_replace_toggled(self, checked: bool):
        self._config.close_viewer_on_replace = checked
        if self._tray and hasattr(self._tray, 'close_viewer_on_replace_action'):
            self._tray.close_viewer_on_replace_action.setChecked(checked)

    def _on_close_scraped_games_menu_on_open_toggled(self, checked: bool):
        self._config.close_scraped_games_menu_on_open = checked
        if self._tray and hasattr(self._tray, 'close_scraped_games_menu_on_open_action'):
            self._tray.close_scraped_games_menu_on_open_action.setChecked(checked)

    def _on_show_replacer_notifications_toggled(self, checked: bool):
        self._config.show_replacer_notifications = checked
        if self._tray and hasattr(self._tray, 'show_replacer_notifications_action'):
            self._tray.show_replacer_notifications_action.setChecked(checked)

    def _on_show_names_toggled(self, checked: bool):
        self._config.show_names = checked
        if self._tray and hasattr(self._tray, 'show_names_action'):
            self._tray.show_names_action.setChecked(checked)
        self._apply_to_cache_viewer('show_names', checked)

    def _on_show_creator_id_toggled(self, checked: bool):
        self._config.show_creator_id = checked
        if self._tray and hasattr(self._tray, 'show_creator_id_action'):
            self._tray.show_creator_id_action.setChecked(checked)
        self._apply_to_cache_viewer('show_creator_id', checked)

    def _apply_to_cache_viewer(self, setting: str, value: bool):
        if self._tray and self._tray.dashboard_window:
            tab = getattr(self._tray.dashboard_window, '_cache_viewer_tab', None)
            if tab is not None:
                if setting == 'show_names':
                    tab._on_show_names_toggled(value)
                elif setting == 'show_creator_id':
                    tab._on_show_creator_id_toggled(value)

    def _is_cache_scraper_enabled(self) -> bool:
        if self._tray and hasattr(self._tray, '_is_cache_scraper_enabled'):
            return self._tray._is_cache_scraper_enabled()
        return False

    def set_cache_scraper_enabled(self, enabled: bool):
        self._cache_scraper_chk.blockSignals(True)
        self._cache_scraper_chk.setChecked(enabled)
        self._cache_scraper_chk.blockSignals(False)

    def _on_cache_scraper_toggled(self, checked: bool):
        if self._tray and hasattr(self._tray, '_set_cache_scraper_enabled'):
            self._tray._set_cache_scraper_enabled(checked)

    def _on_export_naming_toggled(self, checked: bool, option: str):
        current = self._config.is_export_naming_enabled(option)
        if current != checked:
            new_state = self._config.toggle_export_naming(option)
            self._export_chks[option].blockSignals(True)
            self._export_chks[option].setChecked(new_state)
            self._export_chks[option].blockSignals(False)
        if self._tray and hasattr(self._tray, 'export_naming_actions'):
            self._tray.export_naming_actions[option].setChecked(
                self._config.is_export_naming_enabled(option)
            )
