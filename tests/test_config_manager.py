import json
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANAGER_PATH = _REPO_ROOT / 'src' / 'Fleasion' / 'config' / 'manager.py'

_DEFAULT_SETTINGS = {
    'strip_textures': False,
    'enabled_configs': [],
    'last_config': 'Default',
    'theme': 'System',
    'wire_preserving_passthrough': False,
    'run_on_boot': True,
    'export_naming': ['name', 'id'],
}


class ConfigManagerEncodingTests(unittest.TestCase):
    def _load_manager_for(self, root: Path):
        config_dir = root / 'FleasionNT'

        fleasion_pkg = types.ModuleType('Fleasion')
        fleasion_pkg.__path__ = []
        config_pkg = types.ModuleType('Fleasion.config')
        config_pkg.__path__ = []
        utils_pkg = types.ModuleType('Fleasion.utils')
        utils_pkg.__path__ = []
        paths_module = types.ModuleType('Fleasion.utils.paths')
        paths_module.CONFIG_DIR = config_dir
        paths_module.CONFIG_FILE = config_dir / 'settings.json'
        paths_module.CONFIGS_FOLDER = config_dir / 'configs'
        paths_module.DEFAULT_SETTINGS = _DEFAULT_SETTINGS

        module_name = 'Fleasion.config.manager'
        spec = importlib.util.spec_from_file_location(module_name, _MANAGER_PATH)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                'Fleasion': fleasion_pkg,
                'Fleasion.config': config_pkg,
                'Fleasion.utils': utils_pkg,
                'Fleasion.utils.paths': paths_module,
                module_name: module,
            },
        ):
            spec.loader.exec_module(module)

        return module

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

    def test_wire_preserving_passthrough_defaults_off_and_rejects_string_trueish_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()
            self.assertFalse(manager.wire_preserving_passthrough)

            manager.settings['wire_preserving_passthrough'] = 'false'
            self.assertFalse(manager.wire_preserving_passthrough)

            manager.settings['wire_preserving_passthrough'] = 'true'
            self.assertTrue(manager.wire_preserving_passthrough)

    def test_requested_defaults_for_boot_and_export_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_manager_module = self._load_manager_for(Path(tmp))

            manager = config_manager_module.ConfigManager()

            self.assertTrue(manager.run_on_boot)
            self.assertEqual(manager.export_naming, ['name', 'id'])

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
