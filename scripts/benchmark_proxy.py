"""Run repeatable, local proxy CPU microbenchmarks without Roblox traffic."""

# ruff: file-ignore[import-private-name]

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from fleasion.proxy.addons.cache_scraper import CacheScraper
from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.h2_transport import (
    _valid_h2_request_target,  # pyright: ignore[reportPrivateUsage]
)
from fleasion.proxy.server import (
    _FlowResponse,  # pyright: ignore[reportPrivateUsage]
    _read_headers_raw,  # pyright: ignore[reportPrivateUsage]
    _read_http_body_bytes,  # pyright: ignore[reportPrivateUsage]
    _serve_local_file,  # pyright: ignore[reportPrivateUsage]
)
from fleasion.proxy.tls_records import TrafficKeys
from fleasion.utils import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.cache.cache_manager import CacheManager
    from fleasion.config.manager import ConfigManager
    from fleasion.config.types import ReplacementMaps

log = logging.getLogger(__name__)


class _BatchConfig:
    @staticmethod
    def get_all_replacements() -> ReplacementMaps:
        return {99999: 88888}, set(), {}, {}


def _measure(label: str, operation: Callable[[], object], iterations: int) -> None:
    samples: list[tuple[float, float]] = []
    for _ in range(5):
        start_wall = time.perf_counter()
        start_cpu = time.process_time()
        for _ in range(iterations):
            operation()
        samples.append((time.perf_counter() - start_wall, time.process_time() - start_cpu))
    wall = statistics.median(sample[0] for sample in samples) * 1e6 / iterations
    cpu = statistics.median(sample[1] for sample in samples) * 1e6 / iterations
    log.info('%s: %.2f us wall, %.2f us CPU per operation', label, wall, cpu)


async def _measure_http(iterations: int) -> None:
    headers = (
        b'GET /asset?id=123 HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n'
        b'Accept: */*\r\nUser-Agent: Roblox\r\nConnection: keep-alive\r\n\r\n'
    )
    payload = b'x' * 65536
    samples: list[tuple[float, float]] = []
    for _ in range(5):
        start_wall = time.perf_counter()
        start_cpu = time.process_time()
        for _ in range(iterations):
            reader = asyncio.StreamReader()
            reader.feed_data(headers + payload)
            reader.feed_eof()
            parsed = await _read_headers_raw(reader)
            body = await _read_http_body_bytes(reader, len(payload), context='benchmark')
            if parsed is None or body != payload:
                msg = 'HTTP parsing changed the benchmark message'
                raise RuntimeError(msg)
        samples.append((time.perf_counter() - start_wall, time.process_time() - start_cpu))
    wall = statistics.median(sample[0] for sample in samples) * 1e6 / iterations
    cpu = statistics.median(sample[1] for sample in samples) * 1e6 / iterations
    log.info('HTTP/1 headers and 64 KiB body: %.2f us wall, %.2f us CPU', wall, cpu)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=10000)
    parser.add_argument('--include-file', action='store_true', help='Benchmark local file ranges')
    parser.add_argument('--include-batch', action='store_true', help='Benchmark 1,000-item batches')
    parser.add_argument('--include-scraper', action='store_true', help='Benchmark shared CDN URLs')
    parser.add_argument('--include-json', action='store_true', help='Benchmark proxy JSON parsing')
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error('--iterations must be positive')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    keys = TrafficKeys(bytes(32), 0x1301)
    payload = b'x' * 16384
    path = b'/v1/asset/?id=1234567890&version=1'
    _measure('TLS 1.3 encrypt 16 KiB', lambda: keys.encrypt(payload), args.iterations)
    _measure(
        'HTTP/2 request target', lambda: _valid_h2_request_target(b'GET', path), args.iterations
    )
    asyncio.run(_measure_http(args.iterations))
    if args.include_json:
        expected_json = {
            'items': [
                {'id': index, 'values': [True, None, {'name': 'asset'}]} for index in range(2000)
            ]
        }
        json_response = _FlowResponse(b'HTTP/1.1 200 OK', json.dumps(expected_json).encode())

        def parse_json() -> None:
            if json_response.json() != expected_json:
                msg = 'Parsed JSON differs from the original response'
                raise ValueError(msg)

        _measure('Parse 2,000-item proxy JSON', parse_json, min(args.iterations, 100))
    if args.include_batch:
        config = _BatchConfig()
        stripper = TextureStripper(cast('ConfigManager', config))
        replacements = config.get_all_replacements()
        body = json.dumps(
            [
                {'assetId': index, 'assetType': 'MeshPart', 'requestId': str(index)}
                for index in range(1000)
            ],
            separators=(',', ':'),
        ).encode('utf-8')

        def process_batch() -> None:
            result = stripper.process_batch_request(body, {}, replacements, 'benchmark')
            if result != (body, body):
                msg = 'Unmodified batch did not retain its original bytes'
                raise ValueError(msg)

        _measure('Unmodified 1,000-item asset batch', process_batch, min(args.iterations, 100))
    if args.include_scraper:
        scraper = CacheScraper(cast('CacheManager', None))
        scraper.enabled = True
        ids = list(range(1, 3001))
        shared_url = 'https://cdn.example.test/shared'
        requests = json.dumps([{'assetId': asset_id} for asset_id in ids]).encode()
        responses = json.dumps([{'location': shared_url, 'assetTypeId': 1} for _ in ids]).encode()
        unique_urls = [f'https://cdn.example.test/{asset_id}' for asset_id in ids]
        unique_responses = json.dumps(
            [{'location': url, 'assetTypeId': 1} for url in unique_urls]
        ).encode()
        expected_unique = {url: [asset_id] for url, asset_id in zip(unique_urls, ids, strict=True)}

        def process_scraper_batch(response: bytes, *, shared: bool) -> None:
            scraper.clear_tracking()
            scraper.process_batch_response(requests, response)
            tracked = cast('dict[str, list[int | str]]', scraper.__dict__['_url_to_asset'])
            expected = {shared_url: ids} if shared else expected_unique
            if tracked != expected:
                msg = 'CDN associations changed or were reordered'
                raise ValueError(msg)

        with patch.object(log_buffer, 'log'):
            _measure(
                '3,000 assets sharing a CDN URL',
                lambda: process_scraper_batch(responses, shared=True),
                min(args.iterations, 10),
            )
            _measure(
                '3,000 assets with distinct CDN URLs',
                lambda: process_scraper_batch(unique_responses, shared=False),
                min(args.iterations, 10),
            )
    if args.include_file:
        with tempfile.TemporaryDirectory(prefix='fleasion-benchmark-') as directory:
            local_path = Path(directory) / 'asset.ogg'
            with local_path.open('wb') as output:
                output.truncate(16 * 1024 * 1024)
            _measure(
                '128-byte range of 16 MiB local asset',
                lambda: _serve_local_file(str(local_path), b'bytes=0-127'),
                min(args.iterations, 100),
            )


if __name__ == '__main__':
    main()
