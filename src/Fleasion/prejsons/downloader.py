"""PreJsons downloader module."""

import json
import urllib.error

from ..utils import CLOG_URL, ORIGINALS_DIR, REPLACEMENTS_DIR, format_count, log_buffer
from ..utils.http import http_get


def download_prejsons():
    """Download pre-configured JSON files from CLOG.json on startup."""
    try:
        ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        REPLACEMENTS_DIR.mkdir(parents=True, exist_ok=True)

        log_buffer.log('PreJsons', 'Fetching game configurations...')

        clog_data = json.loads(http_get(CLOG_URL, timeout=15).decode('utf-8'))

        games = clog_data.get('games', {})
        log_buffer.log('PreJsons', f'Found {format_count(games, "game")} to process')

        for game_name, game_config in games.items():
            github_url = game_config.get('github')
            if github_url:
                try:
                    content = http_get(github_url, timeout=15)
                    filepath = ORIGINALS_DIR / f'{game_name}.json'
                    filepath.write_bytes(content)
                    log_buffer.log('PreJsons', f'Downloaded original: {game_name}')
                except (urllib.error.URLError, OSError) as e:
                    log_buffer.log('PreJsons', f'Failed original {game_name}: {e}')

            replacement_url = game_config.get('replacement') or game_config.get(
                'Replacement'
            )
            if replacement_url:
                try:
                    content = http_get(replacement_url, timeout=15)
                    filepath = REPLACEMENTS_DIR / f'{game_name}.json'
                    filepath.write_bytes(content)
                    log_buffer.log('PreJsons', f'Downloaded replacement: {game_name}')
                except (urllib.error.URLError, OSError) as e:
                    log_buffer.log('PreJsons', f'Failed replacement {game_name}: {e}')

        log_buffer.log('PreJsons', 'Download complete')
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log_buffer.log('PreJsons', f'Failed to fetch CLOG.json: {e}')
