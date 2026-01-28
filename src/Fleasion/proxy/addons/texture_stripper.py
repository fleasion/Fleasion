"""Texture stripper addon for mitmproxy."""

import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http

from ...utils import PROXY_TARGET_HOST, STRIPPABLE_ASSET_TYPES, log_buffer


class TextureStripper:
    """Mitmproxy addon that strips textures and performs asset replacements."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        # Maps flow ID to requestId we're tracking
        self.pending_requests: dict[str, tuple[str, str, str]] = {}  # flow_id -> (requestId, url_type, url_value)
        # Maps CDN URLs to replacement URLs/paths
        self.cdn_redirects: dict[str, str] = {}
        self.local_redirects: dict[str, str] = {}
        # Cache replacement rules per flow to avoid multiple disk reads
        self._replacements_cache: dict[str, tuple] = {}  # flow_id -> (replacements, removals, cdn, local)

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

    def _get_replacements(self, flow_id: str) -> tuple:
        """Get cached replacement rules for a flow, or load from disk if not cached."""
        if flow_id not in self._replacements_cache:
            self._replacements_cache[flow_id] = self.config_manager.get_all_replacements()
        return self._replacements_cache[flow_id]

    def _clear_flow_cache(self, flow_id: str):
        """Clear cached replacements for a completed flow."""
        self._replacements_cache.pop(flow_id, None)

    def request(self, flow: http.HTTPFlow):
        """Process request and apply modifications."""
        url = flow.request.pretty_url

        # Check for CDN redirect intercepts
        for cdn_url, redirect_url in list(self.cdn_redirects.items()):
            if cdn_url in url:
                log_buffer.log('CDN', f'Redirecting to: {redirect_url}')
                flow.response = http.Response.make(302, b'', {'Location': redirect_url})
                # Remove from tracking after redirect
                del self.cdn_redirects[cdn_url]
                return

        # Check for local file intercepts
        for cdn_url, local_path in list(self.local_redirects.items()):
            if cdn_url in url:
                try:
                    path = Path(local_path)
                    if path.exists():
                        content = path.read_bytes()
                        # Determine content type from extension
                        ext = path.suffix.lower()
                        content_types = {
                            '.png': 'image/png',
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.gif': 'image/gif',
                            '.webp': 'image/webp',
                            '.ogg': 'audio/ogg',
                            '.mp3': 'audio/mpeg',
                            '.wav': 'audio/wav',
                            '.rbxm': 'application/octet-stream',
                            '.rbxmx': 'application/xml',
                        }
                        content_type = content_types.get(ext, 'application/octet-stream')
                        flow.response = http.Response.make(
                            200,
                            content,
                            {'Content-Type': content_type, 'Content-Length': str(len(content))}
                        )
                        log_buffer.log('Local', f'Served local file: {path.name}')
                    else:
                        log_buffer.log('Local', f'File not found: {local_path}')
                except OSError as e:
                    log_buffer.log('Local', f'Error reading file: {e}')
                # Remove from tracking after attempt
                del self.local_redirects[cdn_url]
                return

        # Process batch asset requests
        if (
            urlparse(url).hostname != PROXY_TARGET_HOST
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
        # Use cached replacements to avoid repeated disk I/O
        replacements, removals, cdn_replacements, local_replacements = self._get_replacements(flow.id)

        # Track asset IDs that need CDN/local replacement for response processing
        for e in data:
            if not isinstance(e, dict):
                continue
            aid = e.get('assetId')
            req_id = e.get('requestId')
            if aid and req_id:
                if aid in cdn_replacements:
                    self.pending_requests[f'{flow.id}_{req_id}'] = (req_id, 'cdn', cdn_replacements[aid])
                    log_buffer.log('CDN', f'Tracking asset {aid} for CDN redirect')
                elif aid in local_replacements:
                    self.pending_requests[f'{flow.id}_{req_id}'] = (req_id, 'local', local_replacements[aid])
                    log_buffer.log('Local', f'Tracking asset {aid} for local replacement')

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

        # Replace assets (ID mode) and strip textures
        for e in data:
            if not isinstance(e, dict):
                continue

            # Asset replacement (ID mode only)
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

    def _modify_texturepack_xml(self, content: bytes, replacements: dict[int, int]) -> bytes | None:
        """Modify texturepack XML to replace nested asset IDs.

        Returns modified XML bytes if any changes were made, None otherwise.
        """
        try:
            xml_text = content.decode('utf-8', errors='replace')
            root = ET.fromstring(xml_text)

            modified = False
            for elem_name in ['color', 'normal', 'metalness', 'roughness']:
                node = root.find(elem_name)
                if node is not None and node.text:
                    try:
                        nested_id = int(node.text)
                        if nested_id in replacements:
                            new_id = replacements[nested_id]
                            node.text = str(new_id)
                            log_buffer.log('TexturePack', f'Replaced {elem_name} ID {nested_id} -> {new_id}')
                            modified = True
                    except ValueError:
                        pass

            if modified:
                # Return modified XML
                return ET.tostring(root, encoding='unicode').encode('utf-8')
            return None
        except ET.ParseError:
            return None

    def response(self, flow: http.HTTPFlow):
        """Process response to capture CDN URLs for redirection."""
        url = flow.request.pretty_url

        # Handle texturepack XML responses - modify nested asset IDs directly
        content_type = flow.response.headers.get('Content-Type', '') if flow.response else ''
        if flow.response and flow.response.raw_content and 'xml' in content_type.lower():
            # Use cached replacements
            replacements, removals, cdn_replacements, local_replacements = self._get_replacements(flow.id)
            # Try to modify texturepack XML with ID replacements
            if replacements:
                modified_xml = self._modify_texturepack_xml(flow.response.raw_content, replacements)
                if modified_xml:
                    flow.response.raw_content = modified_xml
                    flow.response.headers['Content-Length'] = str(len(modified_xml))
                    log_buffer.log('TexturePack', 'Modified texturepack XML with ID replacements')

        if (
            urlparse(url).hostname != PROXY_TARGET_HOST
            or not flow.response
            or not flow.response.raw_content
        ):
            return

        enc = flow.response.headers.get('Content-Encoding', '').lower()
        try:
            data = self._decode(flow.response.raw_content, enc)
        except (json.JSONDecodeError, gzip.BadGzipFile, OSError):
            return

        if not isinstance(data, list):
            return

        # Find CDN URLs from response and set up redirects
        for item in data:
            if not isinstance(item, dict):
                continue

            req_id = item.get('requestId')
            location = item.get('location')

            if not req_id or not location:
                continue

            # Check if we're tracking this request
            key = f'{flow.id}_{req_id}'
            if key in self.pending_requests:
                _, url_type, url_value = self.pending_requests.pop(key)
                if url_type == 'cdn':
                    self.cdn_redirects[location] = url_value
                    log_buffer.log('CDN', f'Will redirect {location[:50]}...')
                elif url_type == 'local':
                    self.local_redirects[location] = url_value
                    log_buffer.log('Local', f'Will serve local file for {location[:50]}...')

        # Clear cache for this flow after response is complete
        self._clear_flow_cache(flow.id)
