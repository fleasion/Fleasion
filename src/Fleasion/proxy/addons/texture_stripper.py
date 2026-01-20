"""Texture stripper addon for mitmproxy."""

import gzip
import json
from urllib.parse import urlparse

from ...utils import PROXY_TARGET_HOST, STRIPPABLE_ASSET_TYPES, log_buffer


class TextureStripper:
    """Mitmproxy addon that strips textures and performs asset replacements."""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    @staticmethod
    def _decode(content: bytes, enc: str):
        """Decode content based on encoding."""
        if enc == 'gzip':
            content = gzip.decompress(content)
        return json.loads(content)

    @staticmethod
    def _encode(data, enc: str) -> bytes:
        """Encode data based on encoding."""
        raw = json.dumps(data, separators=(',', ':')).encode()
        return gzip.compress(raw) if enc == 'gzip' else raw

    def request(self, flow):
        """Process request and apply modifications."""
        if (
            urlparse(flow.request.pretty_url).hostname != PROXY_TARGET_HOST
            or not flow.request.raw_content
        ):
            return

        enc = flow.request.headers.get('Content-Encoding', '').lower()
        try:
            data = self._decode(flow.request.raw_content, enc)
        except (json.JSONDecodeError, gzip.BadGzipFile, OSError):
            return

        if not isinstance(data, list):
            return

        modified = False
        replacements, removals = self.config_manager.get_all_replacements()

        # Remove assets
        original_len = len(data)
        data[:] = [
            e
            for e in data
            if not (isinstance(e, dict) and e.get('assetId') in removals)
        ]
        if (removed := original_len - len(data)) > 0:
            log_buffer.log('Remover', f'Removed {removed} asset(s)')
            modified = True

        # Replace assets and strip textures
        for e in data:
            if not isinstance(e, dict):
                continue

            # Asset replacement
            if (aid := e.get('assetId')) in replacements:
                e['assetId'] = replacements[aid]
                log_buffer.log('Replacer', f'Replaced {aid} -> {replacements[aid]}')
                modified = True

            # Texture stripping
            if (
                self.config_manager.strip_textures
                and e.get('assetType') in STRIPPABLE_ASSET_TYPES
                and e.pop('contentRepresentationPriorityList', None) is not None
            ):
                log_buffer.log('Stripper', f"Removed texture priority: {e['assetType']}")
                modified = True

        if modified:
            flow.request.raw_content = self._encode(data, enc)
            flow.request.headers['Content-Length'] = str(len(flow.request.raw_content))
