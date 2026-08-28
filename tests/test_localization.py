import ast
import json
import re
import string
import unicodedata
from pathlib import Path

from fleasion import localization
from fleasion.config import manager as manager_module
from fleasion.translations.de import GERMAN
from fleasion.translations.es import SPANISH
from fleasion.translations.fr import FRENCH
from fleasion.translations.kk import KAZAKH
from fleasion.translations.pl import POLISH
from fleasion.translations.pt import PORTUGUESE
from fleasion.translations.ru import RUSSIAN
from fleasion.translations.tr import TURKISH
from fleasion.translations.zh import CHINESE


_TRANSLATED_CATALOGS = {
    'es': SPANISH,
    'pt': PORTUGUESE,
    'ru': RUSSIAN,
    'kk': KAZAKH,
    'tr': TURKISH,
    'de': GERMAN,
    'fr': FRENCH,
    'zh': CHINESE,
    'pl': POLISH,
}


def test_english_is_default_and_supported_languages_are_available():
    assert localization.available_languages() == (
        ('en', 'English'),
        ('es', 'Español'),
        ('pt', 'Português (Brasil)'),
        ('ru', 'Русский'),
        ('kk', 'Қазақша'),
        ('tr', 'Türkçe'),
        ('de', 'Deutsch'),
        ('fr', 'Français'),
        ('zh', '简体中文'),
        ('pl', 'Polski'),
    )
    assert localization.normalize_language(None) == 'en'
    assert localization.normalize_language('en-US') == 'en'
    assert localization.normalize_language('es-MX') == 'es'
    assert localization.normalize_language('es_ES') == 'es'
    assert localization.normalize_language('pt') == 'pt'
    assert localization.normalize_language('pt-BR') == 'pt'
    assert localization.normalize_language('pt_PT') == 'en'
    assert localization.normalize_language('ru-RU') == 'ru'
    assert localization.normalize_language('kk-KZ') == 'kk'
    assert localization.normalize_language('tr-TR') == 'tr'
    assert localization.normalize_language('de-DE') == 'de'
    assert localization.normalize_language('fr-FR') == 'fr'
    assert localization.normalize_language('pl-PL') == 'pl'
    assert localization.normalize_language('zh-CN') == 'zh'
    assert localization.normalize_language('zh_Hans') == 'zh'
    assert localization.normalize_language('zh-TW') == 'en'
    assert localization.normalize_language('not-a-language') == 'en'


def test_translation_lookup_formats_and_falls_back_to_english():
    localization.set_language('not-a-language')
    assert localization.get_language() == 'en'
    assert localization.tr('language.picker.title') == 'Choose Language'
    assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == 'OK (4s)'
    assert localization.tr('missing.identifier') == 'missing.identifier'
    assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == '1 asset'
    assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == '3 assets'


def test_spanish_translation_lookup_formats_and_counts():
    localization.set_language('es-MX')
    try:
        assert localization.get_language() == 'es'
        assert localization.tr('language.picker.title') == 'Elegir idioma'
        assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == 'Aceptar (4s)'
        assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == '1 activo'
        assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == '3 activos'
    finally:
        localization.set_language('en')


def test_portuguese_translation_lookup_formats_and_counts():
    localization.set_language('pt-BR')
    try:
        assert localization.get_language() == 'pt'
        assert localization.tr('language.picker.title') == PORTUGUESE['language.picker.title']
        assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == PORTUGUESE[
            'onboarding.welcome.ok_countdown'
        ].format(seconds=4)
        assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == PORTUGUESE[
            'count.asset.one'
        ].format(count=1)
        assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == PORTUGUESE[
            'count.asset.other'
        ].format(count=3)
    finally:
        localization.set_language('en')


def test_new_translation_lookup_formats_and_counts():
    catalogs = {
        'ru': RUSSIAN,
        'kk': KAZAKH,
        'tr': TURKISH,
        'de': GERMAN,
        'fr': FRENCH,
        'zh': CHINESE,
        'pl': POLISH,
    }
    for code, catalog in catalogs.items():
        localization.set_language(code)
        try:
            assert localization.get_language() == code
            assert localization.tr('language.picker.title') == catalog['language.picker.title']
            assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == catalog[
                'onboarding.welcome.ok_countdown'
            ].format(seconds=4)
            assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == catalog[
                'count.asset.one'
            ].format(count=1)
            assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == catalog[
                'count.asset.other'
            ].format(count=3)
        finally:
            localization.set_language('en')


def test_profile_error_actions_are_localized():
    localization.set_language('es')
    try:
        action = localization.tr('ui.gui.modifications_tab.profile_action_save')
        assert (
            localization.tr('ui.gui.modifications_tab.could_not_value_profile', value0=action)
            == 'No se pudo guardar el perfil'
        )
    finally:
        localization.set_language('en')


def test_translation_catalogs_match_english_keys_markup_and_placeholders():
    placeholder_re = re.compile(r'\{[^{}]+\}')
    tag_re = re.compile(r'<[^>]+>')

    for catalog in (
        SPANISH,
        PORTUGUESE,
        RUSSIAN,
        KAZAKH,
        TURKISH,
        GERMAN,
        FRENCH,
        CHINESE,
        POLISH,
    ):
        assert list(catalog) == list(localization.ENGLISH)
        assert len(catalog) == len(localization.ENGLISH)

        for identifier, english in localization.ENGLISH.items():
            translated = catalog[identifier]
            assert isinstance(translated, str) and translated, identifier
            assert sorted(placeholder_re.findall(translated)) == sorted(
                placeholder_re.findall(english)
            ), identifier
            assert tag_re.findall(translated) == tag_re.findall(english), identifier
            assert translated.count('\n') == english.count('\n'), identifier
            assert 'ZXQ' not in translated, identifier


def test_translated_onboarding_uses_the_actual_ui_labels():
    label_keys = (
        'ui.gui.replacer_config.replacer',
        'ui.gui.replacer_config.scraped_games',
        'ui.gui.replacer_config.asset_ids',
        'ui.gui.json_viewer.replacement_id',
        'replacer.rules.add_new',
        'ui.gui.replacer_config.enabled',
        'ui.gui.replacer_config.clear_cache',
        'ui.gui.replacer_config.scraper',
    )

    for code, catalog in _TRANSLATED_CATALOGS.items():
        welcome = catalog['onboarding.welcome.body']
        assert 'Default' in welcome, code
        for identifier in label_keys:
            label = catalog[identifier].rstrip(':')
            assert label in welcome, (code, identifier, label)


def test_translated_open_directory_help_matches_the_button_label():
    for code, catalog in _TRANSLATED_CATALOGS.items():
        label = catalog['app.click_here_to_open_directory']
        assert catalog['ui.app.click_here_to_open_directory'] == label, code
        for identifier in (
            'app.most_likely_causes_br_a_antivirus_security',
            'ui.app.most_likely_causes_br_a_antivirus_security',
        ):
            assert label in catalog[identifier], (code, identifier, label)


def test_new_language_font_samples_cover_their_writing_systems():
    alphabets = {
        'ru': set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'),
        'kk': set('аәбвгғдеёжзийкқлмнңоөпрстуұүфхһцчшщъыіьэюя'),
        'tr': set('abcçdefgğhıijklmnoöprsştuüvyz'),
        'de': set('abcdefghijklmnopqrstuvwxyzäöüß'),
        'fr': set(string.ascii_lowercase),
        'pl': set('aąbcćdeęfghijklłmnńoóprsśtuwyzźż'),
    }
    sample_keys = (
        'font_viewer.sample.lowercase',
        'font_viewer.sample.uppercase',
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    )
    for code, alphabet in alphabets.items():
        catalog = _TRANSLATED_CATALOGS[code]
        sample_text = ''.join(catalog[identifier].lower() for identifier in sample_keys)
        assert alphabet <= set(sample_text), code

    for identifier in (
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    ):
        sample = CHINESE[identifier]
        assert sample != localization.ENGLISH[identifier], identifier
        assert any('\u4e00' <= character <= '\u9fff' for character in sample), identifier


def test_turkish_terminology_and_font_samples_are_consistent():
    shared_terms = {
        'Scraped Games': 'Mevcut Oyunlar',
        'Preview': 'Önizleme',
        'Request': 'İstek',
        'Run Anyway (Bad)': 'Yine de Çalıştır (Önerilmez)',
        'Replace With Set': 'Değiştirme Ayarlandı',
    }
    for english, expected in shared_terms.items():
        identifiers = [
            identifier
            for identifier, source_text in localization.ENGLISH.items()
            if source_text == english
        ]
        assert identifiers, english
        assert {TURKISH[identifier] for identifier in identifiers} == {expected}, english

    catalog_text = '\n'.join(TURKISH.values())
    for stale_term in (
        'Replacer',
        'Scraper',
        'scraper',
        'Replace With',
        'Scraped games',
        'Taranan Oyunlar',
        'Taranan oyunlar',
        'Settings >',
        'Proxy Mode',
        'Asset IDs',
        'Replacement ID',
        'Add new',
        'Clear Cache',
        'banlayamaz/banlamaz',
    ):
        assert stale_term not in catalog_text

    alphabet = set('abcçdefgğhıijklmnoöprsştuüvyz')
    for identifier in (
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    ):
        assert alphabet <= set(TURKISH[identifier].casefold()), identifier


def test_spanish_terminology_is_consistent():
    shared_terms = {
        'Scraped Games': 'Juegos recopilados',
        'Preview': 'Vista previa',
        'Request': 'Solicitud',
        'Run on Boot': 'Ejecutar al iniciar el sistema',
        'Run Anyway (Bad)': 'Ejecutar de todos modos (no recomendado)',
        'Replace With Set': 'Reemplazo definido',
    }
    for english, expected in shared_terms.items():
        identifiers = [
            identifier
            for identifier, source_text in localization.ENGLISH.items()
            if source_text == english
        ]
        assert identifiers, english
        assert {SPANISH[identifier] for identifier in identifiers} == {expected}, english

    welcome_keys = (
        'app.welcome_to_fleasion_fleasion_uses_roblox_env',
        'onboarding.welcome.body',
        'ui.app.welcome_to_fleasion_fleasion_uses_roblox_env',
    )
    assert len({SPANISH[identifier] for identifier in welcome_keys}) == 1
    welcome = SPANISH['onboarding.welcome.body']
    for visible_label in (
        '"Juegos recopilados..."',
        '"Agregar nuevo"',
        '"Activado"',
        '"Default"',
        '"Borrar caché"',
    ):
        assert visible_label in welcome
    assert '"Predeterminado"' not in welcome
    assert 'pestaña Reemplazo' in welcome
    assert 'pestaña Extractor' in welcome
    assert 'ID de reemplazo' in welcome

    assert SPANISH['app.click_here_to_open_directory'] == 'Abrir directorio'
    assert SPANISH['ui.app.click_here_to_open_directory'] == 'Abrir directorio'
    for identifier in (
        'app.most_likely_causes_br_a_antivirus_security',
        'ui.app.most_likely_causes_br_a_antivirus_security',
    ):
        assert 'Haz clic en "Abrir directorio".' in SPANISH[identifier]

    catalog_text = '\n'.join(SPANISH.values()).casefold()
    for stale_term in (
        'scraping',
        'scraper de caché',
        'raspador de caché',
        'raspado de activos',
        'juegos raspados',
        'juegos scraped',
        'fleasions',
        'administrador temporal/hijo raíz',
        'harchivo hosts ruta',
        'reemplazar con conjunto',
        'reemplazar con establecer',
        'fleasion está del lado del cliente',
    ):
        assert stale_term not in catalog_text


def test_spanish_font_samples_are_full_alphabet_pangrams():
    alphabet = set(string.ascii_lowercase)
    for identifier in (
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    ):
        normalized = ''.join(
            character
            for character in unicodedata.normalize('NFD', SPANISH[identifier].casefold())
            if unicodedata.category(character) != 'Mn'
        )
        assert alphabet <= set(normalized), identifier


def test_portuguese_brazilian_terminology_is_consistent():
    shared_terms = {
        'Scraped Games': 'Jogos extraídos',
        'Preview': 'Prévia',
        'Request': 'Requisição',
        'Run on Boot': 'Executar na inicialização',
        'Import Custom FastFlags': 'Importar FastFlags personalizadas',
        'Run Anyway (Bad)': 'Executar mesmo assim (não recomendado)',
        'Replace With Set': 'Substituição definida',
    }
    for english, expected in shared_terms.items():
        identifiers = [
            identifier
            for identifier, source_text in localization.ENGLISH.items()
            if source_text == english
        ]
        assert identifiers, english
        assert {PORTUGUESE[identifier] for identifier in identifiers} == {expected}, english

    welcome = PORTUGUESE['onboarding.welcome.body']
    for visible_label in (
        '"Jogos extraídos..."',
        '"Adicionar novo"',
        '"Ativado"',
        '"Default"',
        '"Limpar cache"',
    ):
        assert visible_label in welcome
    assert '"Padrão"' not in welcome

    catalog_text = '\n'.join(PORTUGUESE.values()).casefold()
    assert 'scraping' not in catalog_text
    assert 'scraper de cache' not in catalog_text


def test_portuguese_font_samples_are_full_alphabet_pangrams():
    alphabet = set(string.ascii_lowercase)
    for identifier in (
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    ):
        normalized = ''.join(
            character
            for character in unicodedata.normalize('NFD', PORTUGUESE[identifier].casefold())
            if unicodedata.category(character) != 'Mn'
        )
        assert alphabet <= set(normalized), identifier


def test_translation_values_include_future_registered_languages(monkeypatch):
    pseudo = dict(localization.ENGLISH)
    pseudo['replacer.action.remove'] = 'Supprimer'
    monkeypatch.setitem(localization._TRANSLATIONS, 'fr-test', pseudo)

    values = localization.translation_values('replacer.action.remove')

    assert 'Remove' in values
    assert 'Eliminar' in values
    assert PORTUGUESE['replacer.action.remove'] in values
    assert 'Supprimer' in values


def test_count_translation_uses_selected_language_without_english_noun_leak(monkeypatch):
    pseudo = dict(localization.ENGLISH)
    pseudo['count.asset.one'] = '⟦asset-one⟧ {count}'
    pseudo['count.asset.other'] = '⟦asset-many⟧ {count}'
    monkeypatch.setitem(localization._TRANSLATIONS, 'zz', pseudo)
    try:
        localization.set_language('zz')
        assert localization.tr_count(2, 'count.asset.one', 'count.asset.other') == '⟦asset-many⟧ 2'
    finally:
        localization.set_language('en')


def _translation_keys_from_expression(node: ast.expr) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body = _translation_keys_from_expression(node.body)
        orelse = _translation_keys_from_expression(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None


def test_all_translation_identifiers_exist_in_english_table():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    unknown: list[str] = []
    unsupported_dynamic: list[str] = []

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {'tr', 'tr_count'}
                and node.args
            ):
                continue
            if node.func.id == 'tr':
                identifier_expressions = [node.args[0]]
            elif len(node.args) >= 3:
                identifier_expressions = [node.args[1], node.args[2]]
            else:
                unsupported_dynamic.append(
                    f'{path.relative_to(source_root)}:{node.lineno}: malformed tr_count()'
                )
                continue
            location = f'{path.relative_to(source_root)}:{node.lineno}'
            for expression in identifier_expressions:
                keys = _translation_keys_from_expression(expression)
                if keys is None:
                    unsupported_dynamic.append(f'{location}: {ast.unparse(expression)}')
                    continue
                for key in keys:
                    if key not in localization.ENGLISH:
                        unknown.append(f'{location}: {key}')

    assert not unsupported_dynamic, (
        'Translation identifiers must be statically enumerable so coverage can be verified:\n'
        + '\n'.join(unsupported_dynamic)
    )
    assert not unknown, 'Unknown translation identifiers:\n' + '\n'.join(unknown)


def test_common_ui_surfaces_do_not_use_literal_visible_text():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    text_constructors = {
        'QLabel',
        'QPushButton',
        'QCheckBox',
        'QGroupBox',
        'QAction',
        'QMenu',
        'QRadioButton',
        'QListWidgetItem',
        'QTableWidgetItem',
        'QStandardItem',
        'CollapsibleSection',
        '_RichTextButton',
    }
    text_methods = {
        'setText',
        'setWindowTitle',
        'setToolTip',
        'setStatusTip',
        'setPlaceholderText',
        'setTitle',
        'setInformativeText',
        'setDetailedText',
        'setPrefix',
        'setSuffix',
        'setSpecialValueText',
        'setHeaderLabels',
        'setHorizontalHeaderLabels',
        'setVerticalHeaderLabels',
        'addItem',
        'addItems',
        'addAction',
        'addMenu',
        'addRow',
    }
    allowed_symbols = {'', '↺', '✕', '▼', '▶', '►'}
    untranslated: list[str] = []

    def _literal_nodes(node: ast.expr) -> list[ast.expr]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [
                item
                for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
        if isinstance(node, ast.JoinedStr):
            return [node]
        return []

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            else:
                continue
            argument_indices: list[int] = []
            if call_name in text_constructors | text_methods:
                argument_indices.append(0)
            if call_name in {'getText', 'getItem', 'getInt', 'getDouble', 'getMultiLineText'}:
                argument_indices.extend([1, 2])
            if call_name in {
                'getOpenFileName',
                'getSaveFileName',
                'getOpenFileNames',
                'getExistingDirectory',
            }:
                argument_indices.append(1)
                if call_name != 'getExistingDirectory':
                    argument_indices.append(3)
            for arg_index in argument_indices:
                if arg_index >= len(node.args):
                    continue
                for literal in _literal_nodes(node.args[arg_index]):
                    if isinstance(literal, ast.Constant):
                        text = literal.value
                    else:
                        text = ast.unparse(literal)
                    if text in allowed_symbols:
                        continue
                    untranslated.append(
                        f'{path.relative_to(source_root)}:{literal.lineno}: '
                        f'{call_name} arg {arg_index} ({text!r})'
                    )

    assert not untranslated, 'Visible UI text must use tr() identifiers:\n' + '\n'.join(
        untranslated
    )


def test_indirect_ui_text_flows_do_not_use_literal_visible_text():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    ui_forwarding_helpers = {
        '_show_text_preview': {0},
        '_show_export_complete_message': {0, 1},
        '_show_replacer_notification': {0, 1},
        '_show_selected_account_launch_failed': {1},
        'set_ok_label': {0},
    }
    ui_signal_names = {
        'status_message',
        'error',
        'texture_error',
        '_failed',
        '_status_update',
        '_error_ready',
        'load_failed',
    }
    untranslated: list[str] = []

    def _is_literal_text(node: ast.expr) -> bool:
        return isinstance(node, (ast.JoinedStr, ast.BinOp)) or (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(node.value)
        )

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            else:
                continue

            for arg_index in ui_forwarding_helpers.get(call_name, set()):
                if arg_index < len(node.args) and _is_literal_text(node.args[arg_index]):
                    untranslated.append(
                        f'{path.relative_to(source_root)}:{node.lineno}: '
                        f'{call_name} arg {arg_index}: {ast.unparse(node.args[arg_index])}'
                    )

            if call_name == 'ProxyGate':
                candidate_nodes = [
                    keyword.value for keyword in node.keywords if keyword.arg == 'message'
                ]
                for candidate in candidate_nodes:
                    if _is_literal_text(candidate):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'ProxyGate message: {ast.unparse(candidate)}'
                        )

            if call_name == 'AddAccountDialog':
                candidate_nodes: list[ast.expr] = []
                if len(node.args) > 1:
                    candidate_nodes.append(node.args[1])
                candidate_nodes.extend(
                    keyword.value for keyword in node.keywords if keyword.arg == 'title'
                )
                for candidate in candidate_nodes:
                    if _is_literal_text(candidate):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'AddAccountDialog title: {ast.unparse(candidate)}'
                        )

            if call_name in {'_preview_hex', '_preview_rbxm'}:
                for keyword in node.keywords:
                    if keyword.arg in {'reason', 'title_prefix'} and _is_literal_text(
                        keyword.value
                    ):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'{call_name} {keyword.arg}: {ast.unparse(keyword.value)}'
                        )

            if (
                call_name == 'emit'
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in ui_signal_names
            ):
                for arg_index, arg in enumerate(node.args):
                    if _is_literal_text(arg):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'{node.func.value.attr}.emit arg {arg_index}: {ast.unparse(arg)}'
                        )

    assert not untranslated, (
        'Indirect user-visible text must use tr() identifiers before it reaches UI helpers/signals:\n'
        + '\n'.join(untranslated)
    )


def test_ui_label_constants_and_helper_returns_are_localized():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    ui_constant_suffixes = (
        '_HEADERS',
        '_LABELS',
        '_OPTIONS',
        '_NOTE',
        '_MESSAGE',
        '_COLUMNS',
        '_COLS',
    )
    ui_helper_suffixes = ('_tooltip', '_message', '_display_name', '_humanize_time')
    untranslated: list[str] = []

    def _contains_literal_text(node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return bool(node.value.strip())
        if isinstance(node, ast.JoinedStr):
            return any(
                isinstance(part, ast.Constant)
                and isinstance(part.value, str)
                and bool(part.value.strip())
                for part in node.values
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
            return False
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(_contains_literal_text(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return any(_contains_literal_text(item) for item in node.values)
        if isinstance(node, ast.IfExp):
            return _contains_literal_text(node.body) or _contains_literal_text(node.orelse)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return _contains_literal_text(node.left) or _contains_literal_text(node.right)
        return False

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        source_text = path.read_text(encoding='utf-8')
        tree = ast.parse(source_text, filename=str(path))
        is_ui_module = 'PyQt6' in source_text
        for node in ast.walk(tree):
            if is_ui_module and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == target.id.upper()
                        and target.id.endswith(ui_constant_suffixes)
                        and _contains_literal_text(node.value)
                    ):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: {target.id}'
                        )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.endswith(
                ui_helper_suffixes
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.Return) and _contains_literal_text(child.value):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{child.lineno}: {node.name} return'
                        )

    assert not untranslated, (
        'UI label constants/helper-returned text must be localized:\n' + '\n'.join(untranslated)
    )


def test_ui_collection_labels_are_localized_before_display():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    text_calls = {
        'QLabel',
        'QPushButton',
        'QCheckBox',
        'QGroupBox',
        'QAction',
        'QMenu',
        'QRadioButton',
        'QListWidgetItem',
        'QTableWidgetItem',
        'QStandardItem',
        'CollapsibleSection',
        '_RichTextButton',
        'addAction',
        'addMenu',
        'addItem',
        'addRow',
        'setText',
        'setWindowTitle',
        'setToolTip',
        'setPlaceholderText',
        'setTitle',
    }
    untranslated: list[str] = []

    def _is_raw_string(node: ast.AST | None) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and bool(node.value.strip())
        )

    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ''

    def _tuple_target_names(target: ast.AST) -> list[str | None]:
        if isinstance(target, (ast.Tuple, ast.List)):
            return [item.id if isinstance(item, ast.Name) else None for item in target.elts]
        if isinstance(target, ast.Name):
            return [target.id]
        return []

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        source_text = path.read_text(encoding='utf-8')
        if 'PyQt6' not in source_text:
            continue
        tree = ast.parse(source_text, filename=str(path))

        named_collections: dict[str, ast.AST] = {}
        self_collections: dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    named_collections[target.id] = node.value
                    if (
                        target.id.lower().endswith(('_labels', '_titles', '_captions'))
                        and isinstance(node.value, ast.Dict)
                        and any(_is_raw_string(value) for value in node.value.values)
                    ):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'{target.id} contains literal display labels'
                        )
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'
                ):
                    self_collections[target.attr] = node.value

        for loop in (node for node in ast.walk(tree) if isinstance(node, ast.For)):
            target_names = _tuple_target_names(loop.target)
            if not target_names:
                continue

            source: ast.AST | None = loop.iter
            dict_items = False
            if isinstance(loop.iter, ast.Name):
                source = named_collections.get(loop.iter.id)
            elif (
                isinstance(loop.iter, ast.Call)
                and isinstance(loop.iter.func, ast.Attribute)
                and loop.iter.func.attr == 'items'
                and isinstance(loop.iter.func.value, ast.Attribute)
                and isinstance(loop.iter.func.value.value, ast.Name)
                and loop.iter.func.value.value.id == 'self'
            ):
                source = self_collections.get(loop.iter.func.value.attr)
                dict_items = True

            if source is None:
                continue

            displayed_indices: set[int] = set()
            for child in ast.walk(loop):
                if (
                    not isinstance(child, ast.Call)
                    or not child.args
                    or _call_name(child) not in text_calls
                ):
                    continue
                first_arg = child.args[0]
                if isinstance(first_arg, ast.Name) and first_arg.id in target_names:
                    displayed_indices.add(target_names.index(first_arg.id))
            if not displayed_indices:
                continue

            if dict_items and isinstance(source, ast.Dict):
                for index in displayed_indices:
                    candidates = source.keys if index == 0 else source.values if index == 1 else []
                    if any(_is_raw_string(candidate) for candidate in candidates):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{loop.lineno}: '
                            f'literal self-collection item flows into visible text'
                        )
                continue

            if not isinstance(source, (ast.List, ast.Tuple)):
                continue
            for index in displayed_indices:
                for entry in source.elts:
                    if isinstance(entry, (ast.Tuple, ast.List)):
                        if index < len(entry.elts) and _is_raw_string(entry.elts[index]):
                            untranslated.append(
                                f'{path.relative_to(source_root)}:{loop.lineno}: '
                                f'literal collection label flows into visible text'
                            )
                            break
                    elif index == 0 and _is_raw_string(entry):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{loop.lineno}: '
                            f'literal collection label flows into visible text'
                        )
                        break

    assert not untranslated, (
        'Collection-backed visible UI labels must be translated before display:\n'
        + '\n'.join(untranslated)
    )


def test_local_variables_forwarded_to_ui_do_not_hide_literal_text():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    text_calls = {
        'QLabel',
        'QPushButton',
        'QCheckBox',
        'QGroupBox',
        'QAction',
        'QMenu',
        'QRadioButton',
        'QListWidgetItem',
        'QTableWidgetItem',
        'QStandardItem',
        'CollapsibleSection',
        '_RichTextButton',
        'addAction',
        'addMenu',
        'addItem',
        'addRow',
        'setText',
        'setWindowTitle',
        'setToolTip',
        'setPlaceholderText',
        'setTitle',
    }
    untranslated: list[str] = []

    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ''

    def _literal_fragments(node: ast.AST | None) -> list[str]:
        if node is None:
            return []
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
            return []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            result: list[str] = []
            for part in node.values:
                result.extend(_literal_fragments(part))
            return result
        if isinstance(node, ast.IfExp):
            return _literal_fragments(node.body) + _literal_fragments(node.orelse)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return _literal_fragments(node.left) + _literal_fragments(node.right)
        return []

    def _is_translatable_literal_expression(node: ast.AST) -> bool:
        fragments = [fragment for fragment in _literal_fragments(node) if fragment.strip()]
        if not fragments:
            return False
        joined = ''.join(fragments).strip()
        if joined.startswith(('http://', 'https://')):
            return False
        if not any(char.isalnum() for char in joined):
            return False
        return True

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        source_text = path.read_text(encoding='utf-8')
        if 'PyQt6' not in source_text:
            continue
        tree = ast.parse(source_text, filename=str(path))
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            assignments: dict[str, tuple[int, ast.AST]] = {}
            for node in ast.walk(function):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = (node.lineno, node.value)

            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                if not call.args or _call_name(call) not in text_calls:
                    continue
                first_arg = call.args[0]
                if not isinstance(first_arg, ast.Name) or first_arg.id not in assignments:
                    continue
                assigned_line, expression = assignments[first_arg.id]
                if _is_translatable_literal_expression(expression):
                    untranslated.append(
                        f'{path.relative_to(source_root)}:{call.lineno}: '
                        f'{first_arg.id} assigned literal UI text at {assigned_line}'
                    )

    assert not untranslated, (
        'Local variables forwarded into visible UI must not hide literal English text:\n'
        + '\n'.join(untranslated)
    )


def test_nested_ui_defaults_and_overloads_do_not_hide_literal_text():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    untranslated: list[str] = []

    def _human_literals(node: ast.AST | None) -> list[str]:
        if node is None:
            return []
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {'tr', 'tr_count', 'verbatim'}:
                return []
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
                return _human_literals(node.args[1]) if len(node.args) >= 2 else []
            if isinstance(node.func, ast.Name) and node.func.id == 'getattr':
                return _human_literals(node.args[2]) if len(node.args) >= 3 else []
            return []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if text and any(char.isalpha() for char in text):
                return [node.value]
            return []
        if isinstance(node, ast.JoinedStr):
            result: list[str] = []
            for part in node.values:
                result.extend(_human_literals(part))
            return result
        if isinstance(node, ast.IfExp):
            return _human_literals(node.body) + _human_literals(node.orelse)
        if isinstance(node, ast.BoolOp):
            result: list[str] = []
            for value in node.values:
                result.extend(_human_literals(value))
            return result
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            result: list[str] = []
            for value in node.elts:
                result.extend(_human_literals(value))
            return result
        return []

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        source_text = path.read_text(encoding='utf-8')
        if 'PyQt6' not in source_text:
            continue
        tree = ast.parse(source_text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            else:
                continue

            candidates: list[ast.AST] = []
            if call_name == 'QTreeWidgetItem':
                candidates.extend(node.args)
            elif (
                call_name in {'QListWidgetItem', 'QTableWidgetItem', 'QStandardItem'} and node.args
            ):
                candidates.append(node.args[0])
            if call_name in {'getText', 'getItem', 'getInt', 'getDouble', 'getMultiLineText'}:
                candidates.extend(
                    keyword.value for keyword in node.keywords if keyword.arg == 'text'
                )

            for candidate in candidates:
                literals = _human_literals(candidate)
                if literals:
                    untranslated.append(
                        f'{path.relative_to(source_root)}:{node.lineno}: {call_name}: {literals!r}'
                    )

    assert not untranslated, (
        'Nested/default visible UI text must use translation identifiers or verbatim():\n'
        + '\n'.join(untranslated)
    )


def test_translation_placeholders_do_not_inject_literal_human_text():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'fleasion'
    untranslated: list[str] = []

    def _contains_human_literal(node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {'tr', 'tr_count', 'verbatim'}:
                return False
            if isinstance(node.func, ast.Name) and node.func.id == 'format_count':
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
                return len(node.args) >= 2 and _contains_human_literal(node.args[1])
            if isinstance(node.func, ast.Name) and node.func.id == 'getattr':
                return len(node.args) >= 3 and _contains_human_literal(node.args[2])
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            return bool(
                text
                and any(char.isalpha() for char in text)
                and not text.startswith(('http://', 'https://'))
            )
        if isinstance(node, ast.JoinedStr):
            return any(
                _contains_human_literal(part)
                for part in node.values
                if isinstance(part, ast.Constant)
            )
        if isinstance(node, ast.IfExp):
            return _contains_human_literal(node.body) or _contains_human_literal(node.orelse)
        if isinstance(node, ast.BoolOp):
            return any(_contains_human_literal(value) for value in node.values)
        return False

    for path in source_root.rglob('*.py'):
        if path.name == 'localization.py' or 'translations' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        scopes: list[ast.AST] = [tree]
        scopes.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for scope in scopes:
            stack = list(getattr(scope, 'body', []))
            nodes: list[ast.AST] = []
            while stack:
                current = stack.pop()
                nodes.append(current)
                if (
                    isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                    and current is not scope
                ):
                    continue
                stack.extend(ast.iter_child_nodes(current))

            assignments: dict[str, ast.AST] = {}
            for node in nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value

            for node in nodes:
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'tr'
                ):
                    continue
                for keyword in node.keywords:
                    value = keyword.value
                    if isinstance(value, ast.Name) and value.id in assignments:
                        value = assignments[value.id]
                    if _contains_human_literal(value):
                        untranslated.append(
                            f'{path.relative_to(source_root)}:{node.lineno}: '
                            f'{keyword.arg}={ast.unparse(value)}'
                        )

    assert not untranslated, (
        'Translated strings must not receive untranslated human phrases as placeholders; '
        'use tr(), tr_count(), or verbatim() explicitly:\n' + '\n'.join(untranslated)
    )


def test_settings_language_change_is_saved_but_active_language_waits_for_restart(monkeypatch):
    from fleasion.gui import settings_tab as settings_module

    class FakeCombo:
        @staticmethod
        def currentData():
            return 'future-language'

    class FakeConfig:
        language = 'en'

    fake_tab = type(
        'FakeSettingsTab',
        (),
        {'_language_combo': FakeCombo(), '_config': FakeConfig()},
    )()
    notices: list[tuple[str, str]] = []
    localization.set_language('en')
    monkeypatch.setattr(
        settings_module.QMessageBox,
        'information',
        lambda _parent, title, body: notices.append((title, body)),
    )

    settings_module.SettingsTab._on_language_changed(fake_tab)

    assert fake_tab._config.language == 'future-language'
    assert localization.get_language() == 'en'
    assert notices == [
        (
            localization.tr('settings.language.restart_required_title'),
            localization.tr('settings.language.restart_required_body'),
        )
    ]


def test_config_language_invalid_saved_value_falls_back_to_english(tmp_path, monkeypatch):
    config_dir = tmp_path / 'Fleasion'
    config_dir.mkdir()
    config_file = config_dir / 'settings.json'
    configs_folder = config_dir / 'Configs'
    configs_folder.mkdir()
    config_file.write_text(json.dumps({'language': 'definitely-invalid'}), encoding='utf-8')

    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_file)
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_folder)

    manager = manager_module.ConfigManager()
    assert manager.language == 'en'

    manager.language = 'also-invalid'
    assert manager.language == 'en'
    assert json.loads(config_file.read_text(encoding='utf-8'))['language'] == 'en'
