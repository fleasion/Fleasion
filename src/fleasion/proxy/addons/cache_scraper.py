"""CacheScraper: intercepts and caches Roblox assets before any replacement.

This is a single instance (created by ProxyMaster) so all state is instance-level.
The GUI calls set_enabled() and clear_tracking() directly - no IPC needed since
everything runs in the same process.
"""

import base64
import gzip
import hashlib
from io import BytesIO
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urljoin, urlparse

import requests

from ...cache.cache_manager import CacheManager
from ...utils import format_count, log_buffer

try:
    import orjson

    def _loads(data):
        return orjson.loads(data)
except ImportError:
    import json

    def _loads(data):
        return json.loads(data)


logger = logging.getLogger(__name__)

ASSET_DELIVERY_HOST = 'assetdelivery.roblox.com'
CDN_HOSTS = frozenset({'fts.rbxcdn.com', 'contentdelivery.roblox.com'})
DELIVERY_ENDPOINT = '/v1/assets/batch'
CREATOR_GAME_PAGE_LIMITS = (50, 25, 10)
CREATOR_GAME_MAX_SCAN = 300
HTTPS_GET_MAX_REDIRECTS = 8
HTTPS_GET_MAX_RESPONSE_BYTES = 128 * 1024 * 1024
HTTPS_GET_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
HTTPS_GET_TRUSTED_DOMAINS = ('roblox.com', 'rbxcdn.com')

_TEXPACK_SLOT_NAMES = {
    0: 'Color',
    1: 'Normal',
    2: 'ORM',
}


def _texpack_slot_label(slot: int | None) -> str:
    if slot is None:
        return 'unknown'
    return f'{slot}:{_TEXPACK_SLOT_NAMES.get(slot, "unknown")}'


def _b64decode_padded(value: str) -> bytes:
    raw = str(value).encode('ascii', errors='ignore')
    raw += b'=' * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def _is_trusted_https_host(hostname: str) -> bool:
    host = str(hostname).strip().casefold().rstrip('.')
    return bool(host) and any(
        host == domain or host.endswith(f'.{domain}') for domain in HTTPS_GET_TRUSTED_DOMAINS
    )


def _is_roblox_host(hostname: str) -> bool:
    host = str(hostname).strip().casefold().rstrip('.')
    return host == 'roblox.com' or host.endswith('.roblox.com')


def _bounded_gzip_decompress(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=BytesIO(data)) as stream:
        result = stream.read(HTTPS_GET_MAX_DECOMPRESSED_BYTES + 1)
    if len(result) > HTTPS_GET_MAX_DECOMPRESSED_BYTES:
        raise ValueError('Gzip response exceeds the decompressed response limit')
    return result


def _bounded_zstd_decompress(data: bytes) -> bytes:
    import zstandard

    with zstandard.ZstdDecompressor().stream_reader(BytesIO(data)) as stream:
        result = stream.read(HTTPS_GET_MAX_DECOMPRESSED_BYTES + 1)
    if len(result) > HTTPS_GET_MAX_DECOMPRESSED_BYTES:
        raise ValueError('Zstandard response exceeds the decompressed response limit')
    return result


def _normalized_build_type(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            pass
    return ''.join(ch for ch in text.lower() if ch.isalnum())


def _texpack_slot_from_build_type(value) -> int | None:
    normalized = _normalized_build_type(value)
    if isinstance(normalized, int):
        return normalized if 0 <= normalized <= 2 else None
    if not isinstance(normalized, str) or not normalized:
        return None
    if any(token in normalized for token in ('color', 'albedo', 'diffuse', 'basecolor')):
        return 0
    if any(token in normalized for token in ('normal', 'bump')):
        return 1
    if (
        'metal' in normalized
        or 'rough' in normalized
        or 'emiss' in normalized
        or 'height' in normalized
        or 'displace' in normalized
        or normalized == 'orm'
    ):
        return 2
    return None


def _texpack_slot_from_request(item: dict) -> int | None:
    for key in (
        'requestedBuildType',
        'buildType',
        'textureType',
        'textureMap',
        'mapType',
        'contentType',
    ):
        slot = _texpack_slot_from_build_type(item.get(key))
        if slot is not None:
            return slot
    return None


def _texpack_build_key(item: dict):
    for key in (
        'requestedBuildType',
        'buildType',
        'textureType',
        'textureMap',
        'mapType',
        'contentType',
    ):
        value = _normalized_build_type(item.get(key))
        if value is not None:
            return value
    return None


def _normalize_asset_id(asset_id):
    if isinstance(asset_id, bool):
        return asset_id
    if isinstance(asset_id, int):
        return asset_id
    if isinstance(asset_id, str) and asset_id.isdigit():
        try:
            return int(asset_id)
        except ValueError:
            return asset_id
    return asset_id


def creator_game_base_paths(creator_id: int, creator_type: int, limit: int) -> list[str]:
    """Return games.roblox.com paths for a creator using a Roblox-supported page size."""
    if creator_type == 1:
        return [
            f'/v2/users/{creator_id}/games?sortOrder=Asc&limit={limit}',
            f'/v2/users/{creator_id}/games?accessFilter=Public&sortOrder=Asc&limit={limit}',
        ]
    if creator_type == 2:
        return [
            f'/v2/groups/{creator_id}/gamesV2?accessFilter=2&limit={limit}&sortOrder=Asc',
            f'/v2/groups/{creator_id}/gamesV2?accessFilter=1&limit={limit}&sortOrder=Asc',
        ]
    return []


def _build_texpack_request_slot_map(req_json: list) -> dict[int, int]:
    texpack_ids: set[int] = set()
    asset_counts: dict[int, int] = {}
    for item in req_json:
        if not isinstance(item, dict):
            continue
        aid = _normalize_asset_id(item.get('assetId'))
        if not isinstance(aid, int):
            continue
        asset_counts[aid] = asset_counts.get(aid, 0) + 1
        asset_type = item.get('assetTypeId')
        asset_type_name = str(item.get('assetType', '')).lower()
        if asset_type == 63 or asset_type_name == 'texturepack':
            texpack_ids.add(aid)

    for item in req_json:
        if not isinstance(item, dict):
            continue
        aid = _normalize_asset_id(item.get('assetId'))
        if not isinstance(aid, int) or aid in texpack_ids:
            continue
        if asset_counts.get(aid, 0) > 1 and (
            item.get('contentRepresentationPriorityList') is not None
            or _texpack_build_key(item) is not None
        ):
            texpack_ids.add(aid)

    result: dict[int, int] = {}
    build_slots: dict[int, dict[object, int]] = {}
    next_slot: dict[int, int] = {}
    occurrence_slot: dict[int, int] = {}

    for idx, item in enumerate(req_json):
        if not isinstance(item, dict):
            continue
        aid = _normalize_asset_id(item.get('assetId'))
        if not isinstance(aid, int) or aid not in texpack_ids:
            continue
        slot_quality = _decode_texpack_slot_quality(item)
        slot = slot_quality[0] if slot_quality is not None else None
        if slot is None:
            build_key = _texpack_build_key(item)
            if build_key is not None and asset_counts.get(aid, 0) > 1:
                slots_for_asset = build_slots.setdefault(aid, {})
                if build_key not in slots_for_asset:
                    raw_slot = next_slot.get(aid, 0)
                    slots_for_asset[build_key] = 2 if raw_slot >= 2 else raw_slot
                    next_slot[aid] = raw_slot + 1
                slot = slots_for_asset[build_key]
            elif asset_counts.get(aid, 0) > 1:
                raw_slot = occurrence_slot.get(aid, 0)
                occurrence_slot[aid] = raw_slot + 1
                slot = 2 if raw_slot >= 2 else raw_slot
            else:
                continue
        result[idx] = slot

    return result


def _representation_matches_requested(representation: dict, requested) -> bool:
    for key in (
        'requestedBuildType',
        'buildType',
        'contentType',
        'type',
        'format',
        'name',
        'representationType',
    ):
        if _normalized_build_type(representation.get(key)) == requested:
            return True
    return False


def _select_content_representation(item: dict) -> dict | None:
    crpl = item.get('contentRepresentationPriorityList')
    if not crpl:
        return None
    try:
        decoded = _loads(_b64decode_padded(crpl))
    except Exception:
        return None
    if not isinstance(decoded, list) or not decoded:
        return None

    requested = _normalized_build_type(item.get('requestedBuildType'))
    if requested is not None:
        for representation in decoded:
            if isinstance(representation, dict) and _representation_matches_requested(
                representation, requested
            ):
                return representation
        if isinstance(requested, int) and 0 <= requested < len(decoded):
            representation = decoded[requested]
            return representation if isinstance(representation, dict) else None

    if isinstance(decoded[0], dict):
        return decoded[0]
    return None


def _decode_fidelity_slot_quality(fidelity_b64: str | None) -> tuple[int, int] | None:
    if not fidelity_b64:
        return None
    try:
        fb = _b64decode_padded(fidelity_b64)
    except Exception:
        return None
    if len(fb) < 2:
        return None
    slot = (fb[0] & 0x60) >> 5
    if slot > 2:
        return None
    quality = (fb[1] & 0xC0) >> 6
    return slot, quality


def _decode_texpack_slot_quality(item: dict) -> tuple[int, int] | None:
    request_slot = _texpack_slot_from_request(item)
    if request_slot is not None:
        return request_slot, 0
    representation = _select_content_representation(item)
    if not representation:
        return None
    return _decode_fidelity_slot_quality(representation.get('fidelity'))


def _decode_selected_representation_slot_quality(item: dict) -> tuple[int, int] | None:
    representation = item.get('contentRepresentationSpecifier')
    if not isinstance(representation, dict):
        return None
    return _decode_fidelity_slot_quality(representation.get('fidelity'))


def _ktx2_pack_index(data: bytes) -> int | None:
    """Read Roblox's ``packIndex`` KVD entry from a KTX2 payload when present."""
    import struct

    if len(data) < 80 or data[:12] != b'\xabKTX 20\xbb\r\n\x1a\n':
        return None
    try:
        _dfd_offset, _dfd_length, kvd_offset, kvd_length, _sgd_offset, _sgd_length = (
            struct.unpack_from('<IIIIQQ', data, 48)
        )
    except struct.error:
        return None
    if kvd_offset < 80 or kvd_length <= 0 or kvd_offset + kvd_length > len(data):
        return None

    pos = kvd_offset
    end = kvd_offset + kvd_length
    while pos + 4 <= end:
        try:
            entry_len = struct.unpack_from('<I', data, pos)[0]
        except struct.error:
            return None
        pos += 4
        if entry_len <= 0 or pos + entry_len > end:
            return None
        entry = data[pos : pos + entry_len]
        pos += entry_len
        pos += (-pos) % 4
        if b'\x00' not in entry:
            continue
        key, value = entry.split(b'\x00', 1)
        if key != b'packIndex':
            continue
        try:
            return int(value.rstrip(b'\x00').decode('ascii'))
        except UnicodeDecodeError, ValueError:
            return None
    return None


class CacheScraper:
    """Caches Roblox assets as they are intercepted by the proxy."""

    def __init__(self, cache_manager: CacheManager) -> None:
        self.cache_manager = cache_manager
        self.enabled: bool = False

        self._lock = Lock()
        # asset_id -> {'location': str, 'assetTypeId': int, 'cached'?: True}
        self.cache_logs: dict = {}
        # base CDN URL (no query) -> list[asset_id]  (1:many – same replacement ID → same CDN URL)
        self._url_to_asset: dict[str, list] = {}
        # TexturePack sub-asset lookup: sub_asset_id -> (parent_pack_id, map_index)
        # map_index 0=Color/Albedo, 1=Normal, 2=ORM (Roughness+Metalness combined)
        # Populated when a TexturePack XML is successfully fetched and cached.
        # Lets the replacer resolve a sub-asset ID directly to the correct
        # texture slot without the user needing to know the parent pack ID.
        self._texpack_subasset_lookup: dict[int, tuple[int, int]] = {}

        # Per-slot raw KTX2 store for research/export.  One CDN URL may be
        # referenced by more than one TexturePack/slot, so keep every logical
        # association instead of letting the last batch entry overwrite earlier
        # ones before the CDN response arrives.
        self._url_to_texpack_slot: dict[str, set[tuple[int, int, int]]] = {}
        # Virtual-slot (XML position index) → ORM channel name for packs where
        # the XML has been fetched.  Virtual slot 0 = first XML sub-asset, etc.
        # Only populated for virtual slots ≥ 2 (ORM sub-channels); slots 0 and 1
        # are full-slot replacements and need no channel mapping.
        # Values: 'metalness' | 'roughness' | 'emissive' | 'height'
        self._texpack_vslot_channel: dict[tuple[int, int], str] = {}
        # Tracks which TexturePack parent IDs have had their XML pre-fetched
        # (or attempted) to avoid redundant API calls from precheck_replacements.
        self._texpack_layout_fetched: set[int] = set()

        # Background thread pool for API conversion (KTX->PNG etc.)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cache_api')
        # Incremented whenever the cache database is reset. Background jobs
        # from the previous database are then cancelled or ignored before
        # they can write stale assets back into the new one.
        self._work_generation = 0

        # Real IPs for intercepted hosts - set by ProxyMaster after DNS resolution
        # (before the hosts file is written). Keyed by hostname.
        # Used to bypass our own hosts file when making direct API calls.
        self._real_ips: dict[str, tuple[str, ...]] = {}

        # (session removed - API fetches use _https_get() with raw ssl for SNI control)

    def _submit_background(self, func, *args, generation: int | None = None):
        """Submit cache work only if it belongs to the current database generation."""
        with self._lock:
            current_generation = self._work_generation
            if generation is None:
                generation = current_generation
            if generation != current_generation:
                return None
            executor = self._executor

        try:
            return executor.submit(func, *args, generation=generation)
        except RuntimeError as exc:
            log_buffer.log('Cache', f'Failed to submit background cache work: {exc}')
            return None

    def _generation_is_current(self, generation: int | None) -> bool:
        if generation is None:
            return True
        with self._lock:
            return generation == self._work_generation

    def reset_for_cache_clear(self) -> None:
        """Cancel stale cache work and reset scraper state after Delete DB."""
        with self._lock:
            self._work_generation += 1
            old_executor = self._executor
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cache_api')
            self.cache_logs.clear()
            self._url_to_asset.clear()
            self._texpack_subasset_lookup.clear()
            self._url_to_texpack_slot.clear()
            self._texpack_vslot_channel.clear()
            self._texpack_layout_fetched.clear()

        # Do not wait for running tasks: their generation checks prevent stale
        # writes, while cancel_futures releases queued payloads immediately.
        old_executor.shutdown(wait=False, cancel_futures=True)
        self.cache_manager.clear_memory_cache()
        log_buffer.log('Cache', 'Reset in-memory cache state and cancelled stale work')

    # ------------------------------------------------------------------
    # Called from server MITM thread for assetdelivery batch responses
    # ------------------------------------------------------------------

    def process_batch_response(self, req_body: bytes, resp_body: bytes) -> None:
        """Stage 1: extract asset IDs and CDN locations from batch response."""
        if not self.enabled or not req_body or not resp_body:
            return
        try:
            req_json = _loads(req_body)
            res_json = _loads(resp_body)
        except Exception:
            return

        if not isinstance(req_json, list) or not isinstance(res_json, list):
            return

        tracked = 0
        # Late joiners: newly tracked assets whose CDN URL already has a
        # cached sibling from a previous batch.  The Roblox client won't
        # re-fetch the CDN URL, so we must copy the content ourselves.
        to_copy: list[tuple] = []  # (source_id, dest_id, asset_type, url)
        texpack_request_slots = _build_texpack_request_slot_map(req_json)

        with self._lock:
            generation = self._work_generation
            for idx, item in enumerate(req_json):
                if not isinstance(item, dict) or 'assetId' not in item:
                    continue
                asset_id = item['assetId']
                if idx >= len(res_json):
                    continue
                res_item = res_json[idx]
                if not isinstance(res_item, dict):
                    continue
                location = res_item.get('location')
                asset_type = res_item.get('assetTypeId')
                if location is None:
                    continue
                # Roblox often omits assetTypeId on the 2nd and 3rd slots of a
                # TexturePack batch (all share the same assetId).  Fall back to
                # whatever type we already recorded for this assetId in a prior
                # iteration of this same batch.
                if asset_type is None:
                    asset_type = self.cache_logs.get(asset_id, {}).get('assetTypeId')
                if asset_type is None:
                    continue
                base_url = location.split('?')[0]

                # TexturePack slot metadata must be refreshed before the
                # duplicate-URL skip below. Otherwise the first classification
                # of a reused CDN URL sticks forever and KTX2 slot exports can
                # be written under the wrong slot name.
                if asset_type == 63:
                    request_slot = texpack_request_slots.get(idx)
                    selected_quality = _decode_selected_representation_slot_quality(res_item)
                    if request_slot is not None:
                        slot_quality = (
                            request_slot,
                            selected_quality[1] if selected_quality else 0,
                        )
                    else:
                        slot_quality = None
                    if slot_quality is not None:
                        _slot, _qual = slot_quality
                        new_meta = (int(asset_id), int(_slot), int(_qual))
                        self._url_to_texpack_slot.setdefault(base_url, set()).add(new_meta)
                # Skip if this exact CDN URL is already registered.
                # Previously this was keyed by asset_id alone, which meant only
                # the FIRST batch entry for a given assetId was tracked — silently
                # dropping all subsequent texture slots (normal, metalness,
                # roughness…) that Roblox requests by sending the same assetId
                # multiple times with different requestedBuildType values.
                if base_url in self._url_to_asset:
                    continue
                if asset_id not in self.cache_logs:
                    self.cache_logs[asset_id] = {
                        'location': location,
                        'assetTypeId': asset_type,
                    }
                url_list = self._url_to_asset.setdefault(base_url, [])

                # Check if a sibling for this CDN URL is already cached
                # (from a previous batch).  If so, mark this one cached and
                # schedule a content copy instead of waiting for a CDN
                # response that will never arrive.
                cached_sibling = None
                for sibling_id in url_list:
                    sibling_info = self.cache_logs.get(sibling_id)
                    if sibling_info and 'cached' in sibling_info:
                        cached_sibling = sibling_id
                        break
                if cached_sibling is not None:
                    self.cache_logs[asset_id]['cached'] = True
                    to_copy.append((cached_sibling, asset_id, asset_type, location))

                url_list.append(asset_id)
                tracked += 1

        # Submit copy tasks outside the lock
        for source_id, dest_id, asset_type, url in to_copy:
            self._submit_background(
                self._copy_cached_asset,
                source_id,
                dest_id,
                asset_type,
                url,
                generation=generation,
            )

        if tracked > 0:
            log_buffer.log('Cache', f'Tracking {format_count(tracked, "asset")} for caching')

    # ------------------------------------------------------------------
    # Called from server MITM thread for Roblox CDN responses
    # ------------------------------------------------------------------

    def process_cdn_response(
        self, full_url: str, path: str, body: bytes, content_type: str
    ) -> None:
        """Stage 2: cache the actual CDN asset bytes."""
        if not self.enabled or not body:
            return

        base_url = full_url.split('?')[0]

        # Warn if a CDN URL arrives that was never registered via a batch
        # response — this was the original TexturePack sub-asset blind spot.
        # Should no longer fire after the dedup fix; kept as a canary.
        with self._lock:
            _known = base_url in self._url_to_asset or base_url in self._url_to_texpack_slot
        if not _known:
            _cdn_id = base_url.rsplit('/', 1)[-1]
            log_buffer.log(
                'DEBUG_CDN',
                f'[CDN UNKNOWN] URL not seen in any batch (cdn_id={_cdn_id})',
            )

        with self._lock:
            generation = self._work_generation
            # Grab every TexturePack association BEFORE the asset_ids check so
            # higher-quality CDN responses still get stored after clear_tracking()
            # clears _url_to_asset.  One CDN URL may legitimately be reused by
            # multiple parent packs or slots.
            tp_slot_metas_early = tuple(self._url_to_texpack_slot.get(base_url, ()))
            asset_ids = self._url_to_asset.get(base_url)
            if not asset_ids and not tp_slot_metas_early:
                return
            # Collect all asset IDs that still need caching for this CDN URL
            pending: list[tuple[int, int]] = []  # (asset_id, asset_type)
            if asset_ids:
                for aid in asset_ids:
                    info = self.cache_logs.get(aid)
                    if info and 'cached' not in info:
                        info['cached'] = True
                        pending.append((aid, info.get('assetTypeId', 0)))
        if not pending and not tp_slot_metas_early:
            return

        if not self._generation_is_current(generation):
            return

        cache_hash = path.rsplit('/', 1)[-1]
        metadata = {
            # Persist query-stripped URL to avoid storing signed/query params.
            'url': base_url,
            'content_type': content_type,
            'content_length': len(body),
            'hash': cache_hash,
        }

        # Decompress the body to inspect its true magic bytes.
        # The CDN often serves assets gzip-wrapped regardless of type.
        # We must look at the inner bytes to avoid misidentifying a mesh
        # as an Image or TexturePack (which causes preview failures).
        inner = body
        if body[:2] == b'\x1f\x8b':
            import gzip as _gzip

            try:
                inner = _gzip.decompress(body)
            except Exception:
                inner = body
        elif body[:4] == b'\x28\xb5\x2f\xfd':
            try:
                import zstandard

                inner = zstandard.ZstdDecompressor().decompress(
                    body, max_output_size=64 * 1024 * 1024
                )
            except Exception:
                inner = body

        # For TexturePack slots: archive the exact raw response under every
        # logical parent/slot association for this CDN URL.  The persistent
        # archive keeps all Roblox mip packs; a separate canonical slot file is
        # maintained only for callers that need the largest captured level 0.
        _KTX_MAGIC = (b'\xabKTX 20\xbb', b'\xabKTX 11\xbb')
        if tp_slot_metas_early and inner[:8] in _KTX_MAGIC:
            for _tp_id, _tp_slot, _tp_qual in tp_slot_metas_early:
                self._submit_background(
                    self._store_texpack_slot_ktx2_async,
                    _tp_id,
                    _tp_slot,
                    _tp_qual,
                    inner,
                    generation=generation,
                )

        # Store / convert for every original asset ID that shares this CDN URL
        for asset_id, asset_type in pending:
            needs_conversion = (
                asset_type in (1, 13) and inner[:8] in (b'\xabKTX 20\xbb', b'\xabKTX 11\xbb')
            ) or asset_type == 63

            if needs_conversion:
                self._submit_background(
                    self._fetch_and_update_cache,
                    asset_id,
                    asset_type,
                    full_url,
                    metadata,
                    body,
                    inner,
                    generation=generation,
                )
            else:
                self._submit_background(
                    self._store_asset_async,
                    asset_id,
                    asset_type,
                    inner,
                    full_url,
                    metadata,
                    generation=generation,
                )

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_real_ips(real_ips: dict[str, object]) -> dict[str, tuple[str, ...]]:
        normalized: dict[str, tuple[str, ...]] = {}
        for host, candidates in real_ips.items():
            host_key = str(host).strip().lower().rstrip('.')
            if not host_key:
                continue
            if isinstance(candidates, str):
                values = (candidates,)
            else:
                try:
                    values = tuple(str(value) for value in candidates)
                except TypeError:
                    values = (str(candidates),)
            unique_values = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
            if unique_values:
                normalized[host_key] = unique_values
        return normalized

    def set_real_ips(self, real_ips: dict[str, object]) -> None:
        """Called by ProxyMaster after DNS resolution (before hosts file is written).
        Stores real IP candidates so API calls can bypass our hosts file redirect.
        """
        normalized = self._normalize_real_ips(real_ips)
        with self._lock:
            self._real_ips = normalized
        log_buffer.log('Cache', f'API bypass configured for: {list(normalized.keys())}')

    def update_real_ips(self, real_ips: dict[str, object]) -> None:
        """Prefer refreshed candidates without dropping routes for other hosts."""
        refreshed = self._normalize_real_ips(real_ips)
        if not refreshed:
            return
        with self._lock:
            for host, candidates in refreshed.items():
                existing = self._real_ips.get(host, ())
                self._real_ips[host] = tuple(dict.fromkeys((*candidates, *existing)))

    def _https_get(
        self,
        hostname: str,
        path: str,
        extra_headers: dict | None = None,
        timeout: float = 8.0,
        max_redirects: int = 6,
        return_status: bool = False,
    ) -> 'bytes | None | tuple[bytes | None, int | None]':
        """Make an HTTPS GET request, bypassing our hosts file by connecting to the
        real IP while passing the original hostname as SNI and Host header.

        Uses raw ssl + http.client so we have complete control over SNI — unlike
        requests/urllib3 which uses the connection-target URL as the SNI hostname,
        breaking TLS when we swap hostname -> IP.

        Critical: we advertise Accept-Encoding: gzip, deflate but NOT zstd.
        Roblox's assetdelivery reads Accept-Encoding to decide which CDN URL to
        redirect to.  Without zstd support signalled, it redirects to the
        gzip-compressed PNG version of the asset (not the KTX2+zstd game-client
        version).  This is exactly what the original mitmproxy implementation got
        because Python's requests library sends gzip/deflate Accept-Encoding by
        default, never zstd.
        """
        import http.client
        import socket
        import ssl

        ctx = ssl.create_default_context()
        cur_hostname = str(hostname).strip().casefold().rstrip('.')
        cur_path = path if str(path).startswith('/') else f'/{path}'
        if not _is_trusted_https_host(cur_hostname):
            log_buffer.log('Cache', f'Refused untrusted HTTPS host: {cur_hostname}')
            return (None, None) if return_status else None

        redirect_limit = min(max(0, int(max_redirects)), HTTPS_GET_MAX_REDIRECTS)
        for redirect_count in range(redirect_limit + 1):
            with self._lock:
                real_ips = self._real_ips.get(cur_hostname, ())
            candidates = real_ips or (cur_hostname,)
            ssl_sock = None
            real_ip = ''
            for candidate in candidates:
                raw_sock = None
                try:
                    raw_sock = socket.create_connection((candidate, 443), timeout=timeout)
                    ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=cur_hostname)
                    real_ip = candidate
                    break
                except Exception as exc:
                    try:
                        if raw_sock is not None:
                            raw_sock.close()
                    except Exception:
                        pass
                    log_buffer.log(
                        'Cache', f'Socket connect failed {cur_hostname} ({candidate}): {exc}'
                    )

            if ssl_sock is None:
                return (None, None) if return_status else None

            try:
                conn = http.client.HTTPConnection.__new__(http.client.HTTPSConnection)
                http.client.HTTPConnection.__init__(conn, real_ip, 443, timeout=timeout)
                conn.sock = ssl_sock

                # Match the headers that Python's requests library sends by default.
                # Accept-Encoding: gzip, deflate signals we do NOT support zstd, so
                # assetdelivery redirects to the PNG CDN URL, not the KTX2+zstd one.
                req_headers = {
                    'Host': cur_hostname,
                    'User-Agent': 'Roblox/WinInet',
                    'Accept-Encoding': 'gzip, deflate',
                    'Accept': '*/*',
                    'Connection': 'close',
                }
                if extra_headers:
                    for key, value in extra_headers.items():
                        header_name = str(key)
                        if header_name.casefold() == 'host':
                            continue
                        if header_name.casefold() == 'cookie' and not _is_roblox_host(
                            cur_hostname
                        ):
                            continue
                        req_headers[header_name] = str(value)

                conn.request('GET', cur_path, headers=req_headers)
                resp = conn.getresponse()

                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get('Location', '')
                    ssl_sock.close()
                    if not location or redirect_count >= redirect_limit:
                        return (None, resp.status) if return_status else None
                    current_url = f'https://{cur_hostname}{cur_path}'
                    parsed = urlparse(urljoin(current_url, location))
                    next_hostname = (parsed.hostname or '').casefold().rstrip('.')
                    if (
                        parsed.scheme.casefold() != 'https'
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.port not in (None, 443)
                        or not _is_trusted_https_host(next_hostname)
                    ):
                        log_buffer.log('Cache', f'Refused untrusted HTTPS redirect: {location}')
                        return (None, resp.status) if return_status else None
                    cur_hostname = next_hostname
                    cur_path = parsed.path or '/'
                    if parsed.query:
                        cur_path += '?' + parsed.query
                    continue

                if resp.status == 200:
                    content_length = resp.headers.get('Content-Length')
                    if content_length:
                        try:
                            if int(content_length) > HTTPS_GET_MAX_RESPONSE_BYTES:
                                ssl_sock.close()
                                return (None, 200) if return_status else None
                        except ValueError:
                            pass
                    data = resp.read(HTTPS_GET_MAX_RESPONSE_BYTES + 1)
                    ssl_sock.close()
                    if len(data) > HTTPS_GET_MAX_RESPONSE_BYTES:
                        return (None, 200) if return_status else None
                    # Decompress gzip — assetdelivery wraps PNG in gzip when
                    # Accept-Encoding: gzip was advertised
                    ce = resp.headers.get('Content-Encoding', '').lower()
                    if ce == 'gzip' and data:
                        try:
                            data = _bounded_gzip_decompress(data)
                        except Exception:
                            return (None, 200) if return_status else None
                    elif data[:4] == b'\x28\xb5\x2f\xfd':  # zstd magic
                        try:
                            data = _bounded_zstd_decompress(data)
                        except Exception:
                            return (None, 200) if return_status else None
                    result = data if data else None
                    return (result, 200) if return_status else result

                status = resp.status
                resp.read()
                ssl_sock.close()
                return (None, status) if return_status else None
            except Exception as exc:
                try:
                    ssl_sock.close()
                except Exception:
                    pass
                log_buffer.log('Cache', f'HTTP error {cur_hostname}: {exc}')
                return (None, None) if return_status else None

        return (None, None) if return_status else None  # too many redirects

    # ------------------------------------------------------------------
    # Creator place-ID cache (class-level, shared across threads)
    # ------------------------------------------------------------------
    _creator_place_cache: dict[int, list[int]] = {}
    # Fast-path: creator_id -> last place_id that successfully downloaded an asset.
    # Avoids re-iterating the full games list for the same creator.
    _creator_last_success: dict[int, int] = {}

    def _fetch_creator_info(self, asset_id: str) -> tuple[int | None, int | None]:
        """Look up the creator ID and type for an asset via develop.roblox.com.

        Returns (creator_id, creator_type) or (None, None).
        creator_type: 1 = User, 2 = Group.
        """
        try:
            cookie = self._get_roblosecurity()
            extra = {'Accept': 'application/json'}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            raw = self._https_get(
                'develop.roblox.com',
                f'/v1/assets?assetIds={asset_id}',
                extra_headers=extra,
            )
            if not raw:
                return None, None
            import json as _json

            data = _json.loads(raw).get('data', [])
            if not data:
                return None, None
            item = data[0]
            creator_obj = item.get('creator') or {}
            creator_id = creator_obj.get('targetId') or item.get('creatorTargetId')
            creator_type = creator_obj.get('typeId') or item.get('creatorType')
            if creator_id is not None:
                creator_id = int(creator_id)
            if creator_type is not None:
                creator_type = int(creator_type)
            return creator_id, creator_type
        except Exception as exc:
            log_buffer.log('Cache', f'Creator info lookup failed for {asset_id}: {exc}')
            return None, None

    def _fetch_place_ids_for_creator(self, creator_id: int, creator_type: int) -> list[int]:
        """Get place IDs owned by the given creator, trying multiple pages.

        Uses games.roblox.com which is public and needs no auth.
        Returns a list of rootPlace.id values (may be empty).
        """
        # Check cache first
        if creator_id in self._creator_place_cache:
            cached = self._creator_place_cache[creator_id]
            return cached if isinstance(cached, list) else ([cached] if cached else [])

        try:
            if creator_type not in (1, 2):
                self._creator_place_cache[creator_id] = []
                return []

            host = 'games.roblox.com'
            place_ids: list[int] = []
            seen: set[int] = set()
            attempted: set[str] = set()

            for limit in CREATOR_GAME_PAGE_LIMITS:
                found_before_limit = len(place_ids)
                max_pages = max(1, (CREATOR_GAME_MAX_SCAN + limit - 1) // limit)

                for base_path in creator_game_base_paths(creator_id, creator_type, limit):
                    if base_path in attempted:
                        continue
                    attempted.add(base_path)
                    cursor = ''
                    for _page in range(max_pages):
                        path = base_path + (f'&cursor={cursor}' if cursor else '')
                        raw = self._https_get(
                            host, path, extra_headers={'Accept': 'application/json'}
                        )
                        if not raw:
                            break

                        import json as _json

                        resp = _json.loads(raw)
                        games = resp.get('data', [])
                        for game in games:
                            root_place = game.get('rootPlace')
                            if root_place and root_place.get('id'):
                                pid = int(root_place['id'])
                                if pid not in seen:
                                    place_ids.append(pid)
                                    seen.add(pid)

                        cursor = resp.get('nextPageCursor') or ''
                        if not cursor:
                            break

                if len(place_ids) > found_before_limit:
                    break

            self._creator_place_cache[creator_id] = place_ids
            if place_ids:
                log_buffer.log(
                    'Cache',
                    f'Found {format_count(place_ids, "place")} for creator {creator_id}',
                )
            else:
                log_buffer.log('Cache', f'No games found for creator {creator_id}')
            return place_ids
        except Exception as exc:
            log_buffer.log('Cache', f'Place ID lookup failed for creator {creator_id}: {exc}')
            self._creator_place_cache[creator_id] = []
            return []

    def _fetch_asset_with_place_id_retry(
        self,
        asset_id: str,
        extra_headers: dict | None = None,
    ) -> tuple[bytes | None, int | None]:
        """Download an asset, retrying with Roblox-Place-Id on 403.

        Tries ALL place IDs from the creator's games list until one works,
        since only the specific game that uses the asset will grant access.

        Returns (data, status_code). status_code is the final HTTP status
        (200 on success, 403/404/etc on failure).
        """
        hdrs = dict(extra_headers) if extra_headers else {}
        data, status = self._https_get(
            'assetdelivery.roblox.com',
            f'/v1/asset/?id={asset_id}',
            extra_headers=hdrs or None,
            return_status=True,
        )
        if data:
            return data, status

        if status != 403:
            return None, status

        # 403 — attempt place-ID bypass
        log_buffer.log('Cache', f'Asset {asset_id} returned 403, looking up creator...')
        creator_id, creator_type = self._fetch_creator_info(asset_id)
        if creator_id is None:
            log_buffer.log('Cache', f'Could not resolve creator for asset {asset_id}')
            return None, 403

        place_ids = self._fetch_place_ids_for_creator(creator_id, creator_type)
        if not place_ids:
            log_buffer.log('Cache', f'No places found for creator {creator_id} of asset {asset_id}')
            return None, 403

        # Fast-path: if we previously succeeded with a place ID for this creator,
        # try it first before iterating the full list.
        last_success = self._creator_last_success.get(creator_id)
        if last_success is not None and last_success in place_ids:
            log_buffer.log('Cache', f'Trying cached place {last_success} for asset {asset_id}')
            retry_hdrs = {**hdrs, 'Roblox-Place-Id': str(last_success)}
            data, status = self._https_get(
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={asset_id}',
                extra_headers=retry_hdrs,
                return_status=True,
            )
            if data:
                log_buffer.log(
                    'Cache',
                    f'Successfully downloaded privated asset {asset_id} (cached place {last_success})',
                )
                return data, status

        # Try each place ID until one works
        for place_id in place_ids:
            if place_id == last_success:
                continue  # Already tried above
            log_buffer.log('Cache', f'Trying asset {asset_id} with Roblox-Place-Id: {place_id}')
            retry_hdrs = {**hdrs, 'Roblox-Place-Id': str(place_id)}
            data, status = self._https_get(
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={asset_id}',
                extra_headers=retry_hdrs,
                return_status=True,
            )
            if data:
                log_buffer.log(
                    'Cache',
                    f'Successfully downloaded privated asset {asset_id} (place {place_id})',
                )
                self._creator_last_success[creator_id] = place_id
                return data, status

        log_buffer.log('Cache', f'All {len(place_ids)} place IDs failed for asset {asset_id}')
        return None, 403

    def _fetch_from_api(self, asset_id: str) -> bytes | None:
        """Fetch asset from Roblox delivery API for KTX->PNG / TexturePack conversion.

        The /v1/asset/ endpoint performs server-side conversion:
          - KTX textures  -> PNG
          - TexturePacks  -> XML

        Uses _https_get() which connects by real IP with correct SNI so the
        CDN TLS handshake succeeds regardless of our hosts file entries.
        Uses place-ID retry for privated assets.
        """
        try:
            cookie = self._get_roblosecurity()
            extra = {}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            data, _status = self._fetch_asset_with_place_id_retry(
                asset_id, extra_headers=extra or None
            )
            return data
        except Exception as exc:
            log_buffer.log('Cache', f'API fetch error for {asset_id}: {exc}')
        return None

    def _fetch_and_update_cache(
        self,
        asset_id: str,
        asset_type: int,
        url: str,
        metadata: dict,
        original_content: bytes | None = None,
        inner_content: bytes | None = None,
        generation: int | None = None,
    ) -> None:
        try:
            if not self._generation_is_current(generation):
                return

            # Try local KTX conversion first (KTX1 ETC and KTX2 BasisU/UASTC).
            # Falls through to API fetch if conversion returns None (unsupported format).
            if asset_type in (1, 13) and inner_content:
                try:
                    from ...cache.tools.ktx_to_png import convert as _ktx_convert

                    png_bytes = _ktx_convert(inner_content)
                except Exception:
                    png_bytes = None
                if png_bytes and png_bytes[:4] == b'\x89PNG':
                    metadata['content_length'] = len(png_bytes)
                    success = self._store_asset_if_current(
                        generation,
                        asset_id=str(asset_id),
                        asset_type=asset_type,
                        data=png_bytes,
                        url=url,
                        metadata=metadata,
                    )
                    if success:
                        log_buffer.log('Cache', f'KTX\u2192PNG (local): {asset_id}')
                    return

            api_content = self._fetch_from_api(asset_id)
            if api_content:
                is_valid = False
                content_desc = ''
                if asset_type in (1, 13) and api_content[:4] == b'\x89PNG':
                    is_valid, content_desc = True, 'PNG'
                elif asset_type == 63 and b'<roblox>' in api_content[:100]:
                    is_valid, content_desc = True, 'XML'

                if is_valid:
                    metadata['content_length'] = len(api_content)
                    success = self._store_asset_if_current(
                        generation,
                        asset_id=str(asset_id),
                        asset_type=asset_type,
                        data=api_content,
                        url=url,
                        metadata=metadata,
                    )
                    if success:
                        type_name = self.cache_manager.get_asset_type_name(asset_type)
                        log_buffer.log(
                            'Cache',
                            f'Converted {type_name} to {content_desc}: {asset_id}',
                        )
                        # For TexturePack: preserve raw KTX2 sidecar AND populate
                        # the sub-asset lookup so replacements can target sub-asset IDs.
                        if asset_type == 63:
                            if inner_content and self._generation_is_current(generation):
                                self._store_raw_asset_if_current(
                                    generation,
                                    str(asset_id),
                                    asset_type,
                                    inner_content,
                                )
                            if self._generation_is_current(generation):
                                self._populate_texpack_subasset_lookup(
                                    int(asset_id), api_content, generation=generation
                                )
                    return

            if original_content is not None:
                metadata['content_length'] = len(original_content)
                success = self._store_asset_if_current(
                    generation,
                    asset_id=str(asset_id),
                    asset_type=asset_type,
                    data=original_content,
                    url=url,
                    metadata=metadata,
                )
                if success:
                    type_name = self.cache_manager.get_asset_type_name(asset_type)
                    log_buffer.log('Cache', f'Cached {type_name} (raw fallback): {asset_id}')
        except Exception as exc:
            log_buffer.log('Cache', f'Background conversion error for {asset_id}: {exc}')
            if original_content is not None and self._generation_is_current(generation):
                try:
                    self._store_asset_if_current(
                        generation,
                        asset_id=str(asset_id),
                        asset_type=asset_type,
                        data=original_content,
                        url=url,
                        metadata=metadata,
                    )
                except Exception:
                    pass

    def _populate_texpack_subasset_lookup(
        self, parent_id: int, xml_content: bytes, generation: int | None = None
    ) -> None:
        """Parse TexturePack XML and record sub-asset → (parent, global_index) mappings.

        Fleasion global indices (fixed, asset-independent):
          0 = Color / Albedo     (fidelity slot 0, full slot)
          1 = Normal             (fidelity slot 1, full slot)
          2 = Metalness          (fidelity slot 2, ORM R channel)
          3 = Roughness          (fidelity slot 2, ORM G channel)
          4 = Emissive           (fidelity slot 2, ORM B channel)
          5 = Height             (fidelity slot 2, ORM A channel)

        The global index is FIXED and does NOT depend on the XML tag ordering.
        This means ``TexturePack:3`` ALWAYS targets Roughness regardless of
        the asset's XML structure.
        """
        # Semantic mapping: XML tag name → global index.
        _TAG_TO_GLOBAL_INDEX = {
            'color': 0,
            'albedo': 0,
            'diffuse': 0,
            'basecolor': 0,
            'normal': 1,
            'normalmap': 1,
            'bumpmap': 1,
            'metalness': 2,
            'orm': 2,
            'roughness': 3,
            'emissive': 4,
            'emissivemap': 4,
            'height': 5,
            'displacement': 5,
            'heightmap': 5,
        }
        # ORM channel name for each recognised PBR sub-tag.
        # Tags without a channel (Color, Normal, or combined 'orm') map to None.
        _TAG_TO_CHANNEL: dict[str, str | None] = {
            'color': None,
            'albedo': None,
            'diffuse': None,
            'basecolor': None,
            'normal': None,
            'normalmap': None,
            'bumpmap': None,
            'orm': None,  # full combined ORM — no single channel
            'metalness': 'metalness',
            'roughness': 'roughness',
            'emissive': 'emissive',
            'emissivemap': 'emissive',
            'height': 'height',
            'displacement': 'height',
            'heightmap': 'height',
        }
        try:
            import xml.etree.ElementTree as _ET

            root = _ET.fromstring(xml_content)
            added = 0
            virtual_slot = (
                0  # sequential index among recognised XML children (legacy, kept for vslot_channel)
            )
            for elem in root:  # direct children only — no recursion into sub-trees
                tag_lower = elem.tag.lower().lstrip('{').split('}')[-1]  # strip namespace
                global_index = _TAG_TO_GLOBAL_INDEX.get(tag_lower)
                if global_index is None:
                    continue  # unknown tag — skip (don't advance virtual_slot)
                text = (elem.text or '').strip()
                sub_id = int(text) if text.isdigit() and int(text) != 0 else None
                channel = _TAG_TO_CHANNEL.get(tag_lower)
                if text.isdigit() and int(text) != 0:
                    with self._lock:
                        if generation is not None and generation != self._work_generation:
                            return
                        self._texpack_subasset_lookup[sub_id] = (parent_id, global_index)
                    added += 1
                # Record virtual slot → channel for ORM sub-channels (legacy, kept for potential revert).
                if channel is not None:  # None means Color/Normal/full-ORM → no channel
                    with self._lock:
                        if generation is not None and generation != self._work_generation:
                            return
                        self._texpack_vslot_channel[(parent_id, virtual_slot)] = channel
                virtual_slot += 1
            if added:
                log_buffer.log(
                    'Cache',
                    f'TexturePack {parent_id}: mapped {format_count(added, "sub-asset")}',
                )
        except Exception as exc:
            log_buffer.log('Cache', f'TexturePack {parent_id} sub-asset parse error: {exc}')

    def _store_texpack_slot_ktx2_async(
        self,
        parent_id: int,
        slot: int,
        quality: int,
        ktx2_bytes: bytes,
        generation: int | None = None,
    ) -> None:
        """Persist an exact Roblox TexturePack KTX2 response and a canonical base.

        Every distinct raw response is archived permanently under the Fleasion
        database cache.  Roblox may split one logical mip chain across several
        KTX2 ``packIndex`` payloads, so none of those payloads are replaceable by
        another merely because it has a lower resolution.  Separately, the
        canonical ``slot<N>.ktx2`` file tracks only the largest captured payload
        for legacy preview/compositor callers.

        Roblox fidelity slots are physical packed textures:
          0 = Color / Albedo
          1 = Normal
          2 = ORM (R=Metalness, G=Roughness, B=Emissive, A=Height)
        """
        try:
            import struct as _struct

            if len(ktx2_bytes) < 80 or ktx2_bytes[:12] != b'\xabKTX 20\xbb\r\n\x1a\n':
                log_buffer.log(
                    'TexPackSlot',
                    f'Ignoring non-KTX2 slot payload for {parent_id} slot{slot}',
                )
                return

            new_w, new_h = _struct.unpack_from('<II', ktx2_bytes, 20)
            level_count = _struct.unpack_from('<I', ktx2_bytes, 40)[0]
            if new_w <= 0 or new_h <= 0:
                log_buffer.log(
                    'TexPackSlot',
                    f'Ignoring invalid KTX2 dimensions for {parent_id} slot{slot}: {new_w}x{new_h}',
                )
                return

            pack_index = _ktx2_pack_index(ktx2_bytes)
            digest = hashlib.sha256(ktx2_bytes).hexdigest()
            pack_path = self.cache_manager.get_texturepack_slot_pack_path(
                parent_id,
                slot,
                pack_index,
                quality,
                new_w,
                new_h,
                level_count,
                digest,
            )
            slot_path = self.cache_manager.get_texturepack_slot_path(parent_id, slot)

            # Archive and canonical selection share one lock.  Without this,
            # simultaneous 1024/512 responses could both observe "no canonical"
            # and let the smaller response win the final write by racing later.
            archived_pack = False
            canonical_action: tuple[str, int, int] | None = None
            new_rank = (new_w * new_h, max(new_w, new_h), new_w, new_h)
            with self._lock:
                if generation is not None and generation != self._work_generation:
                    return

                if not pack_path.exists():
                    pack_path.write_bytes(ktx2_bytes)
                    archived_pack = True

                existing = None
                existing_rank = (-1, -1, -1, -1)
                if slot_path.exists():
                    try:
                        existing = slot_path.read_bytes()
                        if len(existing) >= 28 and existing[:12] == b'\xabKTX 20\xbb\r\n\x1a\n':
                            existing_w, existing_h = _struct.unpack_from('<II', existing, 20)
                            existing_rank = (
                                existing_w * existing_h,
                                max(existing_w, existing_h),
                                existing_w,
                                existing_h,
                            )
                    except OSError:
                        existing = None

                if existing is None or existing_rank <= new_rank:
                    if existing != ktx2_bytes:
                        if existing is None:
                            canonical_action = ('created', 0, 0)
                        elif existing_rank < new_rank:
                            canonical_action = ('upgraded', existing_rank[2], existing_rank[3])
                        else:
                            canonical_action = ('replaced', existing_rank[2], existing_rank[3])
                        slot_path.write_bytes(ktx2_bytes)

            if archived_pack:
                pack_label = f'pack{pack_index}' if pack_index is not None else 'packunknown'
                log_buffer.log(
                    'TexPackSlot',
                    f'Archived {parent_id} slot{slot} {pack_label} q={quality} '
                    f'{new_w}x{new_h} mips={level_count} sha256={digest[:12]}',
                )

            if canonical_action is not None:
                action, old_w, old_h = canonical_action
                if action == 'upgraded':
                    log_buffer.log(
                        'TexPackSlot',
                        f'Upgrading canonical {parent_id} slot{slot}: '
                        f'{old_w}x{old_h} -> {new_w}x{new_h}',
                    )
                elif action == 'replaced':
                    log_buffer.log(
                        'TexPackSlot',
                        f'Replacing equal-resolution canonical {parent_id} slot{slot}: '
                        f'{new_w}x{new_h}, bytes changed',
                    )

                slot_name = _TEXPACK_SLOT_NAMES.get(slot, f'slot{slot}')
                log_buffer.log(
                    'TexPackSlot',
                    f'Canonical {slot_name} {new_w}x{new_h} for pack {parent_id} '
                    f'({len(ktx2_bytes)} bytes) -> {slot_path.name}',
                )
        except Exception as exc:
            log_buffer.log('TexPackSlot', f'Failed to store slot {slot} for {parent_id}: {exc}')

    def prefetch_texpack_layout(self, parent_id: int) -> None:
        """Fetch and parse TexturePack XML to populate _texpack_vslot_channel.

        Called by TextureStripper.precheck_replacements at proxy startup for
        packs that have VS≥2 local overrides configured, ensuring the ORM
        channel mapping is ready before the first batch request arrives.
        No-op if the pack has already been fetched or attempted.
        """
        with self._lock:
            if parent_id in self._texpack_layout_fetched:
                return
        # Note: _texpack_layout_fetched is only marked after the fetch completes so
        # that a concurrent batch thread isn't falsely returned early while the
        # background thread is still mid-request.  Duplicate concurrent fetches are
        # harmless since _populate_texpack_subasset_lookup is idempotent.
        try:
            xml_data = self._fetch_from_api(str(parent_id))
            if xml_data and xml_data.lstrip()[:1] == b'<':
                self._populate_texpack_subasset_lookup(parent_id, xml_data)
                log_buffer.log('Cache', f'Pre-fetched TexturePack layout for {parent_id}')
            else:
                log_buffer.log('Cache', f'TexturePack {parent_id} did not return XML for pre-fetch')
        except Exception as exc:
            log_buffer.log('Cache', f'TexturePack pre-fetch failed for {parent_id}: {exc}')
        finally:
            with self._lock:
                self._texpack_layout_fetched.add(parent_id)

    def _store_asset_async(
        self,
        asset_id: str,
        asset_type: int,
        data: bytes,
        url: str,
        metadata: dict,
        generation: int | None = None,
    ) -> None:
        try:
            success = self._store_asset_if_current(
                generation,
                asset_id=str(asset_id),
                asset_type=asset_type,
                data=data,
                url=url,
                metadata=metadata,
            )
            if success:
                type_name = self.cache_manager.get_asset_type_name(asset_type)
                log_buffer.log('Cache', f'Cached {type_name}: {asset_id} ({len(data)} bytes)')
        except Exception as exc:
            log_buffer.log('Cache', f'Cache store error for {asset_id}: {exc}')

    def _copy_cached_asset(
        self,
        source_id,
        dest_id,
        asset_type: int,
        url: str,
        generation: int | None = None,
    ) -> None:
        """Copy an already-cached asset to a new asset ID (cross-batch replication)."""
        try:
            if not self._generation_is_current(generation):
                return
            data = self.cache_manager.get_asset(str(source_id), asset_type)
            if data and self._generation_is_current(generation):
                success = self._store_asset_if_current(
                    generation,
                    asset_id=str(dest_id),
                    asset_type=asset_type,
                    data=data,
                    url=url,
                    metadata={'replicated_from': str(source_id)},
                )
                if success:
                    type_name = self.cache_manager.get_asset_type_name(asset_type)
                    log_buffer.log('Cache', f'Replicated {type_name}: {dest_id} (from {source_id})')
        except Exception as exc:
            log_buffer.log('Cache', f'Replication error {source_id}->{dest_id}: {exc}')

    def _store_asset_if_current(self, generation: int | None, **kwargs) -> bool:
        """Store an asset while holding the reset lock for this generation."""
        with self._lock:
            if generation is not None and generation != self._work_generation:
                return False
            return bool(self.cache_manager.store_asset(**kwargs))

    def _store_raw_asset_if_current(
        self, generation: int | None, asset_id: str, asset_type: int, data: bytes
    ) -> bool:
        """Store a raw sidecar only while the background job is current."""
        with self._lock:
            if generation is not None and generation != self._work_generation:
                return False
            self.cache_manager.store_raw_asset(asset_id, asset_type, data)
            return True

    # ------------------------------------------------------------------
    # DEBUG: Direct /v1/asset/ response hook (non-batch blind spot)
    # ------------------------------------------------------------------

    def process_direct_asset_response(
        self,
        path: str,
        status: int,
        location: str,
        body: bytes,
        content_type: str,
    ) -> None:
        """Called by the proxy for every non-batch assetdelivery.roblox.com response.

        This is the blind spot where TexturePack sub-asset IDs (e.g. the normal
        map referenced inside the TexturePack XML) flow through without being
        tracked by the scraper.

        In DEBUG mode we just log every observation so we can confirm the theory.
        We extract the asset ID from the path query string (``?id=<id>``) and log
        the full context so it's easy to correlate with the batch log.
        """
        if not self.enabled:
            return

        # Parse asset ID out of path like /v1/asset/?id=7547298681
        import re as _re

        _m = _re.search(r'[?&]id=(\d+)', path)
        asset_id = _m.group(1) if _m else None

        # Identify the body magic so we know what kind of asset this is
        magic = body[:8].hex() if body else 'empty'
        body_snippet = body[:64].hex() if body else 'empty'

        # Is this a 302 redirect to the CDN, or direct bytes?
        is_redirect = (status in (301, 302, 303, 307, 308)) and bool(location)

        log_buffer.log(
            'DEBUG_DIRECT',
            f'[DIRECT ASSET] asset_id={asset_id!r} '
            f'status={status} is_redirect={is_redirect} '
            f'body_len={len(body)} magic={magic} '
            f'content_type={content_type!r}',
        )

        if is_redirect:
            # The CDN URL in Location: will be fetched next as a Roblox CDN
            # request, but it WON'T be in _url_to_asset so process_cdn_response
            # will silently drop it. Log only a non-sensitive CDN id.
            _cdn_id = str(location).split('?', 1)[0].rsplit('/', 1)[-1]
            log_buffer.log(
                'DEBUG_DIRECT',
                f'[DIRECT ASSET REDIRECT] asset_id={asset_id!r} cdn_id={_cdn_id!r}  '
                f'<-- this CDN URL will NOT be caught by process_cdn_response '
                f'because it was never registered via a batch response',
            )
        else:
            # Direct bytes (rare for images but possible). Log what we got.
            log_buffer.log(
                'DEBUG_DIRECT',
                f'[DIRECT ASSET BYTES] asset_id={asset_id!r} body_head={body_snippet}',
            )

    # ------------------------------------------------------------------
    # GUI-callable interface (same as before - no change needed in GUI code)
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        log_buffer.log('Cache', f'Cache scraper {"enabled" if enabled else "disabled"}')

    def clear_tracking(self) -> None:
        with self._lock:
            self.cache_logs.clear()
            self._url_to_asset.clear()
        log_buffer.log('Cache', 'Cleared asset tracking log')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_roblosecurity(self, *, wait: bool = False) -> str | None:
        from ...utils.roblox_auth import get_roblosecurity, wait_for_roblosecurity

        if wait:
            return wait_for_roblosecurity()
        return get_roblosecurity()
