"""Fetch unmodified Roblox avatar metadata without using Fleasion's interceptor.

The proxy remembers true upstream IPs before changing system hosts entries.
Connect to those IPs directly while preserving TLS SNI, hostname validation,
and HTTP Host. Never fall back to OS name resolution: it may resolve to Fleasion.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
from contextlib import closing
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode, urlsplit

import certifi
import urllib3

from fleasion.utils.json_types import as_json_object

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fleasion.proxy.upstream import UpstreamEndpoint

THUMBNAIL_ORIGIN = 'thumbnails.roblox.com'
_MAX_METADATA_BYTES = 128 * 1024


def _read_direct_upstream(endpoint: UpstreamEndpoint, path: str) -> bytes | None:
    """Return bounded metadata over a verified upstream TLS connection."""
    if not endpoint.ip:
        return None
    try:
        address = ipaddress.ip_address(endpoint.ip)
    except ValueError:
        return None
    if not address.is_global:
        return None
    try:
        with (
            closing(
                urllib3.HTTPSConnectionPool(
                    endpoint.ip,
                    port=endpoint.port,
                    server_hostname=THUMBNAIL_ORIGIN,
                    assert_hostname=THUMBNAIL_ORIGIN,
                    cert_reqs=ssl.CERT_REQUIRED,
                    ca_certs=certifi.where(),
                )
            ) as pool,
            closing(
                pool.urlopen(
                    'GET',
                    path,
                    headers={'Host': THUMBNAIL_ORIGIN, 'Accept-Encoding': 'identity'},
                    retries=False,
                    redirect=False,
                    preload_content=False,
                    timeout=urllib3.Timeout(connect=4.0, read=5.0),
                )
            ) as response,
        ):
            if response.status != 200:
                return None
            payload = response.read(_MAX_METADATA_BYTES + 1)
            return payload if len(payload) <= _MAX_METADATA_BYTES else None
    except OSError, urllib3.exceptions.HTTPError, ssl.SSLError:
        return None


def fetch_original_avatar_url(user_id: str, endpoints: Sequence[UpstreamEndpoint]) -> str:
    """Return a verified original avatar URL, or '' when bypass is unavailable."""
    if not user_id.isascii() or not user_id.isdigit() or int(user_id) <= 0:
        return ''
    query = urlencode({'userIds': user_id, 'size': '150x150', 'format': 'Png'})
    path = f'/v1/users/avatar-headshot?{query}'
    for endpoint in endpoints:
        body = _read_direct_upstream(endpoint, path)
        if body is None:
            continue
        try:
            payload = as_json_object(json.loads(body))
        except ValueError, UnicodeDecodeError:
            continue
        if payload is None:
            continue
        entries = payload.get('data')
        if not isinstance(entries, list):
            continue
        for item in cast('list[object]', entries):
            entry = as_json_object(item)
            if entry is None or str(entry.get('targetId')) != user_id:
                continue
            image_url = entry.get('imageUrl')
            if isinstance(image_url, str) and urlsplit(image_url).scheme == 'https':
                return image_url
    return ''
