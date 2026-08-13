"""Retrieve and cache the public Roblox FastFlag catalog."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import ClassVar, Final

from ..utils import APP_CACHE_DIR
from ..utils.http import http_get

FastFlagValues = dict[str, str | None]


class FastFlagCatalog:
    """Merge published ClientSettings values with public tracker variable names."""

    SETTINGS_URL: Final = (
        'https://clientsettingscdn.roblox.com/v2/settings/application/PCDesktopClient'
    )
    SETTINGS_APPLICATIONS: Final = (
        'PCDesktopClient',
        'MacDesktopClient',
        'PlayStationClient',
        'XboxClient',
        'iOSApp',
        'UWPApp',
        'AndroidApp',
        'PCStudioApp',
        'MacStudioApp',
        'PCStudioBootstrapper',
        'MacStudioBootstrapper',
        'PCClientBootstrapper',
        'MacClientBootstrapper',
    )
    SETTINGS_BUCKETS: Final = ('', '/bucket/zcanary', '/bucket/zintegration')
    TRACKER_VARIABLES_URL: Final = (
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/roblox/FVariables.txt'
    )
    HISTORICAL_TRACKER_VARIABLES_URL: Final = (
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/'
        '03a46e5f35e7aa5d85310189b477caee20b20761/FVariables.txt'
    )
    BYPASS_CUSTOM_FFLAGS_HEADER: Final = {'X-Fleasion-Bypass-Custom-FFlags': '1'}
    FAMILIES: Final = (
        'DFFlag',
        'DFInt',
        'DFLog',
        'DFString',
        'DFFloat',
        'FFlag',
        'FInt',
        'FLog',
        'FString',
        'FFloat',
    )
    CACHE_TTL_SECONDS: Final = 60 * 60
    CACHE_VERSION: Final = 1
    cache_path: ClassVar[Path] = APP_CACHE_DIR / 'fflag_browser.json'

    @classmethod
    def family_for(cls, name: str) -> str:
        """Return the FastVariable prefix family for a name."""
        return next((family for family in cls.FAMILIES if name.startswith(family)), 'Other')

    @staticmethod
    def extract_flags(payload: object) -> dict[str, str]:
        """Validate and normalize a public ClientSettings response."""
        if not isinstance(payload, dict):
            raise ValueError('Roblox returned an invalid FastFlag response.')
        settings = payload.get('applicationSettings')
        if not isinstance(settings, dict):
            raise ValueError('Roblox returned no application FastFlags.')

        flags: dict[str, str] = {}
        for raw_name, raw_value in settings.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_value, str | int | float | bool):
                continue
            flags[name] = (
                'True' if raw_value is True else 'False' if raw_value is False else str(raw_value)
            )
        if not flags:
            raise ValueError('Roblox returned no usable FastFlags.')
        return flags

    @classmethod
    def extract_tracker_flags(cls, payload: bytes) -> dict[str, None]:
        """Extract known FastVariable names from a public Client Tracker list."""
        try:
            lines = payload.decode('utf-8').splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError('The FastVariable tracker returned invalid text.') from exc

        flags: dict[str, None] = {}
        for line in lines:
            _, marker, raw_name = line.partition('] ')
            if not marker:
                continue
            name = raw_name.strip()
            if name.startswith(cls.FAMILIES):
                flags[name] = None
        if not flags:
            raise ValueError('The FastVariable tracker returned no usable FastFlags.')
        return flags

    @classmethod
    def settings_urls(cls) -> tuple[str, ...]:
        """Return stable, canary, and integration ClientSettings endpoints."""
        base_url = 'https://clientsettingscdn.roblox.com/v2/settings/application/'
        urls = [cls.SETTINGS_URL]
        urls.extend(
            f'{base_url}{application}{bucket}'
            for bucket in cls.SETTINGS_BUCKETS
            for application in cls.SETTINGS_APPLICATIONS
            if f'{base_url}{application}{bucket}' != cls.SETTINGS_URL
        )
        return tuple(urls)

    @classmethod
    def read_cache(cls, *, now: float | None = None) -> FastFlagValues | None:
        """Return the recent catalog when its optional cache is valid."""
        try:
            cached = json.loads(cls.cache_path.read_text(encoding='utf-8'))
            if cached.get('version') != cls.CACHE_VERSION:
                return None
            fetched_at = float(cached['fetched_at'])
            age = (time.time() if now is None else now) - fetched_at
            raw_flags = cached['flags']
            if not 0 <= age < cls.CACHE_TTL_SECONDS or not isinstance(raw_flags, dict):
                return None
        except OSError, ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError:
            return None

        flags: FastFlagValues = {}
        for raw_name, value in raw_flags.items():
            name = raw_name.strip() if isinstance(raw_name, str) else ''
            if name.startswith(cls.FAMILIES) and (value is None or isinstance(value, str)):
                flags[name] = value
        return flags or None

    @classmethod
    def write_cache(cls, flags: FastFlagValues, *, now: float | None = None) -> None:
        """Atomically persist a catalog union for the next hour."""
        temporary_path = cls.cache_path.with_name(f'.{cls.cache_path.name}.tmp')
        try:
            cls.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(
                    {
                        'version': cls.CACHE_VERSION,
                        'fetched_at': time.time() if now is None else now,
                        'flags': flags,
                    },
                    separators=(',', ':'),
                ),
                encoding='utf-8',
            )
            temporary_path.replace(cls.cache_path)
        except OSError:
            pass
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    @classmethod
    def fetch(cls) -> FastFlagValues:
        """Fetch and merge all configured sources."""
        flags: FastFlagValues = {}
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix='fleasion-fflags') as executor:
            futures = {
                executor.submit(http_get, url, 20, cls.BYPASS_CUSTOM_FFLAGS_HEADER): url
                for url in cls.settings_urls()
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    settings = cls.extract_flags(json.loads(future.result()))
                except OSError, ValueError, json.JSONDecodeError:
                    continue
                if url == cls.SETTINGS_URL:
                    flags.update(settings)
                else:
                    flags.update({name: None for name in settings if name not in flags})

        for tracker_url in (cls.TRACKER_VARIABLES_URL, cls.HISTORICAL_TRACKER_VARIABLES_URL):
            try:
                tracker_flags = cls.extract_tracker_flags(http_get(tracker_url, timeout=20))
            except OSError, ValueError:
                continue
            flags.update(
                {name: value for name, value in tracker_flags.items() if name not in flags}
            )

        if not flags:
            raise ValueError('No configured FastFlag source returned usable data.')
        cls.write_cache(flags)
        return flags

    @classmethod
    def load(cls, *, force: bool = False) -> FastFlagValues:
        """Return a fresh-enough cached catalog or fetch a new one."""
        if not force and (cached := cls.read_cache()) is not None:
            return cached
        return cls.fetch()
