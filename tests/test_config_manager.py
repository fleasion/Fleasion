import json
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANAGER_PATH = _REPO_ROOT / 'src' / 'fleasion' / 'config' / 'manager.py'

class ConfigManagerEncodingTests(unittest.TestCase):
    def _load_manager_for(self, root: Path):
        config_dir = root / 'FleasionNT'

        fleasion_pkg = types.ModuleType('fleasion')
        fleasion_pkg.__path__ = []
        config_pkg = types.ModuleType('fleasion.config')
        config_pkg.__path__ = []
        utils_pkg = types.ModuleType('fleasion.utils')
        utils_pkg.__path__ = []
        paths_module = types.ModuleType('fleasion.utils.paths')
        paths_module.CONFIG_DIR = config_dir
        paths_module.CONFIG_FILE = config_dir / 'settings.json'
        paths_module.CONFIGS_FOLDER = config_dir / 'configs'
        secure_tokens_module = types.ModuleType('fleasion.utils.secure_tokens')
        secure_tokens_module.decrypt_token = lambda value, _path: value.removeprefix('fernet:')
        secure_tokens_module.encrypt_token = lambda value, _path: f'fernet:{value}'

        module_name = 'fleasion.config.manager'
        spec = importlib.util.spec_from_file_location(module_name, _MANAGER_PATH)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                'fleasion': fleasion_pkg,
                'fleasion.config': config_pkg,
                'fleasion.utils': utils_pkg,
                'fleasion.utils.paths': paths_module,
                'fleasion.utils.secure_tokens': secure_tokens_module,
                module_name: module,
            },
        ):
            spec.loader.exec_module(module)

        return module

    def test_upstream_passwords_are_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()

            manager.upstream_http_connect_password = 'http-secret'
            manager.upstream_socks5_password = 'socks-secret'

            stored = json.loads((Path(tmp) / 'FleasionNT' / 'settings.json').read_text())
            self.assertEqual(stored['upstream_http_connect_password'], 'fernet:http-secret')
            self.assertEqual(stored['upstream_socks5_password'], 'fernet:socks-secret')
            self.assertEqual(manager.upstream_http_connect_password, 'http-secret')
            self.assertEqual(manager.upstream_socks5_password, 'socks-secret')

    def test_unicode_config_names_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()

            self.assertTrue(manager.create_config('日本語'))
            manager.last_config = '日本語'
            manager.replacement_rules = [
                {
                    'name': 'тест',
                    'replace_ids': ['123'],
                    'replace_with': '456',
                }
            ]

            reloaded = config_manager_module.ConfigManager()
            reloaded.last_config = '日本語'

            self.assertIn('日本語', reloaded.config_names)
            self.assertEqual(reloaded.replacement_rules[0]['name'], 'тест')

    def test_legacy_non_utf8_config_is_recovered_and_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            config_path = configs_dir / 'Default.json'
            config_path.write_bytes(
                b'{"replacement_rules":[{"name":"100\x89","replace_ids":[]}]}'
            )

            manager = config_manager_module.ConfigManager()

            self.assertEqual(manager.replacement_rules[0]['name'], '100‰')
            normalized = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual(normalized['replacement_rules'][0]['name'], '100‰')

    def test_invalid_config_bytes_do_not_crash_startup_or_dashboard_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            (configs_dir / 'Default.json').write_bytes(b'\x89PNG\r\n\x1a\nnot json')

            manager = config_manager_module.ConfigManager()

            self.assertEqual(manager.replacement_rules, [])

    def test_external_config_inspection_accepts_legacy_shapes_and_rejects_scalars(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            manager = config_manager_module.ConfigManager()

            root_list = configs_dir / 'root-list.txt'
            root_list.write_text(json.dumps([{'name': 'Rule'}]), encoding='utf-8')
            scalar = configs_dir / 'scalar.txt'
            scalar.write_text('1', encoding='utf-8')
            binary = configs_dir / 'binary.bin'
            binary.write_bytes(b'\x89PNG\r\n\x1a\n\x00binary')
            compressed = configs_dir / 'compressed.bin'
            compressed.write_bytes(b'\xff' * 64)

            self.assertEqual(manager.inspect_config_file(root_list).status, 'valid')
            self.assertEqual(manager.inspect_config_file(scalar).status, 'invalid')
            self.assertEqual(manager.inspect_config_file(binary).status, 'binary')
            self.assertEqual(manager.inspect_config_file(compressed).status, 'binary')

    def test_external_config_import_does_not_overwrite_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            manager = config_manager_module.ConfigManager()

            destination = configs_dir / 'Copied.json'
            destination.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
            source = configs_dir / 'Copied.txt'
            source.write_text(json.dumps({'replacement_rules': [{'name': 'new'}]}), encoding='utf-8')

            with self.assertRaises(FileExistsError):
                manager.import_config_file(source)

            self.assertTrue(source.exists())
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), {'replacement_rules': []})

    def test_large_list_root_config_is_loaded_as_replacement_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            rules = [
                {
                    'name': 'Large imported config',
                    'enabled': True,
                    'replace_ids': ['123'],
                    'mode': 'id',
                    'with_id': 456,
                    'notes': 'x' * (225 * 1024),
                }
            ]
            (configs_dir / 'Default.json').write_text(json.dumps(rules), encoding='utf-8')

            manager = config_manager_module.ConfigManager()

            self.assertEqual(manager.replacement_rules, rules)

    def test_config_with_non_list_replacement_rules_loads_empty_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            (configs_dir / 'Default.json').write_text(
                json.dumps({'replacement_rules': {'not': 'a list'}}),
                encoding='utf-8',
            )

            manager = config_manager_module.ConfigManager()

            self.assertEqual(manager.replacement_rules, [])

    def test_cached_config_refreshes_after_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            configs_dir.mkdir(parents=True)
            config_path = configs_dir / 'Default.json'
            config_path.write_text(
                json.dumps({'replacement_rules': [{'name': 'Old', 'replace_ids': ['1']}]}),
                encoding='utf-8',
            )

            manager = config_manager_module.ConfigManager()
            self.assertEqual(manager.replacement_rules[0]['name'], 'Old')

            config_path.write_text(
                json.dumps({'replacement_rules': [{'name': 'New', 'replace_ids': ['2', '3']}]}),
                encoding='utf-8',
            )

            self.assertEqual(manager.replacement_rules[0]['name'], 'New')
            self.assertEqual(manager.replacement_rules[0]['replace_ids'], ['2', '3'])

    def test_wire_preserving_passthrough_defaults_off_and_rejects_string_trueish_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            self.assertFalse(manager.wire_preserving_passthrough)

            manager.settings['wire_preserving_passthrough'] = 'false'
            self.assertFalse(manager.wire_preserving_passthrough)

            manager.settings['wire_preserving_passthrough'] = 'true'
            self.assertTrue(manager.wire_preserving_passthrough)

    def test_proxy_mode_defaults_to_env_and_accepts_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            self.assertEqual(manager.proxy_mode, 'env')

            manager.proxy_mode = 'hosts'
            self.assertEqual(manager.proxy_mode, 'hosts')

            manager.proxy_mode = 'invalid'
            self.assertEqual(manager.proxy_mode, 'env')

            self.assertFalse(manager.env_proxy_migration_v1_complete)
            manager.env_proxy_migration_v1_complete = True
            self.assertTrue(manager.env_proxy_migration_v1_complete)

            reloaded = config_manager_module.ConfigManager()
            self.assertEqual(reloaded.proxy_mode, 'env')
            self.assertTrue(reloaded.env_proxy_migration_v1_complete)

            self.assertFalse(manager.lock_roblox_files_read_only)
            manager.lock_roblox_files_read_only = True
            self.assertTrue(manager.lock_roblox_files_read_only)

            self.assertTrue(manager.close_env_proxy_roblox_on_exit)
            manager.close_env_proxy_roblox_on_exit = False
            self.assertFalse(manager.close_env_proxy_roblox_on_exit)

    def test_linux_client_selection_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            self.assertEqual(manager.linux_client, 'auto')

            manager.linux_client = 'Sober'
            self.assertEqual(manager.linux_client, 'sober')
            self.assertEqual(
                config_manager_module.ConfigManager().linux_client,
                'sober',
            )

            manager.linux_client = 'not-a-client'
            self.assertEqual(manager.linux_client, 'auto')

    def test_linux_client_selection_uses_live_registry_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            registry_module = types.ModuleType('fleasion.utils.linux_clients')
            registry_module.LINUX_CLIENTS_BY_KEY = {
                'sober': object(),
                'future-client': object(),
            }

            with patch.dict(
                sys.modules,
                {'fleasion.utils.linux_clients': registry_module},
            ):
                manager = config_manager_module.ConfigManager()
                manager.linux_client = 'Future-Client'
                self.assertEqual(manager.linux_client, 'future-client')

    def test_requested_defaults_for_boot_and_export_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()

            self.assertTrue(manager.run_on_boot)
            self.assertTrue(manager.desktop_integration)
            self.assertEqual(manager.export_naming, ['name', 'id'])

    def test_custom_fflags_are_risk_gated_disabled_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()

            self.assertFalse(manager.custom_fflags_enabled)
            self.assertFalse(manager.custom_fflags_warning_accepted)
            self.assertEqual(manager.custom_fflags, {})

            manager.custom_fflags = {
                'DFIntTaskSchedulerTargetFps': 20,
                'FFlagExample': True,
                'invalid': ['nested'],
            }
            manager.custom_fflags_warning_accepted = True
            manager.custom_fflags_enabled = True

            reloaded = config_manager_module.ConfigManager()
            self.assertTrue(reloaded.custom_fflags_enabled)
            self.assertTrue(reloaded.custom_fflags_warning_accepted)
            self.assertEqual(
                reloaded.custom_fflags,
                {
                    'DFIntTaskSchedulerTargetFps': '20',
                    'FFlagExample': 'True',
                },
            )

    def test_custom_fflag_windows_toggle_state_and_keybinds_are_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()

            manager.custom_fflag_disabled = [' FFlagExample ', '', 'FFlagExample']
            manager.custom_fflag_keybinds = {
                ' FFlagExample ': {'scan_code': 0x1E, 'extended': False, 'modifiers': 0},
                'CtrlOnly': {'scan_code': 0x1D, 'extended': False, 'modifiers': 0},
                'Invalid': {'scan_code': 0, 'extended': False, 'modifiers': 0},
                'BadModifier': {'scan_code': 0x30, 'extended': False, 'modifiers': 0x10},
            }

            reloaded = config_manager_module.ConfigManager()
            self.assertEqual(reloaded.custom_fflag_disabled, ['FFlagExample'])
            self.assertEqual(
                reloaded.custom_fflag_keybinds,
                {
                    'FFlagExample': {'scan_code': 0x1E, 'extended': False, 'modifiers': 0},
                    'CtrlOnly': {'scan_code': 0x1D, 'extended': False, 'modifiers': 0},
                },
            )

    def test_custom_fflag_mouse_bindings_are_preserved_per_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()
            manager.custom_fflag_keybinds = {
                'WindowsMouse4': {
                    'platform': 'windows', 'kind': 'mouse_button', 'scan_code': 5,
                    'extended': False, 'modifiers': 0,
                },
                'LinuxWheelDown': {
                    'platform': 'linux_evdev', 'kind': 'mouse_wheel',
                    'direction': 'down', 'modifiers': 0,
                },
            }

            self.assertEqual(
                manager.custom_fflag_keybinds,
                {
                    'WindowsMouse4': {
                        'platform': 'windows', 'kind': 'mouse_button', 'scan_code': 5,
                        'extended': False, 'modifiers': 0,
                    },
                    'LinuxWheelDown': {
                        'platform': 'linux_evdev', 'kind': 'mouse_wheel',
                        'direction': 'down', 'modifiers': 0,
                    },
                },
            )

    def test_dummy_replacement_ids_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {'name': 'Dummy zero', 'enabled': True, 'replace_ids': ['100'], 'mode': 'id', 'with_id': 0},
                {'name': 'Dummy one', 'enabled': True, 'replace_ids': ['101'], 'mode': 'id', 'with_id': 1},
                {'name': 'Real', 'enabled': True, 'replace_ids': ['102'], 'mode': 'id', 'with_id': 999},
            ]

            replacements, removals, cdn_replacements, local_replacements = manager.get_all_replacements()

            self.assertEqual(replacements, {102: 999})
            self.assertEqual(removals, set())
            self.assertEqual(cdn_replacements, {})
            self.assertEqual(local_replacements, {})

    def test_reserved_numeric_asset_type_range_is_not_treated_as_a_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'Crystal Scythe inspect',
                    'enabled': True,
                    'replace_ids': [14098254579, 1, '80', 81.9],
                    'mode': 'id',
                    'with_id': 94820576007871,
                }
            ]

            replacements, _, _, _ = manager.get_all_replacements()

            self.assertEqual(replacements, {14098254579: 94820576007871})

    def test_word_asset_types_keep_the_existing_wildcard_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'All images',
                    'enabled': True,
                    'replace_ids': ['Image'],
                    'mode': 'id',
                    'with_id': 999,
                }
            ]

            replacements, _, _, _ = manager.get_all_replacements()

            self.assertEqual(replacements, {1: 999})

    def test_reserved_numeric_replacement_target_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'Reserved target',
                    'enabled': True,
                    'replace_ids': [1000],
                    'mode': 'id',
                    'with_id': 80,
                }
            ]

            self.assertEqual(manager.get_all_replacements()[0], {})

    def test_portable_local_replacement_resolves_below_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            asset = configs_dir / 'StickObj' / 'stick.obj'
            asset.parent.mkdir()
            asset.write_text('stick', encoding='utf-8')

            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'Sticks',
                    'enabled': True,
                    'replace_ids': ['100'],
                    'mode': 'local',
                    'local_path': '/StickObj/stick.obj',
                }
            ]

            _, _, _, local_replacements = manager.get_all_replacements()

            self.assertEqual(local_replacements, {100: str(asset)})

    @unittest.skipIf(sys.platform == 'win32', 'POSIX portable-path fixture')
    def test_configs_asset_takes_priority_then_falls_back_to_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            absolute_asset = Path(tmp) / 'outside' / 'stick.obj'
            absolute_asset.parent.mkdir()
            absolute_asset.write_text('absolute', encoding='utf-8')
            portable_value = absolute_asset.as_posix()
            configs_asset = configs_dir.joinpath(*portable_value[1:].split('/'))
            configs_asset.parent.mkdir(parents=True)
            configs_asset.write_text('configs', encoding='utf-8')

            self.assertEqual(
                config_manager_module.resolve_local_replacement_path(portable_value),
                configs_asset,
            )

            configs_asset.unlink()
            self.assertEqual(
                config_manager_module.resolve_local_replacement_path(portable_value),
                absolute_asset,
            )

    @unittest.skipIf(sys.platform == 'win32', 'POSIX portable-path fixture')
    def test_invalidated_replacements_notice_when_priority_configs_asset_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            absolute_asset = Path(tmp) / 'outside' / 'stick.obj'
            absolute_asset.parent.mkdir()
            absolute_asset.write_text('absolute', encoding='utf-8')
            portable_value = absolute_asset.as_posix()
            configs_asset = configs_dir.joinpath(*portable_value[1:].split('/'))

            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'Priority',
                    'enabled': True,
                    'replace_ids': ['100'],
                    'mode': 'local',
                    'local_path': portable_value,
                }
            ]

            self.assertEqual(manager.get_all_replacements()[3], {100: str(absolute_asset)})

            configs_asset.parent.mkdir(parents=True)
            configs_asset.write_text('configs', encoding='utf-8')
            manager.invalidate_replacements_cache()

            self.assertEqual(manager.get_all_replacements()[3], {100: str(configs_asset)})

    def test_replacement_cache_hit_does_not_stat_local_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            manager = config_manager_module.ConfigManager()
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            asset = configs_dir / 'StickObj' / 'stick.obj'
            asset.parent.mkdir()
            asset.write_text('stick', encoding='utf-8')
            manager.enabled_configs = ['Default']
            manager.replacement_rules = [
                {
                    'name': 'Sticks',
                    'enabled': True,
                    'replace_ids': ['100'],
                    'mode': 'local',
                    'local_path': '/StickObj/stick.obj',
                }
            ]
            manager.get_all_replacements()

            original_file_signature = manager._file_signature
            checked_paths = []

            def record_file_signature(path):
                checked_paths.append(Path(path))
                return original_file_signature(path)

            manager._file_signature = record_file_signature
            manager.get_all_replacements()

            self.assertEqual(checked_paths, [configs_dir / 'Default.json'])

    def test_portable_assets_allow_one_to_ten_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'

            ten_folders = [f'level-{index}' for index in range(10)]
            valid_asset = configs_dir.joinpath(*ten_folders, 'asset.obj')
            valid_asset.parent.mkdir(parents=True)
            valid_asset.write_text('valid', encoding='utf-8')
            valid_value = '/' + '/'.join((*ten_folders, 'asset.obj'))

            eleven_folders = [f'level-{index}' for index in range(11)]
            too_deep_asset = configs_dir.joinpath(*eleven_folders, 'asset.obj')
            too_deep_asset.parent.mkdir(parents=True)
            too_deep_asset.write_text('too deep', encoding='utf-8')
            too_deep_value = '/' + '/'.join((*eleven_folders, 'asset.obj'))

            self.assertEqual(
                config_manager_module.resolve_local_replacement_path(valid_value),
                valid_asset,
            )
            self.assertNotEqual(
                config_manager_module.resolve_local_replacement_path(too_deep_value),
                too_deep_asset,
            )

    def test_browsed_configs_asset_is_stored_portably(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            asset = configs_dir / 'Pack' / 'Models' / 'stick.obj'
            asset.parent.mkdir(parents=True)
            asset.write_text('stick', encoding='utf-8')

            self.assertEqual(
                config_manager_module.local_replacement_path_for_storage(asset),
                '/Pack/Models/stick.obj',
            )

    def test_nested_json_is_an_asset_not_a_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))
            configs_dir = Path(tmp) / 'FleasionNT' / 'configs'
            nested_config = configs_dir / 'Pack' / 'metadata.json'
            nested_config.parent.mkdir(parents=True)
            nested_config.write_text(
                json.dumps({'replacement_rules': [{'name': 'Nested'}]}),
                encoding='utf-8',
            )

            manager = config_manager_module.ConfigManager()

            self.assertEqual(manager.config_names, ['Default'])

    def test_macos_auth_source_accepts_only_supported_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            self.assertEqual(manager.macos_auth_source, '')

            manager.macos_auth_source = 'Chrome'
            self.assertEqual(manager.macos_auth_source, 'Chrome')

            manager.macos_auth_source = 'manual'
            self.assertEqual(manager.macos_auth_source, 'manual')

            manager.macos_auth_source = 'Internet Explorer'
            self.assertEqual(manager.macos_auth_source, '')


if __name__ == '__main__':
    unittest.main()
