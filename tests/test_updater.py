from collections.abc import Callable
from typing import cast
from urllib.parse import SplitResult

import pytest
from packaging.version import Version
from pytest import MonkeyPatch

from fleasion.utils import update_resolver
from fleasion.utils.metadata import APP_REPO
from fleasion.utils.update_resolver import ReleaseCandidate, UpdateResolver
from fleasion.utils.updater import QtUpdateChecker


def _worker(checker: QtUpdateChecker) -> None:
    callback = cast('Callable[[], None]', getattr(checker, '_worker'))
    callback()


def _record_found(values: list[tuple[str, str]]) -> Callable[[str, str], None]:
    def record(tag: str, url: str) -> None:
        values.append((tag, url))

    return record


_REPOSITORY_URL = 'https://github.com/fleasion/Fleasion'
_LATEST_RELEASE_API = 'https://api.github.com/repos/fleasion/Fleasion/releases/latest'
_RELEASES_API = 'https://api.github.com/repos/fleasion/Fleasion/releases'


def _resolver(current_version: str) -> UpdateResolver:
    return UpdateResolver(current_version, _REPOSITORY_URL)


@pytest.mark.parametrize(
    ('current_version', 'latest_tag', 'expected'),
    [
        ('2.4.0', 'v2.4.1', True),
        ('2.4.0', 'v2.4.0', False),
        ('2.4.0', 'v2.3.9', False),
        ('2.4.0b1', 'v2.4.0', True),
        ('2.4.0b1', 'v2.3.0', False),
        ('2.4.0b1+local', 'v2.4.0b1', False),
        ('2.4.0b1+gabcdef0', ' v2.4.0 ', True),
        ('2.4.0', 'OUT OF BETA', False),
        ('not-a-version', 'v2.4.1', False),
    ],
)
def test_update_availability_uses_semantic_version_ordering(
    current_version: str,
    latest_tag: str,
    expected: bool,
) -> None:
    release = {'tag_name': latest_tag, 'draft': False, 'prerelease': False}
    assert (_resolver(current_version).select_update(release) is not None) is expected


@pytest.mark.parametrize(
    ('current_version', 'expected_api'),
    [
        ('2.4.0', _LATEST_RELEASE_API),
        ('2.4.0.post1', _LATEST_RELEASE_API),
        ('2.4.0a1', _RELEASES_API),
        ('2.4.0b1', _RELEASES_API),
        ('2.4.0rc1', _RELEASES_API),
        ('2.4.0.dev1', _RELEASES_API),
    ],
)
def test_release_api_follows_installed_version_channel(
    current_version: str,
    expected_api: str,
) -> None:
    assert _resolver(current_version).release_api == expected_api


def test_repository_url_is_normalized_for_github_endpoints() -> None:
    resolver = UpdateResolver('2.4.0', 'https://github.com/example/project.git/')

    assert resolver.repository_slug == 'example/project'
    assert resolver.releases_page == 'https://github.com/example/project/releases/latest'
    assert resolver.latest_release_api == (
        'https://api.github.com/repos/example/project/releases/latest'
    )


def test_resolver_caches_parsed_configuration(monkeypatch: MonkeyPatch) -> None:
    urlsplit_calls: list[str] = []
    original_urlsplit = update_resolver.urlsplit

    def tracked_urlsplit(url: str) -> SplitResult:
        urlsplit_calls.append(url)
        return original_urlsplit(url)

    monkeypatch.setattr(update_resolver, 'urlsplit', tracked_urlsplit)
    resolver = _resolver('2.4.0b1')

    assert resolver.parsed_current_version is resolver.parsed_current_version
    assert resolver.repository_slug == 'fleasion/Fleasion'
    assert resolver.releases_page.endswith('/fleasion/Fleasion/releases/latest')
    assert resolver.release_api == _RELEASES_API
    assert resolver.parsed_repository_url is resolver.parsed_repository_url
    assert urlsplit_calls == [_REPOSITORY_URL]


def test_repository_url_rejects_unsupported_hosts() -> None:
    resolver = UpdateResolver('2.4.0', 'https://example.invalid/fleasion/Fleasion')

    with pytest.raises(ValueError, match='Unsupported GitHub repository URL'):
        _ = resolver.release_api


def test_prerelease_channel_selects_newest_published_prerelease() -> None:
    releases = [
        {
            'tag_name': 'v2.4.0b3',
            'html_url': 'https://example.invalid/b3',
            'draft': True,
            'prerelease': True,
        },
        {
            'tag_name': 'v2.4.0b2',
            'html_url': 'https://example.invalid/b2',
            'draft': False,
            'prerelease': True,
        },
        {'tag_name': 'not-a-version', 'draft': False, 'prerelease': True},
        {'tag_name': 'v2.3.0', 'draft': False, 'prerelease': False},
    ]

    assert _resolver('2.4.0b1').select_update(releases) == ReleaseCandidate(
        version=Version('2.4.0b2'),
        tag='v2.4.0b2',
        html_url='https://example.invalid/b2',
    )


def test_prerelease_channel_prefers_final_release() -> None:
    releases = [
        {'tag_name': 'v2.4.0rc1', 'draft': False, 'prerelease': True},
        {
            'tag_name': 'v2.4.0',
            'html_url': 'https://example.invalid/stable',
            'draft': False,
            'prerelease': False,
        },
    ]

    assert _resolver('2.4.0b2').select_update(releases) == ReleaseCandidate(
        version=Version('2.4.0'),
        tag='v2.4.0',
        html_url='https://example.invalid/stable',
    )


@pytest.mark.parametrize(
    'release',
    [
        {'tag_name': 'v2.4.1', 'draft': True, 'prerelease': False},
        {'tag_name': 'v2.5.0b1', 'draft': False, 'prerelease': True},
    ],
)
def test_stable_channel_ignores_drafts_and_prereleases(release: dict[str, object]) -> None:
    assert _resolver('2.4.0').select_update(release) is None


def test_resolver_fetches_from_its_channel_endpoint(monkeypatch: MonkeyPatch) -> None:
    requested_urls: list[str] = []
    requested_params: list[object] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return [
                {
                    'tag_name': 'v2.4.0b2',
                    'html_url': 'https://example.invalid/b2',
                    'draft': False,
                    'prerelease': True,
                }
            ]

    def get(url: str, **_kwargs: object) -> Response:
        requested_urls.append(url)
        requested_params.append(_kwargs.get('params'))
        return Response()

    monkeypatch.setattr(update_resolver.requests, 'get', get)
    resolver = _resolver('2.4.0b1')

    assert resolver.check() == ReleaseCandidate(
        version=Version('2.4.0b2'),
        tag='v2.4.0b2',
        html_url='https://example.invalid/b2',
    )
    assert requested_urls == [_RELEASES_API]
    assert requested_params == [{'per_page': 10}]


def test_qt_checker_owns_resolver_and_emits_its_result(monkeypatch: MonkeyPatch) -> None:
    resolver = _resolver('2.4.0b1')
    monkeypatch.setattr(
        resolver,
        'check',
        lambda: ReleaseCandidate(
            version=Version('2.4.0b2'),
            tag='v2.4.0b2',
            html_url='https://example.invalid/b2',
        ),
    )
    checker = QtUpdateChecker(resolver)
    found: list[tuple[str, str]] = []
    checker.found.connect(_record_found(found))

    _worker(checker)

    assert checker.resolver is resolver
    assert found == [('v2.4.0b2', 'https://example.invalid/b2')]


def test_qt_checker_injects_repository_metadata_into_default_resolver() -> None:
    checker = QtUpdateChecker()

    assert checker.resolver.repository_url == APP_REPO
