import ast
import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

from fleasion import localization
from fleasion.translations.qml_de import QML_GERMAN
from fleasion.translations.qml_en import QML_ENGLISH
from fleasion.translations.qml_es import QML_SPANISH
from fleasion.translations.qml_fr import QML_FRENCH
from fleasion.translations.qml_kk import QML_KAZAKH
from fleasion.translations.qml_pl import QML_POLISH
from fleasion.translations.qml_pt import QML_PORTUGUESE
from fleasion.translations.qml_ru import QML_RUSSIAN
from fleasion.translations.qml_sources import QML_SOURCE_IDS
from fleasion.translations.qml_tr import QML_TURKISH
from fleasion.translations.qml_zh import QML_CHINESE

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_QML_ROOT = _PROJECT_ROOT / 'src' / 'fleasion' / 'qml'
_PYTHON_UI_ROOT = _PROJECT_ROOT / 'src' / 'fleasion' / 'qml_api'
_RUNTIME_PATH = _PROJECT_ROOT / 'src' / 'fleasion' / 'qml_runtime.py'
_QSTR_LITERAL_RE = re.compile(r'qsTr\(\s*(["\'])(.*?)(?<!\\)\1', re.S)
_PLACEHOLDER_RE = re.compile(r'\{[^{}]+\}')
_DIRECT_TEXT_RE = re.compile(
    r'^\s*(?:(?:readonly\s+)?property\s+string\s+)?'
    r'(?:text|title|subtitle|description|placeholderText|toolTipText|accessibleName|'
    r'labelText|actionText|heading|message|details|acceptText)\s*:\s*(["\'])(.*?)\1\s*$'
)
_QML_TRANSLATIONS = {
    'es': QML_SPANISH,
    'pt': QML_PORTUGUESE,
    'ru': QML_RUSSIAN,
    'kk': QML_KAZAKH,
    'tr': QML_TURKISH,
    'de': QML_GERMAN,
    'fr': QML_FRENCH,
    'zh': QML_CHINESE,
    'pl': QML_POLISH,
}


def _qml_sources() -> set[str]:
    sources: set[str] = set()
    for path in _QML_ROOT.rglob('*.qml'):
        source = path.read_text(encoding='utf-8')
        for match in _QSTR_LITERAL_RE.finditer(source):
            literal = match.group(1) + match.group(2) + match.group(1)
            sources.add(ast.literal_eval(literal))
    return sources


def test_qml_source_map_covers_every_literal_qstr_source():
    sources = _qml_sources()

    assert sources == set(QML_SOURCE_IDS)
    assert len(sources) == 1138
    mapped_identifiers = set(QML_SOURCE_IDS.values())
    assert mapped_identifiers <= set(localization._ENGLISH_CATALOG)
    for code, catalog in localization._TRANSLATIONS.items():
        assert mapped_identifiers <= set(catalog), code
    source_catalog_ids = {
        identifier for identifier in QML_SOURCE_IDS.values() if identifier.startswith('qml.')
    }
    assert source_catalog_ids <= set(QML_ENGLISH)
    assert not (
        set(QML_ENGLISH)
        - source_catalog_ids
        - {identifier for identifier in QML_ENGLISH if identifier.startswith('qml.dynamic.')}
    )


def test_qml_supplemental_catalogs_match_english_keys_and_placeholders():
    for code, catalog in _QML_TRANSLATIONS.items():
        assert set(catalog) == set(QML_ENGLISH), code
        assert len(catalog) == len(QML_ENGLISH), code

        for identifier, english in QML_ENGLISH.items():
            translated = catalog[identifier]
            assert isinstance(translated, str) and translated, (code, identifier)
            assert sorted(_PLACEHOLDER_RE.findall(translated)) == sorted(
                _PLACEHOLDER_RE.findall(english)
            ), (code, identifier)
            assert translated.count('\n') == english.count('\n'), (code, identifier)


def test_qml_visible_text_properties_do_not_use_hardcoded_english():
    alphabetic_allowlist = {'F'}
    offenders: list[str] = []

    for path in _QML_ROOT.rglob('*.qml'):
        for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if 'qsTr(' in line:
                continue
            match = _DIRECT_TEXT_RE.match(line)
            if match is None:
                continue
            value = ast.literal_eval(match.group(1) + match.group(2) + match.group(1))
            if value in alphabetic_allowlist or not any(character.isalpha() for character in value):
                continue
            offenders.append(f'{path.relative_to(_QML_ROOT)}:{line_number}: {value!r}')

    assert not offenders, 'Hardcoded QML-visible text:\n' + '\n'.join(offenders)


def test_qml_translator_switches_languages_and_preserves_placeholders():
    application = QCoreApplication.instance() or QCoreApplication([])
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(
        b"""import QtQml\nQtObject {\n"""
        b"""    property string dashboard: qsTr("Open dashboard")\n"""
        b"""    property string proxyStatus: qsTr("Proxy status: %1").arg("RUN")\n"""
        b"""    property string blocked: qsTr("%n blocked place(s)", "", 3)\n"""
        b"""}\n""",
        QUrl(),
    )
    assert not component.isError(), [error.toString() for error in component.errors()]

    try:
        localization.set_language('es')
        instance = component.create()
        assert instance is not None
        assert instance.property('dashboard') == 'Abrir panel'
        assert instance.property('proxyStatus') == 'Estado del proxy: RUN'
        assert instance.property('blocked') == '3 lugar(es) bloqueado(s)'

        localization.set_language('de')
        engine.retranslate()
        assert instance.property('dashboard') == 'Dashboard öffnen'
        assert instance.property('proxyStatus') == 'Proxy-Status: RUN'
        assert instance.property('blocked') == '3 blockierte(r) Ort(e)'

        localization.set_language('en')
        engine.retranslate()
        assert instance.property('dashboard') == 'Open dashboard'
        assert instance.property('proxyStatus') == 'Proxy status: RUN'
        assert instance.property('blocked') == '3 blocked place(s)'
    finally:
        localization.set_language('en')
        del application


def _python_dynamic_identifiers() -> set[str]:
    identifiers: set[str] = set()
    paths = [*_PYTHON_UI_ROOT.glob('*.py'), _RUNTIME_PATH]
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.startswith('qml.dynamic.'):
                identifiers.add(node.value)
    return identifiers


def test_python_dynamic_ui_identifiers_are_fully_cataloged():
    english_dynamic = {key for key in QML_ENGLISH if key.startswith('qml.dynamic.')}
    referenced = _python_dynamic_identifiers()

    assert len(english_dynamic) == 464
    for identifier in referenced:
        if identifier.endswith('.'):
            assert any(key.startswith(identifier) for key in english_dynamic), identifier
        else:
            assert identifier in english_dynamic, identifier

    for code, catalog in _QML_TRANSLATIONS.items():
        translated_dynamic = {key for key in catalog if key.startswith('qml.dynamic.')}
        assert translated_dynamic == english_dynamic, code
        for identifier in english_dynamic:
            assert catalog[identifier].strip(), (code, identifier)
            assert sorted(_PLACEHOLDER_RE.findall(catalog[identifier])) == sorted(
                _PLACEHOLDER_RE.findall(QML_ENGLISH[identifier])
            ), (code, identifier)


def test_python_qml_ui_calls_do_not_embed_authored_english_literals():
    offenders: list[str] = []
    paths = [*_PYTHON_UI_ROOT.glob('*.py'), _RUNTIME_PATH]

    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue

            arguments: list[ast.expr] = []
            label = ''
            if (
                node.func.attr == 'emit'
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {'errorOccurred', 'notificationRequested'}
            ):
                label = f'{node.func.value.attr}.emit'
                arguments = list(
                    node.args[:2]
                    if node.func.value.attr == 'notificationRequested'
                    else node.args[:1]
                )
            elif node.func.attr == '_set_status':
                label = '_set_status'
                arguments = list(node.args[:1])
            elif (
                node.func.attr in {'run', 'run_cancellable'}
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == '_task'
            ):
                label = f'_task.{node.func.attr}'
                arguments = list(node.args[:1])
            else:
                continue

            for argument in arguments:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    if argument.value and any(character.isalpha() for character in argument.value):
                        offenders.append(
                            f'{path.relative_to(_PROJECT_ROOT)}:{node.lineno}: '
                            f'{label}: {argument.value!r}'
                        )
                elif isinstance(argument, ast.JoinedStr):
                    offenders.append(
                        f'{path.relative_to(_PROJECT_ROOT)}:{node.lineno}: {label}: f-string'
                    )

    assert not offenders, 'Raw Python-to-QML UI text:\n' + '\n'.join(offenders)
