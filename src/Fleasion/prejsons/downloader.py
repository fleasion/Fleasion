"""PreJsons downloader module."""

import json
import urllib.error
import urllib.request

from ..utils import CLOG_URL, ORIGINALS_DIR, REPLACEMENTS_DIR, log_buffer


def download_prejsons():
    """Download pre-configured JSON files from CLOG.json on startup."""
    try:
        ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        REPLACEMENTS_DIR.mkdir(parents=True, exist_ok=True)

        log_buffer.log('PreJsons', 'Fetching game configurations...')

        req = urllib.request.Request(
            CLOG_URL, headers={'User-Agent': 'FleasionNT/1.2.0'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            clog_data = json.loads(response.read().decode('utf-8'))

        games = clog_data.get('games', {})
        log_buffer.log('PreJsons', f'Found {len(games)} game(s) to process')

        for game_name, game_config in games.items():
            github_url = game_config.get('github')
            if github_url:
                try:
                    req = urllib.request.Request(
                        github_url, headers={'User-Agent': 'FleasionNT/1.2.0'}
                    )
                    with urllib.request.urlopen(req, timeout=15) as response:
                        content = response.read()
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
                    req = urllib.request.Request(
                        replacement_url, headers={'User-Agent': 'FleasionNT/1.2.0'}
                    )
                    with urllib.request.urlopen(req, timeout=15) as response:
                        content = response.read()
                    filepath = REPLACEMENTS_DIR / f'{game_name}.json'
                    filepath.write_bytes(content)
                    log_buffer.log('PreJsons', f'Downloaded replacement: {game_name}')
                except (urllib.error.URLError, OSError) as e:
                    log_buffer.log('PreJsons', f'Failed replacement {game_name}: {e}')

        log_buffer.log('PreJsons', 'Download complete')
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log_buffer.log('PreJsons', f'Failed to fetch CLOG.json: {e}')
