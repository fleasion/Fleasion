import ast
import json
import re
from pathlib import Path

from fleasion import localization
from fleasion.config import manager as manager_module
from fleasion.translations.es import SPANISH


def test_english_is_default_and_spanish_is_available():
    assert localization.available_languages() == (('en', 'English'), ('es', 'Español'))
    assert localization.normalize_language(None) == 'en'
    assert localization.normalize_language('en-US') == 'en'
    assert localization.normalize_language('es-MX') == 'es'
    assert localization.normalize_language('es_ES') == 'es'
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


def test_spanish_catalog_matches_english_keys_markup_and_placeholders():
    assert list(SPANISH) == list(localization.ENGLISH)
    assert len(SPANISH) == len(localization.ENGLISH)

    placeholder_re = re.compile(r'\{[^{}]+\}')
    tag_re = re.compile(r'<[^>]+>')
    for identifier, english in localization.ENGLISH.items():
        spanish = SPANISH[identifier]
        assert isinstance(spanish, str) and spanish, identifier
        assert sorted(placeholder_re.findall(spanish)) == sorted(placeholder_re.findall(english)), (
            identifier
        )
        assert tag_re.findall(spanish) == tag_re.findall(english), identifier
        assert 'ZXQ' not in spanish, identifier


def test_translation_values_include_future_registered_languages(monkeypatch):
    pseudo = dict(localization.ENGLISH)
    pseudo['replacer.action.remove'] = 'Supprimer'
    monkeypatch.setitem(localization._TRANSLATIONS, 'fr-test', pseudo)

    values = localization.translation_values('replacer.action.remove')

    assert 'Remove' in values
    assert 'Eliminar' in values
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
