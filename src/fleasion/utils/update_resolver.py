"""GitHub release and update-channel resolution without UI dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple, TypeIs
from urllib.parse import urlsplit

import requests
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import ClassVar
    from urllib.parse import SplitResult


def _is_object_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_object_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


class ReleaseCandidate(NamedTuple):
    """A parsed GitHub release eligible for an application update."""

    version: Version
    tag: str
    html_url: str


class UpdateResolver:
    """Resolve the highest published GitHub release for an installed version."""

    GITHUB_API_ORIGIN: ClassVar[str] = 'https://api.github.com'
    PRERELEASE_PAGE_SIZE: ClassVar[int] = 10

    def __init__(
        self,
        current_version: str,
        repository_url: str,
        *,
        timeout: int = 10,
    ) -> None:
        self.current_version = current_version
        self.repository_url = repository_url.rstrip('/')
        self.timeout = timeout

    @cached_property
    def parsed_current_version(self) -> Version | None:
        """Return the installed version, or ``None`` when it is invalid."""
        try:
            return Version(self.current_version)
        except InvalidVersion:
            return None

    @cached_property
    def parsed_repository_url(self) -> SplitResult:
        """Return the repository URL parsed once for all derived endpoints."""
        return urlsplit(self.repository_url)

    @cached_property
    def repository_slug(self) -> str:
        """Return the validated GitHub owner and repository path."""
        parsed = self.parsed_repository_url
        path_parts = parsed.path.strip('/').split('/')
        if parsed.scheme != 'https' or parsed.hostname != 'github.com' or len(path_parts) != 2:
            raise ValueError(f'Unsupported GitHub repository URL: {self.repository_url!r}')
        owner, repository = path_parts
        repository = repository.removesuffix('.git')
        if not owner or not repository:
            raise ValueError(f'Unsupported GitHub repository URL: {self.repository_url!r}')
        return f'{owner}/{repository}'

    @cached_property
    def releases_page(self) -> str:
        """Return the browser URL for the latest full release."""
        parsed = self.parsed_repository_url
        return f'{parsed.scheme}://{parsed.netloc}/{self.repository_slug}/releases/latest'

    @cached_property
    def latest_release_api(self) -> str:
        """Return the GitHub API endpoint for the latest full release."""
        return f'{self.GITHUB_API_ORIGIN}/repos/{self.repository_slug}/releases/latest'

    @cached_property
    def releases_api(self) -> str:
        """Return the GitHub API endpoint for published release-channel resolution."""
        return f'{self.GITHUB_API_ORIGIN}/repos/{self.repository_slug}/releases'

    @staticmethod
    def display_version(tag: str) -> str:
        """Return a normalized release version for presentation."""
        try:
            return str(Version(tag))
        except InvalidVersion:
            return tag.strip()

    @cached_property
    def prerelease_channel(self) -> bool:
        """Return whether the installed version follows published prereleases."""
        current = self.parsed_current_version
        return current is not None and (current.is_prerelease or current.is_devrelease)

    @cached_property
    def release_api(self) -> str:
        """Return the minimal GitHub API endpoint needed by the current channel."""
        return self.releases_api if self.prerelease_channel else self.latest_release_api

    def _release_candidate(
        self,
        current: Version,
        release: Mapping[object, object],
        *,
        prerelease_channel: bool,
    ) -> ReleaseCandidate | None:
        """Parse one published GitHub release into an eligible update candidate."""
        if release.get('draft') is True:
            return None
        if not prerelease_channel and release.get('prerelease') is True:
            return None

        tag = release.get('tag_name')
        if not isinstance(tag, str):
            return None

        try:
            candidate = Version(tag)
        except InvalidVersion:
            return None
        if candidate <= current:
            return None

        html_value = release.get('html_url')
        html_url = html_value.strip() if isinstance(html_value, str) else ''
        return ReleaseCandidate(
            version=candidate,
            tag=tag,
            html_url=html_url or self.releases_page,
        )

    def select_update(self, release_data: object) -> ReleaseCandidate | None:
        """Select the highest eligible release for the installed update channel."""
        current = self.parsed_current_version
        if current is None:
            return None

        prerelease_channel = current.is_prerelease or current.is_devrelease
        releases: Sequence[object]
        if _is_object_mapping(release_data):
            releases = (release_data,)
        elif _is_object_list(release_data):
            releases = release_data
        else:
            return None

        candidates: list[ReleaseCandidate] = []
        for release in releases:
            if not _is_object_mapping(release):
                continue
            candidate = self._release_candidate(
                current,
                release,
                prerelease_channel=prerelease_channel,
            )
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            return None

        return max(candidates, key=lambda candidate: candidate.version)

    def check(self) -> ReleaseCandidate | None:
        """Fetch GitHub releases and resolve an available update, if any."""
        try:
            response = requests.get(
                self.release_api,
                timeout=self.timeout,
                headers={'Accept': 'application/vnd.github+json'},
                params=(
                    {'per_page': self.PRERELEASE_PAGE_SIZE} if self.prerelease_channel else None
                ),
            )
            response.raise_for_status()
            release_data = response.json()
        except Exception:
            return None
        return self.select_update(release_data)
