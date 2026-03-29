"""Cache viewer tab - simplified version for viewing cached assets."""

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QLabel, QComboBox, QLineEdit, QMessageBox,
    QHeaderView, QFileDialog, QGroupBox, QSplitter, QTextEdit, QCheckBox,
    QMenu, QScrollArea, QGridLayout, QFrame, QDialog
)
from PyQt6.QtWidgets import QWidgetAction
from PyQt6.QtGui import QPixmap, QImage, QAction, QCursor, QFont, QFontDatabase, QPalette, QColor
from PIL import Image
import io
import threading
import gzip as gzip_module

from .cache_manager import CacheManager
from .obj_viewer import ObjViewerPanel
from .audio_player import AudioPlayerWidget
from .animation_viewer import AnimationViewerPanel
from .cache_json_viewer import CacheJsonViewer
from .font_viewer import FontViewerWidget
from . import mesh_processing
from ..utils import log_buffer, open_folder
import json


class NumericSortItem(QTableWidgetItem):
    """Custom table item that sorts based on a numeric value rather than text."""
    def __init__(self, numeric_val, text):
        super().__init__(text)
        self.numeric_val = numeric_val

    def __lt__(self, other):
        if isinstance(other, NumericSortItem):
            return self.numeric_val < other.numeric_val
        return super().__lt__(other)


class SearchWorkerThread(QThread):
    '''Worker thread for filtering assets without blocking UI.'''

    results_ready = pyqtSignal(list)

    def __init__(self, assets: list, search_text: str, asset_info: dict):
        super().__init__()
        self.assets = assets
        self.search_text = search_text.strip().lower()
        self.asset_info = asset_info
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        '''Filter assets in background thread.'''
        if not self.search_text or self._stop_requested:
            self.results_ready.emit(self.assets)
            return

        filtered = []
        batch_size = 100  # Process in batches to allow interruption

        for i in range(0, len(self.assets), batch_size):
            if self._stop_requested:
                return

            batch = self.assets[i:i + batch_size]

            for a in batch:
                if self._stop_requested:
                    return

                asset_id = a['id']

                # Fast path: check ID first
                if self.search_text in asset_id.lower():
                    filtered.append(a)
                    continue

                # Check type name
                if self.search_text in a['type_name'].lower():
                    filtered.append(a)
                    continue

                # Check resolved name if available
                if asset_id in self.asset_info:
                    info = self.asset_info[asset_id]
                    name = info.get('resolved_name')
                    if name and self.search_text in name.lower():
                        filtered.append(a)
                        continue
                    creator_name = info.get('creator_name')
                    if creator_name and self.search_text in creator_name.lower():
                        filtered.append(a)
                        continue

                # Check other fields
                url = a.get('url', '').lower()
                hash_val = a.get('hash', '').lower()
                cached_at = a.get('cached_at', '').lower()

                if (self.search_text in url or
                    self.search_text in hash_val or
                    self.search_text in cached_at):
                    filtered.append(a)

        if not self._stop_requested:
            self.results_ready.emit(filtered)


class DeleteWorkerThread(QThread):
    '''Worker thread for deleting multiple assets without blocking UI.'''

    progress = pyqtSignal(int, int)  # (current, total)
    deletion_complete = pyqtSignal(int, int)  # (deleted_count, failed_count)

    def __init__(self, assets: list, cache_manager):
        super().__init__()
        self.assets = assets
        self.cache_manager = cache_manager
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        '''Delete assets in background thread using batch delete for efficiency.'''
        if self._stop_requested or not self.assets:
            self.deletion_complete.emit(0, 0)
            return

        # Convert assets list to (asset_id, asset_type) tuples
        assets_to_delete = [(a['id'], a['type']) for a in self.assets]
        
        # Use batch delete which only writes index once (much faster than N writes)
        deleted_count, failed_count = self.cache_manager.delete_assets_batch(assets_to_delete)
        
        if not self._stop_requested:
            self.deletion_complete.emit(deleted_count, failed_count)


def _get_roblosecurity() -> str | None:
    """Get .ROBLOSECURITY cookie from Roblox local storage."""
    import os
    import json
    import base64
    import re

    try:
        import win32crypt
    except ImportError:
        return None

    path = os.path.expandvars(r'%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat')
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            data = json.load(f)
        cookies_data = data.get('CookiesData')
        if not cookies_data:
            return None
        enc = base64.b64decode(cookies_data)
        dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
        s = dec.decode(errors='ignore')
        m = re.search(r'\.ROBLOSECURITY\s+([^\s;]+)', s)
        return m.group(1) if m else None
    except Exception:
        return None


class ImageLoaderThread(QThread):
    """Worker thread for loading and processing images."""

    image_ready = pyqtSignal(QPixmap)
    error = pyqtSignal(str)

    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            log_buffer.log('Preview', f'Loading image ({len(self.data)} bytes)')

            data = self.data
            if data[:12] in (b'\xabKTX 11\xbb\r\n\x1a\n', b'\xabKTX 20\xbb\r\n\x1a\n'):
                log_buffer.log('Preview', 'KTX detected, converting to PNG...')
                from .tools.ktx_to_png import convert as _ktx_convert
                data = _ktx_convert(data)
                if data is None:
                    if not self._stop_requested:
                        self.error.emit('KTX format not supported for local preview')
                    return

            image = Image.open(io.BytesIO(data))

            if self._stop_requested:
                return

            # Convert to RGBA
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            elif image.mode == 'RGB':
                image = image.convert('RGBA')

            if self._stop_requested:
                return

            qimage = QImage(
                image.tobytes(),
                image.width,
                image.height,
                QImage.Format.Format_RGBA8888
            )
            pixmap = QPixmap.fromImage(qimage)

            if not self._stop_requested:
                log_buffer.log('Preview', f'Image loaded: {image.width}x{image.height}')
                self.image_ready.emit(pixmap)

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Image load error: {e}')
                self.error.emit(str(e))


class MeshLoaderThread(QThread):
    """Worker thread for loading and converting meshes."""

    mesh_ready = pyqtSignal(str)  # OBJ content
    error = pyqtSignal(str)

    def __init__(self, data: bytes, asset_id: str):
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            log_buffer.log('Preview', f'Loading mesh {self.asset_id} ({len(self.data)} bytes)')

            # Decompress if gzip
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
                log_buffer.log('Preview', f'Decompressed mesh: {len(decompressed)} bytes')

            if self._stop_requested:
                return

            # Convert to OBJ
            obj_content = mesh_processing.convert(decompressed)

            if self._stop_requested:
                return

            if obj_content:
                log_buffer.log('Preview', f'Mesh converted successfully')
                self.mesh_ready.emit(obj_content)
            else:
                self.error.emit('Failed to convert mesh to OBJ format')

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Mesh conversion error: {e}')
                self.error.emit(str(e))

class SolidModelLoaderThread(QThread):
    """Worker thread for loading and converting solid models (CSG)."""

    mesh_ready = pyqtSignal(str)  # OBJ content
    error = pyqtSignal(str)

    def __init__(self, data: bytes, asset_id: str):
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            log_buffer.log('Preview', f'Loading SolidModel {self.asset_id} ({len(self.data)} bytes)')

            if self._stop_requested:
                return

            # Convert to OBJ using solidmodel_converter
            import tempfile
            import gzip as gzip_module
            from pathlib import Path
            from .tools.solidmodel_converter.converter import deserialize_rbxm, _export_obj_from_doc
            
            # Decompress if gzip
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
                log_buffer.log('Preview', f'Decompressed SolidModel: {len(decompressed)} bytes')
                
            # Use memory directly and dump to temp file because the library forces Path based OBJ output currently
            doc = deserialize_rbxm(decompressed)
            
            with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as f:
                temp_obj_path = Path(f.name)
            
            try:
                _export_obj_from_doc(doc, temp_obj_path, decompose=False)
                obj_content = temp_obj_path.read_text(encoding='utf-8')
            finally:
                if temp_obj_path.exists():
                    temp_obj_path.unlink()

            if self._stop_requested:
                return

            if obj_content:
                log_buffer.log('Preview', f'SolidModel converted successfully')
                self.mesh_ready.emit(obj_content)
            else:
                self.error.emit('Failed to convert SolidModel to OBJ format')

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'SolidModel conversion error: {e}')
                self.error.emit(str(e))



class AnimationLoaderThread(QThread):
    """Worker thread for loading animation data asynchronously."""

    animation_ready = pyqtSignal(bytes)  # Animation data ready to load into viewer
    error = pyqtSignal(str)

    def __init__(self, data: bytes, asset_id: str):
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            # Decompress if gzip
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
                log_buffer.log('Preview', f'Decompressed animation: {len(decompressed)} bytes')

            if self._stop_requested:
                return

            # Emit the data for the main thread to load into the viewer
            # The actual animation loading must happen on main thread due to OpenGL context
            self.animation_ready.emit(decompressed)

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Animation load error: {e}')
                self.error.emit(str(e))


class TexturePackLoaderThread(QThread):
    """Worker thread for loading texture pack images asynchronously."""

    texture_loaded = pyqtSignal(str, str, str, bytes)  # map_name, map_id, hash, image_data
    texture_error = pyqtSignal(str, str)  # map_name, error_message
    finished_loading = pyqtSignal()

    def __init__(self, maps: dict, cache_manager: 'CacheManager', cache_scraper=None):
        super().__init__()
        self.maps = maps
        self.cache_manager = cache_manager
        self._cache_scraper = cache_scraper
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        from urllib.parse import urlparse

        log_buffer.log('Preview', f'Loading texture pack with {len(self.maps)} maps')

        for map_name, map_id in self.maps.items():
            if self._stop_requested:
                return

            try:
                data = self.cache_manager.get_asset(str(map_id), 1)
                hash_val = ''

                if data:
                    asset_info = self.cache_manager.get_asset_info(str(map_id), 1)
                    hash_val = asset_info.get('hash', '') if asset_info else ''
                    log_buffer.log('Preview', f'Loaded {map_name} from cache')
                else:
                    if self._stop_requested:
                        return

                    log_buffer.log('Preview', f'Fetching {map_name} from API')
                    # Use scraper's bypass fetch if available (bypasses hosts file redirect)
                    if self._cache_scraper is not None:
                        cookie = _get_roblosecurity()
                        extra = {}
                        if cookie:
                            extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
                        data = self._cache_scraper._https_get(
                            'assetdelivery.roblox.com',
                            f'/v1/asset/?id={map_id}',
                            extra_headers=extra or None,
                        )
                        if not data:
                            self.texture_error.emit(map_name, 'API returned no data')
                            continue
                    else:
                        # Fallback: direct requests (only works when proxy is not running)
                        import requests as _requests
                        api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={map_id}'
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        cookie = _get_roblosecurity()
                        if cookie:
                            headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
                        response = _requests.get(api_url, headers=headers, timeout=10)
                        if response.status_code == 200 and response.content:
                            data = response.content
                        else:
                            self.texture_error.emit(map_name, f'API error: {response.status_code}')
                            continue

                if self._stop_requested:
                    return

                self.texture_loaded.emit(map_name, str(map_id), hash_val, data)

            except Exception as e:
                if not self._stop_requested:
                    log_buffer.log('Preview', f'Texture {map_name} error: {e}')
                    self.texture_error.emit(map_name, str(e))

        if not self._stop_requested:
            log_buffer.log('Preview', 'Texture pack loading complete')
            self.finished_loading.emit()


class AssetLoaderThread(QThread):
    """Worker thread for downloading assets from Roblox API and storing them in the cache."""

    progress = pyqtSignal(int, int)  # (current, total)
    asset_loaded = pyqtSignal(str, str, int)  # (asset_id, name, asset_type)
    finished_loading = pyqtSignal(int, int)  # (loaded_count, failed_count)
    status_message = pyqtSignal(str)  # status text for the dialog

    def __init__(self, asset_ids: list[int], cache_manager: 'CacheManager',
                 cache_scraper=None):
        super().__init__()
        self.asset_ids = asset_ids
        self.cache_manager = cache_manager
        self._cache_scraper = cache_scraper
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        import requests
        import time

        total = len(self.asset_ids)
        loaded_count = 0
        failed_count = 0

        if not self.asset_ids or self._stop_requested:
            self.finished_loading.emit(0, 0)
            return

        # Get authentication cookie
        cookie = _get_roblosecurity()

        # Build session
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update({
            'User-Agent': 'Roblox/WinInet',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Referer': 'https://www.roblox.com/',
            'Origin': 'https://www.roblox.com',
        })
        if cookie:
            try:
                sess.cookies.set('.ROBLOSECURITY', cookie)
            except Exception:
                sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

        # Phase 1: Batch-fetch asset metadata (name, type, creator) in groups of 50
        self.status_message.emit('Fetching asset info...')
        log_buffer.log('Scraper', f'[Load Asset] Fetching info for {total} asset(s)')

        asset_metadata = {}  # asset_id_str -> {name, type, creator_id, creator_type}
        batch_size = 50
        str_ids = [str(aid) for aid in self.asset_ids]

        for i in range(0, len(str_ids), batch_size):
            if self._stop_requested:
                self.finished_loading.emit(loaded_count, failed_count)
                return

            batch = str_ids[i:i + batch_size]
            query = ','.join(batch)
            url = f'https://develop.roblox.com/v1/assets?assetIds={query}'

            try:
                response = sess.get(url, timeout=10)
                response.raise_for_status()
                data = response.json().get('data', [])
                for item in data:
                    aid = item.get('id')
                    if aid is None:
                        continue
                    creator_obj = item.get('creator') or {}
                    creator_id = None
                    creator_type = None
                    if isinstance(creator_obj, dict) and creator_obj:
                        creator_id = creator_obj.get('targetId')
                        creator_type = creator_obj.get('typeId')
                    if creator_id is None:
                        creator_id = item.get('creatorTargetId')
                    if creator_type is None:
                        creator_type = item.get('creatorType')
                    try:
                        if creator_type is not None:
                            creator_type = int(creator_type)
                    except Exception:
                        creator_type = None
                    try:
                        if creator_id is not None:
                            creator_id = int(creator_id)
                    except Exception:
                        creator_id = None

                    asset_metadata[str(aid)] = {
                        'name': item.get('name', 'Unknown'),
                        'type': item.get('typeId') or item.get('assetTypeId') or 1,
                        'creator_id': creator_id,
                        'creator_type': creator_type,
                    }
                log_buffer.log('Scraper', f'[Load Asset] Fetched metadata for batch {i // batch_size + 1}')
            except Exception as e:
                log_buffer.log('Scraper', f'[Load Asset] Failed to fetch metadata batch: {e}')

        # Phase 2: Resolve creator names
        creators_to_resolve = {}
        for meta in asset_metadata.values():
            cid = meta.get('creator_id')
            ctype = meta.get('creator_type')
            if cid is not None and ctype is not None and cid not in creators_to_resolve:
                creators_to_resolve[cid] = ctype

        creator_names = {}
        if creators_to_resolve:
            self.status_message.emit('Resolving creator names...')
            log_buffer.log('Scraper', f'[Load Asset] Resolving {len(creators_to_resolve)} creator name(s)')

            # Batch-resolve users
            user_ids = [cid for cid, ctype in creators_to_resolve.items() if ctype == 1]
            group_ids = [cid for cid, ctype in creators_to_resolve.items() if ctype == 2]

            if user_ids:
                try:
                    resp = sess.post(
                        'https://users.roblox.com/v1/users',
                        json={'userIds': user_ids, 'excludeBannedUsers': False},
                        timeout=10,
                    )
                    resp.raise_for_status()
                    for entry in resp.json().get('data', []):
                        uid = entry.get('id')
                        name = entry.get('name') or entry.get('displayName') or 'Unknown'
                        if uid is not None:
                            creator_names[uid] = name
                except Exception as e:
                    log_buffer.log('Scraper', f'[Load Asset] Failed to fetch user names: {e}')

            for gid in group_ids:
                if self._stop_requested:
                    break
                try:
                    resp = sess.get(
                        f'https://groups.roblox.com/v1/groups/{gid}',
                        timeout=10,
                    )
                    resp.raise_for_status()
                    name = resp.json().get('name', 'Unknown')
                    creator_names[gid] = name
                except Exception as e:
                    log_buffer.log('Scraper', f'[Load Asset] Failed to fetch group {gid}: {e}')

        # Store creator names back into asset_metadata
        for meta in asset_metadata.values():
            cid = meta.get('creator_id')
            if cid is not None and cid in creator_names:
                meta['creator_name'] = creator_names[cid]

        # Phase 3: Download each asset's data and store in cache IN PARALLEL
        # Use ThreadPoolExecutor for concurrent downloads to dramatically improve speed.
        # The V1 assetdelivery endpoint doesn't support batch data download, so we
        # parallelize individual requests. 6 workers gives good throughput without
        # hitting rate limits too aggressively.
        self.status_message.emit('Downloading assets...')
        log_buffer.log('Scraper', f'[Load Asset] Starting parallel download of {total} asset(s)')

        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading as _threading

        _progress_lock = _threading.Lock()
        _progress_count = [0]  # mutable counter for closure

        def _download_one(aid_str: str) -> tuple[str, bool]:
            """Download a single asset. Returns (aid_str, success)."""
            if self._stop_requested:
                return aid_str, False

            meta = asset_metadata.get(aid_str)
            asset_type = meta['type'] if meta else 1
            asset_name = meta['name'] if meta else 'Unknown'

            try:
                if self._cache_scraper is not None:
                    extra = {}
                    if cookie:
                        extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
                    data, _status = self._cache_scraper._fetch_asset_with_place_id_retry(
                        aid_str, extra_headers=extra or None,
                    )
                else:
                    api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={aid_str}'
                    dl_headers = {'User-Agent': 'Roblox/WinInet'}
                    if cookie:
                        dl_headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
                    resp = sess.get(api_url, headers=dl_headers, timeout=15,
                                    allow_redirects=True)
                    data = resp.content if resp.status_code == 200 else None

                    # Attempt place-ID retry on 403 using pre-fetched creator metadata
                    if data is None and resp.status_code == 403 and meta:
                        cid = meta.get('creator_id')
                        ctype = meta.get('creator_type')
                        if cid is not None and ctype is not None:
                            g_paths = ([f'/v2/users/{cid}/games?sortOrder=Asc&limit=100']
                                        if ctype == 1 else
                                        [f'/v2/groups/{cid}/gamesV2?accessFilter=2&limit=100&sortOrder=Asc',
                                         f'/v2/groups/{cid}/gamesV2?accessFilter=1&limit=100&sortOrder=Asc'])
                            try:
                                seen_pids = set()
                                for g_path in g_paths:
                                    g_r = sess.get(f'https://games.roblox.com{g_path}',
                                                   headers={'Accept': 'application/json'},
                                                   timeout=10)
                                    if g_r.status_code == 200:
                                        games = g_r.json().get('data', [])
                                        for game in games:
                                            rp = game.get('rootPlace')
                                            if rp and rp.get('id'):
                                                pid = int(rp['id'])
                                                if pid in seen_pids:
                                                    continue
                                                seen_pids.add(pid)
                                                retry_h = {**dl_headers, 'Roblox-Place-Id': str(pid)}
                                                r2 = sess.get(api_url, headers=retry_h,
                                                              timeout=15, allow_redirects=True)
                                                if r2.status_code == 200 and r2.content:
                                                    data = r2.content
                                                    log_buffer.log('Scraper', f'[Load Asset] Place-ID bypass succeeded for {aid_str}')
                                                    break  # Found working place ID
                                    if data:
                                        break  # Stop trying paths
                            except Exception:
                                pass

                if data:
                    self.cache_manager.store_asset(
                        aid_str, asset_type, data,
                        url=f'https://assetdelivery.roblox.com/v1/asset/?id={aid_str}',
                    )
                    log_buffer.log('Scraper', f'[Load Asset] Stored asset {aid_str} ({asset_name})')
                    return aid_str, True
                else:
                    log_buffer.log('Scraper', f'[Load Asset] No data returned for asset {aid_str}')
                    return aid_str, False

            except Exception as e:
                log_buffer.log('Scraper', f'[Load Asset] Failed to download asset {aid_str}: {e}')
                return aid_str, False

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_download_one, aid_str): aid_str
                       for aid_str in str_ids}

            for future in as_completed(futures):
                if self._stop_requested:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                aid_str, success = future.result()
                if success:
                    loaded_count += 1
                    meta = asset_metadata.get(aid_str)
                    asset_name = meta['name'] if meta else 'Unknown'
                    self.asset_loaded.emit(aid_str, asset_name,
                                           meta['type'] if meta else 1)
                else:
                    failed_count += 1

                with _progress_lock:
                    _progress_count[0] += 1
                    done = _progress_count[0]
                self.progress.emit(done, total)
                self.status_message.emit(f'Downloaded {done}/{total} assets')

        # Re-stamp cached_at timestamps to preserve the user's original input order.
        # Parallel downloads finish in arbitrary order, so the auto-generated timestamps
        # from store_asset() don't reflect the intended sequence.
        # We assign monotonically increasing timestamps (1ms apart) so that:
        #   first ID in list  → earliest timestamp → bottom of descending sort
        #   last ID in list   → latest timestamp   → top of descending sort
        from datetime import datetime, timedelta
        base_time = datetime.now()
        with self.cache_manager._lock:
            for order_idx, aid_str in enumerate(str_ids):
                meta = asset_metadata.get(aid_str)
                asset_type = meta['type'] if meta else 1
                asset_key = f'{asset_type}_{aid_str}'
                entry = self.cache_manager.index['assets'].get(asset_key)
                if entry is not None:
                    # Offset: first ID gets base_time, last ID gets base_time + N ms
                    entry['cached_at'] = (base_time + timedelta(milliseconds=order_idx)).isoformat()
            self.cache_manager._schedule_index_commit()

        # Store resolved metadata so the name resolver picks it up
        self._resolved_metadata = asset_metadata
        self._resolved_creator_names = creator_names

        log_buffer.log('Scraper', f'[Load Asset] Complete: {loaded_count} loaded, {failed_count} failed')
        self.finished_loading.emit(loaded_count, failed_count)


class CategoryFilterPopup(QMenu):
    filters_changed = pyqtSignal(set)

    def __init__(self, parent=None, active_filters=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QMenu { background-color: palette(window); border: 1px solid palette(mid); border-radius: 4px; color: palette(window-text); }
            QWidget#FilterContainer { background-color: palette(window); }
            QCheckBox { padding: 1px; color: palette(window-text); font-size: 12px; }
            QCheckBox::indicator { width: 14px; height: 14px; }
        """)
        
        self.active_filters = set(active_filters) if active_filters else set()
        self._updating = False
        
        self.container = QWidget()
        self.container.setObjectName("FilterContainer")
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(10, 10, 10, 10)
        
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        
        self.categories = {
            '3D Models': [4, 10, 39, 40, 32, 17, 79, 75], 
            'Images/Textures': [1, 13, 63, 21, 22, 18], 
            'Audio/Video': [3, 62, 33], 
            'Animations': [24, 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 78], 
            'Avatar Parts': [16, 25, 26, 27, 28, 29, 30, 31], 
            'Clothing': [2, 11, 12, 8, 19], 
            'Accessories': [41, 42, 43, 44, 45, 46, 47, 57, 58, 64, 65, 66, 67, 68, 69, 70, 71, 72, 76, 77],
            'Scripts/Data': [5, 6, 7, 37, 38, 80, 59, 74, 73, 35, 34, 9, 'Json'] 
        }
        
        self.checkboxes = {} 
        self.category_checkboxes = {} 
        
        col = 0
        row = 0
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        
        for cat_name, type_ids in self.categories.items():
            cat_frame = QFrame()
            cat_frame.setObjectName("CategoryCard")
            cat_frame.setStyleSheet("""
                QFrame#CategoryCard {
                    border: 1px solid palette(mid);
                    border-radius: 6px;
                    background-color: palette(base);
                }
            """)
            vbox = QVBoxLayout(cat_frame)
            vbox.setContentsMargins(6, 6, 6, 6)
            vbox.setSpacing(3)
            
            cat_cb = QCheckBox(cat_name)
            cat_cb.setStyleSheet("font-weight: bold; color: #55aaff;")
            cat_cb.setTristate(True)
            self.category_checkboxes[cat_name] = cat_cb
            vbox.addWidget(cat_cb)
            
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)
            line.setStyleSheet("background-color: palette(mid); margin-bottom: 2px; margin-top: 2px;")
            vbox.addWidget(line)
            
            cat_types = []
            for tid in type_ids:
                if isinstance(tid, str):
                    # String detected-type entry (e.g. 'Json')
                    name = tid
                elif tid in CacheManager.ASSET_TYPES:
                    name = CacheManager.ASSET_TYPES[tid]
                else:
                    continue

                # Calculate reasonable elide width based on parent or fallback
                max_w = 130
                if self.parent() and self.parent().parent():
                    max_w = max(80, int(self.parent().parent().width() * 0.15) - 20)

                elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, max_w)
                cb = QCheckBox(elided)
                if elided != name:
                    cb.setToolTip(name)
                cb.setChecked(tid in self.active_filters)
                self.checkboxes[tid] = cb
                vbox.addWidget(cb)
                cat_types.append(tid)
            
            cat_cb.clicked.connect(lambda checked, t=cat_types, c=cat_name: self._on_category_clicked(t, c))
            for tid in cat_types:
                cb = self.checkboxes[tid]
                cb.clicked.connect(lambda checked, t=tid, c=cat_name: self._on_type_clicked(t, c, checked))
                
            self._update_category_state(cat_name)
            vbox.addStretch()
            grid.addWidget(cat_frame, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1
                
        layout.addLayout(grid)
        
        btn_layout = QHBoxLayout()
        clear_btn = QPushButton("Clear Filters")
        clear_btn.setStyleSheet("padding: 5px 15px; border: 1px solid palette(mid); border-radius: 3px;")
        clear_btn.clicked.connect(self._clear_all)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        action = QWidgetAction(self)
        action.setDefaultWidget(self.container)
        self.addAction(action)

    def mouseReleaseEvent(self, e):
        # Prevent the menu from closing if the user clicks inside the container but not on a specific checkbox
        action = self.actionAt(e.pos())
        if action and action.defaultWidget() == self.container:
            # We clicked inside the container area
            return
        super().mouseReleaseEvent(e)

    def _on_category_clicked(self, type_ids, cat_name):
        if self._updating: return
        self._updating = True
        
        checked_count = sum(1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked())
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)
        new_state = (checked_count < total_count)
        
        for tid in type_ids:
            if tid in self.checkboxes:
                cb = self.checkboxes[tid]
                cb.blockSignals(True)
                cb.setChecked(new_state)
                cb.blockSignals(False)
                if new_state:
                    self.active_filters.add(tid)
                else:
                    self.active_filters.discard(tid)
                    
        self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)

    def _on_type_clicked(self, tid, cat_name, checked):
        if self._updating: return
        self._updating = True
        if checked:
            self.active_filters.add(tid)
        else:
            self.active_filters.discard(tid)
            
        self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)
        
    def _update_category_state(self, cat_name):
        cat_cb = self.category_checkboxes[cat_name]
        type_ids = self.categories[cat_name]
        checked_count = sum(1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked())
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)
        
        cat_cb.blockSignals(True)
        if checked_count == 0:
            cat_cb.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == total_count and total_count > 0:
            cat_cb.setCheckState(Qt.CheckState.Checked)
        else:
            cat_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        cat_cb.blockSignals(False)
        
    def _clear_all(self):
        if self._updating: return
        self._updating = True
        self.active_filters.clear()
        for cb in self.checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        for cat_name in self.categories:
            self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)



# --- Column definitions used across the scraper tab ---
# Column 0 is always the ▼ toggle/counter — not user-configurable.
# Columns 1-6 are the data columns the user can show/hide.
COL_TOGGLE_WIDTH = 14
SCRAPER_COLUMNS = [
    # (key, label, default_visible, default_width)
    ('hash_name',  'Hash/Name',  True,  200),
    ('creator',    'Creator',    False, 120),  # off by default
    ('asset_id',   'Asset ID',   True,  100),
    ('type',       'Type',       True,  120),
    ('size',       'Size',       True,   70),
    ('cached_at',  'Cached At',  True,  135),
    ('url',        'URL',        False, 300),  # off by default
]
# Logical index → column key  (index 0 = toggle column, 1-6 = data columns)
_COL_IDX_TO_KEY = ['_toggle'] + [c[0] for c in SCRAPER_COLUMNS]
# Column key → logical index
_COL_KEY_TO_IDX = {'_toggle': 0, **{c[0]: i + 1 for i, c in enumerate(SCRAPER_COLUMNS)}}


class ColumnVisibilityMenu(QMenu):
    """
    A non-closing QMenu that lets the user toggle which Scraper columns are
    visible.  Styled identically to the ObjViewer options menu (native Qt
    checkable actions).  The menu only closes when the user clicks outside it.
    """

    visibility_changed = pyqtSignal(dict)   # {col_key: bool}

    def __init__(self, column_visibility: dict, parent=None):
        super().__init__(parent)
        self._col_visibility = dict(column_visibility)
        self._actions: dict[str, QAction] = {}
        self._building = True

        for key, label, _default, _w in SCRAPER_COLUMNS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self._col_visibility.get(key, True))
            action.toggled.connect(lambda checked, k=key: self._on_toggled(k, checked))
            self.addAction(action)
            self._actions[key] = action

        self._building = False

    # ------------------------------------------------------------------
    # Prevent the menu from closing when the user clicks a checkable item.
    # It will still close on Escape or clicking outside.
    # ------------------------------------------------------------------
    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.pos())
        if action and action.isCheckable():
            action.toggle()          # manually toggle without closing
            return
        super().mouseReleaseEvent(event)

    def _on_toggled(self, key: str, checked: bool):
        if self._building:
            return

        self._col_visibility[key] = checked

        # Enforce: at least one column must remain visible
        any_visible = any(self._col_visibility.values())
        if not any_visible:
            # Revert this action and restore Hash/Name
            self._building = True
            self._col_visibility[key] = True
            self._actions[key].setChecked(True)
            self._col_visibility['hash_name'] = True
            self._actions['hash_name'].setChecked(True)
            self._building = False

        self.visibility_changed.emit(dict(self._col_visibility))

    def update_from(self, col_visibility: dict):
        """Sync action states from an external dict (e.g. after config load)."""
        self._building = True
        for key, action in self._actions.items():
            action.setChecked(col_visibility.get(key, True))
        self._col_visibility = dict(col_visibility)
        self._building = False


class CacheViewerTab(QWidget):
    """Tab for viewing and managing cached Roblox assets."""
    
    # Signal to request table sync from background threads (thread-safe)
    _sync_table_requested = pyqtSignal()

    def __init__(self, cache_manager: CacheManager, cache_scraper=None, parent=None, config_manager=None):
        super().__init__(parent)
        self.cache_manager = cache_manager
        self.cache_scraper = cache_scraper
        self.config_manager = config_manager
        self._active_filters = set()
        self._last_asset_count = 0  # Track for change detection
        self._selected_asset_id: str | None = None  # Track selected asset by ID
        self._show_names = True  # Show names instead of hashes (on by default)
        self._asset_info: dict[str, dict] = {}  # asset_id -> {resolved_name, creator_id, creator_name, creator_type, hash, row}
        self._current_pixmap = None  # Store current image for resize
        
        # OPTIMIZATION: Cache asset_id -> row mapping for O(1) lookups instead of O(n) linear search
        # Updated whenever table structure changes (populate, sort). Validates on read for thread-safety.
        self._asset_row_cache: dict[str, int] = {}

        # Worker threads for async preview loading
        self._image_loader: ImageLoaderThread | None = None
        self._mesh_loader: MeshLoaderThread | None = None
        self._animation_loader: AnimationLoaderThread | None = None
        self._texturepack_loader: TexturePackLoaderThread | None = None

        # Search worker thread
        self._search_worker: SearchWorkerThread | None = None
        self._pending_search_text: str = ''
        self._is_searching: bool = False
        
        # Delete worker thread
        self._delete_worker: DeleteWorkerThread | None = None

        # Asset loader worker thread
        self._asset_loader: AssetLoaderThread | None = None
        self._is_deleting: bool = False

        # Texturepack data for context menu
        self._texturepack_data: dict = {}  # map_name -> {id, hash, data}
        self._texturepack_xml: str = ''  # Original XML
        # Track whether we've installed the global event filter for audio hotkeys
        self._audio_key_filter_installed = False

        # Column visibility – loaded from config, validated, then applied
        self._col_visibility: dict[str, bool] = self._load_col_visibility()
        # Column widths (pixels) – None means "use default"
        self._col_widths: dict[str, int | None] = self._load_col_widths()
        # Toggle column (col 0) width – start with legacy constant, will be recalculated
        self._col_toggle_width: int = COL_TOGGLE_WIDTH
        # Currently active sort column (logical index). Defaults to Cached At (6, shifted by 1).
        self._sort_col_idx: int = 6
        self._sort_order = Qt.SortOrder.DescendingOrder
        # Guard against re-entrant sort-indicator resets when blocking col-0 sort
        self._in_sort_guard: bool = False
        # Reference to the shared non-closing visibility menu (created lazily)
        self._col_visibility_menu: ColumnVisibilityMenu | None = None
        # Guard: prevent re-entrant column resize saves during programmatic resizes
        self._resizing_cols: bool = False

        self._setup_ui()
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_for_updates)
        self._refresh_timer.start(3000)  # Check every 3 seconds

        # Search debounce timer (longer delay to batch rapid keystrokes)
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._do_search)

        # Filter debounce timer
        self._filter_debounce = QTimer()
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.timeout.connect(self._refresh_assets)

        # Load persisted resolved names from index
        self._load_persisted_names()

        # Connect the table sync signal (thread-safe way to update from background threads)
        self._sync_table_requested.connect(self._sync_visible_rows_with_asset_info)

        # Refresh to show persisted names
        QTimer.singleShot(0, self._refresh_assets)

        # Start name resolver daemon thread
        threading.Thread(target=self._name_resolver_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Column visibility / width helpers
    # ------------------------------------------------------------------

    def _default_col_visibility(self) -> dict[str, bool]:
        return {key: default_vis for key, _label, default_vis, _w in SCRAPER_COLUMNS}

    def _load_col_visibility(self) -> dict[str, bool]:
        """Load column visibility from config. Fall back to defaults, validate."""
        defaults = self._default_col_visibility()
        if self.config_manager is None:
            return defaults
        saved = self.config_manager.settings.get('scraper_column_visibility', {})
        merged = {**defaults, **{k: bool(v) for k, v in saved.items() if k in defaults}}
        # Validate: at least one visible
        if not any(merged.values()):
            # All off – fall back to Hash/Name only (per spec)
            merged = {key: False for key, *_ in SCRAPER_COLUMNS}
            merged['hash_name'] = True

        return merged

    def _load_col_widths(self) -> dict[str, int | None]:
        """Load saved column widths from config."""
        defaults: dict[str, int | None] = {key: None for key, *_ in SCRAPER_COLUMNS}
        if self.config_manager is None:
            return defaults
        saved = self.config_manager.settings.get('scraper_column_widths', {})
        merged = {}
        for key, _label, _vis, default_w in SCRAPER_COLUMNS:
            w = saved.get(key)
            merged[key] = int(w) if isinstance(w, (int, float)) and w > 0 else None
        return merged

    def _recalc_toggle_width(self, total_rows: int | None = None):
        """Recalculate and apply the minimal width for column 0 so numeric
        row counters never get truncated. Uses the table font metrics and
        applies a small padding for spacing.
        """
        try:
            if total_rows is None:
                total_rows = self.table.rowCount()
            # At least show '1' width if empty to leave room for header arrow
            total_rows = max(1, int(total_rows))
            fm = self.table.fontMetrics()
            largest_text = str(total_rows)
            text_w = fm.horizontalAdvance(largest_text)
            arrow_w = fm.horizontalAdvance('▼')
            padding = 7
            w = max(COL_TOGGLE_WIDTH, text_w + padding, arrow_w + padding)
            self._col_toggle_width = int(w)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, self._col_toggle_width)
        except Exception:
            # Fall back silently to the legacy constant on any error
            self._col_toggle_width = COL_TOGGLE_WIDTH
            try:
                self.table.setColumnWidth(0, COL_TOGGLE_WIDTH)
            except Exception:
                pass

    def _renumber_counters(self):
        """Renumber the left-most counter column so it shows 1..N in the
        current visible order. This should be called after any sort or
        when the visible ordering changes.
        """
        try:
            # Block signals to avoid spurious selection/changed events
            self.table.blockSignals(True)
            row_count = self.table.rowCount()
            for r in range(row_count):
                old = self.table.item(r, 0)
                flags = old.flags() if old is not None else Qt.ItemFlag.ItemIsEnabled
                align = old.textAlignment() if old is not None else Qt.AlignmentFlag.AlignCenter
                new = NumericSortItem(r, str(r + 1))
                new.setFlags(flags)
                new.setTextAlignment(align)
                self.table.setItem(r, 0, new)
        except Exception:
            pass
        finally:
            self.table.blockSignals(False)
        
        # OPTIMIZATION: Update row cache after sort completes so next sync uses fresh positions
        self._update_asset_row_cache()

    def _save_col_settings(self):
        """Persist column visibility and widths to config."""
        if self.config_manager is None:
            return
        self.config_manager.settings['scraper_column_visibility'] = dict(self._col_visibility)
        self.config_manager.settings['scraper_column_widths'] = dict(self._col_widths)
        self.config_manager.save()

    def _apply_column_visibility(self, initial: bool = False):
        """Show/hide table columns (indices 1–7) and update resize modes.

        Column 0 (▼ toggle/counter) is always visible and Fixed — never touched here.
        The last *visible* data column (index ≥ 1) gets Stretch so it fills the
        remaining table width with no seam on its right edge.  Every other visible
        data column is Interactive so the user can drag its seam.

        If the currently active sort column is hidden, reset the sort to
        'Cached At' (logical index 6).
        """
        header = self.table.horizontalHeader()

        # Find which data column will be last visible (idx 1-6)
        last_visible_idx = -1
        for i, (key, *_) in enumerate(SCRAPER_COLUMNS, start=1):
            if self._col_visibility.get(key, True):
                last_visible_idx = i

        for i, (key, *_) in enumerate(SCRAPER_COLUMNS, start=1):
            visible = self._col_visibility.get(key, True)
            header.setSectionHidden(i, not visible)
            if visible:
                if i == last_visible_idx:
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
                else:
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        # If sort column just became hidden, reset to Cached At (idx 6)
        sort_key = _COL_IDX_TO_KEY[self._sort_col_idx] if self._sort_col_idx < len(_COL_IDX_TO_KEY) else None
        if sort_key and sort_key != '_toggle' and not self._col_visibility.get(sort_key, True):
            self._sort_col_idx = 6   # Cached At
            self._sort_order = Qt.SortOrder.DescendingOrder
            self.table.sortByColumn(6, Qt.SortOrder.DescendingOrder)

        if not initial:
            self._save_col_settings()
            QTimer.singleShot(0, self._auto_snap_splitter)

    # ------------------------------------------------------------------
    # Column-visibility menu helpers
    # ------------------------------------------------------------------

    def _get_or_create_col_menu(self) -> 'ColumnVisibilityMenu':
        """Return (and lazily create) the shared ColumnVisibilityMenu."""
        if self._col_visibility_menu is None:
            self._col_visibility_menu = ColumnVisibilityMenu(self._col_visibility, self)
            self._col_visibility_menu.visibility_changed.connect(self._on_col_visibility_changed)
        else:
            # Keep it in sync with any external changes
            self._col_visibility_menu.update_from(self._col_visibility)
        return self._col_visibility_menu

    def _on_header_section_clicked(self, logical_index: int):
        """Open the visibility menu when the ▼ column (index 0) is clicked."""
        if logical_index == 0:
            menu = self._get_or_create_col_menu()
            # Position below the ▼ header section
            header = self.table.horizontalHeader()
            x = header.sectionPosition(0)
            pos = header.mapToGlobal(header.rect().bottomLeft())
            pos.setX(pos.x() + x)
            menu.exec(pos)

    def _show_col_visibility_from_header(self, pos):
        """Right-click on any header section: open menu at cursor."""
        menu = self._get_or_create_col_menu()
        menu.exec(QCursor.pos())

    def _on_col_visibility_changed(self, new_visibility: dict):
        """Called when the user toggles a column in the visibility menu."""
        self._col_visibility = new_visibility
        self._apply_column_visibility()

    # ------------------------------------------------------------------
    # Column resize tracking
    # ------------------------------------------------------------------

    def _on_sort_indicator_changed(self, logical_index: int, order):
        """Block sort on col 0 (▼ toggle); track sort column for all others."""
        if self._in_sort_guard:
            return
        if logical_index == 0:
            # Column 0 is the toggle — restore the previous sort immediately
            self._in_sort_guard = True
            self.table.sortByColumn(self._sort_col_idx, self._sort_order)
            self._in_sort_guard = False
            return
        self._sort_col_idx = logical_index
        self._sort_order = order
        # After the internal Qt sort completes (this signal fires before
        # Qt performs the actual sort), renumber the left-most counter
        # column so it always shows 1..N in the current visible order.
        QTimer.singleShot(0, self._renumber_counters)

    def _on_column_resized(self, logical_index: int, _old_size: int, new_size: int):
        """Save user-dragged column widths to config."""
        if self._resizing_cols:
            return
        # Col 0 is Fixed, last visible is Stretch — neither should be persisted
        if logical_index == 0:
            return
        header = self.table.horizontalHeader()
        if header.sectionResizeMode(logical_index) == QHeaderView.ResizeMode.Stretch:
            return
        key = _COL_IDX_TO_KEY[logical_index]
        self._col_widths[key] = new_size
        self._save_col_settings()
        QTimer.singleShot(0, self._auto_snap_splitter)

    # ------------------------------------------------------------------
    # Splitter auto-snap
    # ------------------------------------------------------------------

    def _auto_snap_splitter(self):
        """Resize the splitter so the preview gets as much space as possible.

        Column 0 is a Fixed-width toggle column (COL_TOGGLE_WIDTH).
        The vertical header is hidden — its counter role is filled by col 0.
        The last visible data column is always Stretch; we use only its header
        label minimum width when computing table_min so that an over-wide
        user session doesn't prevent the preview from opening at the right size.
        """
        if self.preview_panel.isHidden():
            return

        total = self.splitter.width()
        if total <= 0:
            return

        header = self.table.horizontalHeader()

        # Find last visible data column (idx 1-7, Stretch mode)
        last_visible_idx = -1
        for i in range(7, 0, -1):
            if not header.isSectionHidden(i):
                last_visible_idx = i
                break

        # Col 0: fixed toggle/counter width (always visible)
        col_w = self._col_toggle_width

        for i in range(1, 8):
            if header.isSectionHidden(i):
                continue
            if i == last_visible_idx:
                key = SCRAPER_COLUMNS[i - 1][0]
                if key == 'url':
                    fm = header.fontMetrics()
                    label = SCRAPER_COLUMNS[i - 1][1]
                    col_w += fm.horizontalAdvance(label) + 24
                else:
                    col_w += self.table.sizeHintForColumn(i) + 20
            else:
                col_w += self.table.columnWidth(i)

        sb_margin = self.table.verticalScrollBar().sizeHint().width() + 4
        table_min = col_w + sb_margin
        splitter_handle = self.splitter.handleWidth()

        if table_min + splitter_handle < total:
            self.splitter.setSizes([table_min, total - table_min - splitter_handle])
        else:
            table_w = max(int(total * 0.6), table_min)
            preview_w = max(total - table_w - splitter_handle, 50)
            self.splitter.setSizes([table_w, preview_w])

    # ------------------------------------------------------------------
    # resizeEvent – update splitter continuously as main window resizes
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Snap splitter in real-time if preview is open
        if not self.preview_panel.isHidden():
            self._auto_snap_splitter()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._update_table_alt_palette()

    def _update_table_alt_palette(self):
        """Apply a slightly darker alternate-row colour in dark mode, or reset in light/system mode."""
        pal = self.palette()
        is_dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        if is_dark:
            table_pal = self.table.palette()
            table_pal.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
            self.table.setPalette(table_pal)
        else:
            self.table.setPalette(QPalette())  # inherit from application

    def _sanitize_filename(self, name: str) -> str:
        """Replace characters that are illegal in Windows filenames with Unicode look‑alikes."""
        char_map = {
            '/': '∕',      # U+2215  (division slash)
            '\\': '⧵',     # U+29F5  (reverse solidus operator)
            ':': '꞉',      # U+A789  (modifier letter colon)
            '*': '∗',      # U+2217  (asterisk operator)
            '?': '？',      # U+FF1F  (fullwidth question mark)
            '"': '＂',      # U+FF02  (fullwidth quotation mark)
            '<': '＜',      # U+FF1C  (fullwidth less‑than sign)
            '>': '＞',      # U+FF1E  (fullwidth greater‑than sign)
            '|': '｜',      # U+FF5C  (fullwidth vertical line)
        }
        return ''.join(char_map.get(c, c) for c in name)

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Filters (includes scraper toggle and stats)
        self._create_filters(layout)

        # Splitter for table and preview
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Asset table
        table_widget = QWidget()
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._create_table(table_layout)
        table_widget.setLayout(table_layout)
        self.splitter.addWidget(table_widget)

        # Right side: Preview panel
        self.preview_panel = self._create_preview_panel()
        self.splitter.addWidget(self.preview_panel)

        # Set splitter sizes (table gets more space initially)
        self.splitter.setSizes([600, 300])
        
        # Initially hide the preview panel (as requested: hide if no asset selected)
        self.preview_panel.setHidden(True)

        # Connect splitter moved to rescale image
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        layout.addWidget(self.splitter, stretch=1)

        # Actions
        self._create_actions(layout)

        self.setLayout(layout)
        # Don't refresh here - wait for the queued refresh after persisted names are loaded
        # self._refresh_assets()

    def _create_filters(self, parent_layout):
        """Create filter controls."""
        filter_group = QGroupBox('Filters')
        filter_layout = QHBoxLayout()

        # Search box first
        filter_layout.addWidget(QLabel('Search:'))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search all columns...')
        self.search_box.textChanged.connect(self._on_search_text_changed)
        filter_layout.addWidget(self.search_box)

        # Type selector second
        self.filter_btn = QPushButton('Type: All Types')
        self.filter_btn.clicked.connect(self._show_filter_popup)
        filter_layout.addWidget(self.filter_btn)

        filter_layout.addStretch()

        # Show names toggle (on by default)
        self.show_names_toggle = QCheckBox('Show Names')
        self.show_names_toggle.setChecked(True)
        self.show_names_toggle.toggled.connect(self._on_show_names_toggled)
        filter_layout.addWidget(self.show_names_toggle)

        filter_layout.addWidget(QLabel('|'))

        # Cache scraper toggle - reflect actual scraper state
        self.scraper_toggle = QCheckBox('Enable Cache Scraper')
        scraper_enabled = self.cache_scraper.enabled if self.cache_scraper else False
        self.scraper_toggle.setChecked(scraper_enabled)
        self.scraper_toggle.stateChanged.connect(self._toggle_scraper)
        filter_layout.addWidget(self.scraper_toggle)

        filter_layout.addWidget(QLabel('|'))

        # Stats label
        self.stats_label = QLabel('Total: 0 assets | Size: 0 B')
        filter_layout.addWidget(self.stats_label)

        filter_group.setLayout(filter_layout)
        parent_layout.addWidget(filter_group)

    def _show_filter_popup(self):
        self.popup = CategoryFilterPopup(self, self._active_filters)
        self.popup.filters_changed.connect(self._on_filters_changed)

        # Position the popup relative to the filter button, keeping it on the
        # same monitor.  The critical rule: NEVER pass a coordinate that is
        # outside the button's screen geometry to QMenu.exec() — Qt interprets
        # an off-screen position as "find nearest available space", which may
        # jump to a completely different monitor.
        #
        # Strategy:
        #   1. Get the screen the button lives on.
        #   2. Force-size the popup so sizeHint() is accurate.
        #   3. Prefer below the button; flip above if it overflows bottom.
        #   4. Clamp X so the right edge stays within the screen.
        #   5. If neither above nor below fits, pin to bottom of screen on same monitor.
        from PyQt6.QtGui import QScreen
        from PyQt6.QtCore import QPoint

        # Force layout so sizeHint reflects real dimensions
        self.popup.adjustSize()

        btn_rect   = self.filter_btn.rect()
        btn_bottom = self.filter_btn.mapToGlobal(btn_rect.bottomLeft())
        btn_top    = self.filter_btn.mapToGlobal(btn_rect.topLeft())

        screen: QScreen | None = self.filter_btn.screen()
        if screen is None:
            app = QApplication.instance()
            if app:
                screen = app.screenAt(btn_bottom)

        if screen is None:
            # No screen info — just show below and trust Qt
            self.popup.exec(btn_bottom)
            return

        sg = screen.availableGeometry()
        ph = self.popup.sizeHint().height()
        pw = self.popup.sizeHint().width()

        # X: left-align with button, clamp so right edge stays on screen
        x = btn_bottom.x()
        if x + pw > sg.right():
            x = sg.right() - pw
        x = max(x, sg.left())

        # Y: below preferred; flip above if it overflows the bottom of this screen
        if btn_bottom.y() + ph <= sg.bottom():
            y = btn_bottom.y()
        elif btn_top.y() - ph >= sg.top():
            y = btn_top.y() - ph
        else:
            # Neither fits fully — pin to screen bottom so popup is on correct monitor
            y = sg.bottom() - ph
            y = max(y, sg.top())

        self.popup.exec(QPoint(x, y))
        
    def _on_filters_changed(self, filters):
        self._active_filters = set(filters)
        count = len(self._active_filters)
        if count == 0:
            self.filter_btn.setText('Type: All Types')
        elif count == 1:
            tid = next(iter(self._active_filters))
            name = tid if isinstance(tid, str) else CacheManager.ASSET_TYPES.get(tid, str(tid))
            self.filter_btn.setText(f'Type: {name}')
        else:
            self.filter_btn.setText(f'{count} Filters...')
            
        self._filter_debounce.start(300)

    def _create_table(self, parent_layout):
        """Create asset table."""
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            '▼', 'Hash/Name', 'Creator', 'Asset ID', 'Type', 'Size', 'Cached At', 'URL'
        ])

        header = self.table.horizontalHeader()

        # Column 0: ▼ toggle — Fixed width, never sorted, never resized by user
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        # Use dynamic width based on number of rows so the numeric counter never
        # gets truncated. The actual width will be recalculated when rows are
        # populated via `_recalc_toggle_width`.
        self.table.setColumnWidth(0, self._col_toggle_width)

        # Apply saved (or default) widths for data columns (1-6)
        self._resizing_cols = True
        for i, (key, _label, _vis, default_w) in enumerate(SCRAPER_COLUMNS, start=1):
            w = self._col_widths.get(key) or default_w
            self.table.setColumnWidth(i, w)
        self._resizing_cols = False

        # Apply visibility + resize modes for data columns (last visible → Stretch)
        self._apply_column_visibility(initial=True)

        # Hide the native row-number vertical header — col 0 now shows the counter
        self.table.verticalHeader().hide()

        # ── Intercept clicks on col 0 to open the visibility menu ──────────
        # sortIndicatorChanged fires before Qt's internal sort call, so we can
        # restore the previous sort inside the guard without a visible flicker.
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        # sectionClicked: open the menu when col 0 is clicked
        header.sectionClicked.connect(self._on_header_section_clicked)

        # Right-click on any header section also opens the visibility menu
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_col_visibility_from_header)

        # Save column widths when the user drags a seam
        header.sectionResized.connect(self._on_column_resized)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # Apply a slightly darker alternating-row colour in dark mode; this is
        # managed by _update_table_alt_palette() and kept in sync via changeEvent.
        self._update_table_alt_palette()
        self.table.currentItemChanged.connect(self._on_selection_changed)
        # Prevent column 0 (counter) from ever becoming the current item.
        # If Qt lands on column 0 (e.g. during keyboard nav), silently redirect
        # focus to column 1 of the same row so there is only one selection anchor.
        self.table.currentCellChanged.connect(self._redirect_counter_focus)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        parent_layout.addWidget(self.table)

    def _create_preview_panel(self):
        """Create preview panel for viewing assets."""
        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_group = QGroupBox('Preview')
        preview_group_layout = QVBoxLayout()

        # Scrollable container for all preview content
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Container widget inside scroll area
        self.preview_container = QWidget()
        self.preview_container_layout = QVBoxLayout()
        self.preview_container_layout.setContentsMargins(5, 5, 5, 5)

        # 3D Viewer for meshes
        self.obj_viewer = ObjViewerPanel(config_manager=self.config_manager)
        self.obj_viewer.clear_requested.connect(self._clear_preview)
        self.preview_container_layout.addWidget(self.obj_viewer)

        # Loading indicator
        self.loading_label = QLabel('Loading...')
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet('QLabel { background-color: palette(base); color: #888; font-size: 14px; padding: 20px; }')
        self.preview_container_layout.addWidget(self.loading_label)
        self.loading_label.hide()

        # Image viewer (will show/hide as needed)
        self.image_label = QLabel('Select an asset to preview')
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: palette(base); color: #888; }')
        self.image_label.setScaledContents(False)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_image_context_menu)
        self.preview_container_layout.addWidget(self.image_label)

        # Audio player container with centering wrapper
        self.audio_player = None  # Created dynamically when needed
        self.audio_wrapper = QWidget()
        audio_wrapper_layout = QVBoxLayout()
        audio_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        audio_wrapper_layout.addStretch(1)
        self.audio_container = QWidget()
        self.audio_container_layout = QVBoxLayout()
        self.audio_container_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_container.setLayout(self.audio_container_layout)
        audio_wrapper_layout.addWidget(self.audio_container)
        audio_wrapper_layout.addStretch(1)
        self.audio_wrapper.setLayout(audio_wrapper_layout)
        self.preview_container_layout.addWidget(self.audio_wrapper)

        # Animation viewer
        self.animation_viewer = AnimationViewerPanel(config_manager=self.config_manager)
        self.preview_container_layout.addWidget(self.animation_viewer)

        # Text viewer for other types
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText('Select an asset to preview')
        self.preview_container_layout.addWidget(self.text_viewer)
        self._text_viewer_default_font = self.text_viewer.font()
        self._text_viewer_default_wrap = QTextEdit.LineWrapMode.WidgetWidth

        # JSON viewer for JSON files
        self.json_viewer = CacheJsonViewer()
        self.preview_container_layout.addWidget(self.json_viewer)

        # Font viewer for font files
        self.font_wrapper = QWidget()
        font_wrapper_layout = QVBoxLayout()
        font_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        font_wrapper_layout.addStretch(1)
        self.font_container = QWidget()
        self.font_container_layout = QVBoxLayout()
        self.font_container_layout.setContentsMargins(0, 0, 0, 0)
        self.font_container.setLayout(self.font_container_layout)
        font_wrapper_layout.addWidget(self.font_container)
        font_wrapper_layout.addStretch(1)
        self.font_wrapper.setLayout(font_wrapper_layout)
        self.preview_container_layout.addWidget(self.font_wrapper)

        # Texture pack container (dynamically created)
        self.texturepack_widget = None

        # Set up scroll area
        self.preview_container.setLayout(self.preview_container_layout)
        self.preview_scroll.setWidget(self.preview_container)
        preview_group_layout.addWidget(self.preview_scroll)

        # Initially hide all preview widgets
        self.obj_viewer.hide()
        self.audio_wrapper.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()

        self.preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(self.preview_group)

        preview_widget.setLayout(preview_layout)
        return preview_widget

    def _create_actions(self, parent_layout):
        """Create action buttons."""
        actions_layout = QHBoxLayout()

        delete_db_btn = QPushButton('Delete DB')
        delete_db_btn.clicked.connect(self._clear_cache)
        actions_layout.addWidget(delete_db_btn)

        delete_cache_btn = QPushButton('Delete Cache')
        delete_cache_btn.clicked.connect(self._delete_roblox_cache)
        actions_layout.addWidget(delete_cache_btn)

        self.stop_preview_btn = QPushButton('Stop Preview')
        self.stop_preview_btn.clicked.connect(self._stop_preview)
        self.stop_preview_btn.hide()
        actions_layout.addWidget(self.stop_preview_btn)

        actions_layout.addStretch()

        load_asset_btn = QPushButton('Load Asset...')
        load_asset_btn.clicked.connect(self._show_load_asset_dialog)
        actions_layout.addWidget(load_asset_btn)

        open_cache_btn = QPushButton('Open Cache Folder')
        open_cache_btn.clicked.connect(lambda: open_folder(self.cache_manager.cache_dir))
        actions_layout.addWidget(open_cache_btn)

        open_export_btn = QPushButton('Open Export Folder')
        open_export_btn.clicked.connect(lambda: open_folder(self.cache_manager.export_dir))
        actions_layout.addWidget(open_export_btn)

        parent_layout.addLayout(actions_layout)

    def _check_for_updates(self):
        """Check if cache has new assets and update stats only."""
        try:
            stats = self.cache_manager.get_cache_stats()
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])
            self.stats_label.setText(f'Total: {total_assets} assets | Size: {total_size}')

            # Only refresh table if asset count changed
            if total_assets != self._last_asset_count:
                self._last_asset_count = total_assets
                self._refresh_assets()
        except Exception:
            pass  # Ignore errors during background refresh

    def _refresh_assets(self):
        '''Refresh the asset list using search worker for all searches.'''
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Get search text
        search_text = self.search_box.text().strip()

        # Get filter type
        filter_types = self._active_filters

        # Get assets
        assets = self.cache_manager.list_assets(filter_types)

        # Ensure all assets have _asset_info entries so the background name
        # resolver can discover and resolve them even when a search filter
        # hides them from the table.
        for a in assets:
            aid = a['id']
            if aid not in self._asset_info:
                self._asset_info[aid] = {
                    'hash': a.get('hash', ''),
                    'resolved_name': None,
                    'creator_id': None,
                    'creator_name': None,
                    'creator_type': None,
                    'row': None,
                }

        # For empty search, show all immediately
        if not search_text:
            self._populate_table(assets)
            return

        # Always use worker thread for searches to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(assets, search_text, self._asset_info)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _populate_table(self, assets: list):
        """Populate the table with assets."""
        # ── Save scroll anchor ────────────────────────────────────────────
        # Capture the asset_id of the row at the top of the visible viewport
        # so we can scroll back to it after rebuilding the table.
        # This prevents the "user teleportation" bug where inserting new rows
        # resets or jumps the scroll position unexpectedly.
        _anchor_asset_id: str | None = None
        _vsb = self.table.verticalScrollBar()
        _saved_scroll = _vsb.value()
        _top_index = self.table.indexAt(self.table.viewport().rect().topLeft())
        if _top_index.isValid():
            _top_row = _top_index.row()
            _id_item = self.table.item(_top_row, 1)  # col 1 carries the asset dict in UserRole
            if _id_item is not None:
                _asset_data = _id_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(_asset_data, dict):
                    _anchor_asset_id = _asset_data.get('id')

        # Disable updates while populating (major performance boost)
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)

        # Track row to restore selection
        row_to_select: int | None = None

        try:
            # Clear old item memory in C++ before allocating new rows
            self.table.clearContents()
            self.table.setRowCount(0)
            
            # Update table
            self.table.setRowCount(len(assets))

            # Recalculate toggle column width to fit the largest row number
            # without truncation.
            self._recalc_toggle_width(len(assets))

            for row, asset in enumerate(assets):
                asset_id = asset['id']
                hash_val = asset.get('hash', '')

                # Track if this is the previously selected asset
                if self._selected_asset_id and asset_id == self._selected_asset_id:
                    row_to_select = row

                # Initialize or update asset info tracking
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': hash_val,
                        'resolved_name': None,
                        'creator_id': None,
                        'creator_name': None,
                        'creator_type': None,
                        'row': row,
                    }
                else:
                    self._asset_info[asset_id]['row'] = row

                # Column 0: row counter (1-based), not selectable, centred
                counter_item = NumericSortItem(row, str(row + 1))
                counter_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Enabled but NOT selectable and NOT focusable - prevents the counter
                # column from acting as an independent selection anchor when using
                # keyboard navigation, which caused the double-selection UI bug.
                counter_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren
                )
                self.table.setItem(row, 0, counter_item)

                # Column 1: Hash/Name — also carries the asset UserRole payload
                info = self._asset_info[asset_id]
                if self._show_names and info.get('resolved_name'):
                    display_val = info['resolved_name']
                else:
                    display_val = hash_val
                name_item = QTableWidgetItem(display_val)
                name_item.setData(Qt.ItemDataRole.UserRole, asset)
                self.table.setItem(row, 1, name_item)

                # Column 2: Creator
                creator_name = info.get('creator_name')
                creator_id = info.get('creator_id')
                if creator_name is not None:
                    creator_display = creator_name
                elif creator_id is not None:
                    # Fallback to creator_id if name not yet resolved
                    creator_display = str(creator_id)
                else:
                    creator_display = ''
                creator_item = QTableWidgetItem(creator_display)
                self.table.setItem(row, 2, creator_item)

                # Column 3: Asset ID
                id_item = QTableWidgetItem(asset_id)
                self.table.setItem(row, 3, id_item)

                # Column 4: Type
                # Use detected type if available (e.g., 'Json' for detected JSON files)
                type_name = self.cache_manager.get_type_name_for_asset(asset_id, asset['type'])
                fm = self.table.fontMetrics()
                max_w = max(100, int(self.width() * 0.15))
                elided_type = fm.elidedText(type_name, Qt.TextElideMode.ElideRight, max_w)
                type_item = QTableWidgetItem(elided_type)
                if elided_type != type_name:
                    type_item.setToolTip(type_name)
                self.table.setItem(row, 4, type_item)

                # Column 5: Size
                # For TexturePack we show the raw KTX2 size when available
                size = asset.get('raw_size', asset.get('size', 0))
                size_str = self._format_size(size)
                size_item = NumericSortItem(size, size_str)
                self.table.setItem(row, 5, size_item)

                # Column 6: Cached At
                cached_at = asset.get('cached_at', '')
                if cached_at:
                    try:
                        cached_at = cached_at.split('T')[0] + ' ' + cached_at.split('T')[1].split('.')[0]
                    except (IndexError, AttributeError):
                        pass
                cached_item = QTableWidgetItem(cached_at)
                self.table.setItem(row, 6, cached_item)

                # Column 7: URL
                url = asset.get('url', '')
                url_item = QTableWidgetItem(url)
                self.table.setItem(row, 7, url_item)
            
            # CRITICAL FIX: After all cells are created, immediately update any cells
            # that already have resolved data in _asset_info. This fixes the race condition
            # where async updates via QTimer don't get applied before the table is visible.
            for row, asset in enumerate(assets):
                asset_id = asset['id']
                info = self._asset_info.get(asset_id)
                if not info:
                    continue
                
                # Update name if resolved
                if self._show_names and info.get('resolved_name'):
                    name_item = self.table.item(row, 1)
                    if name_item:
                        name_item.setText(info['resolved_name'])
                
                # Update creator if resolved - be explicit with None checks
                creator_name = info.get('creator_name')
                creator_id = info.get('creator_id')
                if creator_name is not None or creator_id is not None:
                    creator_item = self.table.item(row, 2)
                    if creator_item:
                        if creator_name is not None:
                            creator_item.setText(creator_name)
                        else:
                            creator_item.setText(str(creator_id))
        finally:
            # Re-enable updates
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)

        # Ensure counters reflect the visible ordering (in case a sort
        # operation occurred while populating). Defer to the event loop
        # so any pending sort completes first.
        QTimer.singleShot(0, self._renumber_counters)

        # Restore selection if the asset still exists
        if row_to_select is not None:
            self.table.blockSignals(True)
            self.table.selectRow(row_to_select)
            self.table.blockSignals(False)

        # ── Restore scroll anchor ─────────────────────────────────────────
        # Rules:
        #   1. If user was at the very top (scroll == 0), stay at the top.
        #      New assets arriving should not push the user away from the top.
        #   2. If user was scrolled down and anchor asset is still visible,
        #      restore it to the top of the viewport.
        #   3. If anchor asset is gone (filter changed), go to top — do NOT
        #      use the saved pixel value which maps to a random row in the new set.
        if _saved_scroll == 0:
            # Was at top — stay at top (don't chase anchor, just leave it)
            _vsb.setValue(0)
        elif _anchor_asset_id is not None:
            _new_anchor_row: int | None = None
            for _r in range(self.table.rowCount()):
                _it = self.table.item(_r, 1)
                if _it is not None:
                    _d = _it.data(Qt.ItemDataRole.UserRole)
                    if isinstance(_d, dict) and _d.get('id') == _anchor_asset_id:
                        _new_anchor_row = _r
                        break
            if _new_anchor_row is not None:
                self.table.scrollTo(
                    self.table.model().index(_new_anchor_row, 0),
                    self.table.ScrollHint.PositionAtTop,
                )
            # else: filter changed, anchor gone — leave at top (row 0)

        # Update stats
        try:
            stats = self.cache_manager.get_cache_stats()
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])
            
            self.stats_label.setText(f'Total: {total_assets} assets | Size: {total_size}')
                
            self._last_asset_count = total_assets
        except Exception:
            pass

        # OPTIMIZATION: Update row cache after table populate so background thread can use cached lookups
        self._update_asset_row_cache()

    def _format_size(self, size_bytes: int) -> str:
        """Format size in bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.1f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.1f} TB'

    def _toggle_scraper(self, state):
        """Toggle cache scraper on/off."""
        if self.cache_scraper:
            enabled = bool(state)
            self.cache_scraper.set_enabled(enabled)

    def _on_search_text_changed(self):
        '''Handle search text change - debounce to avoid too many searches.'''
        self._search_debounce.stop()
        self._search_debounce.start(300)  # 300ms debounce

    def _do_search(self):
        '''Execute the actual search after debounce using worker thread.'''
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        search_text = self.search_box.text().strip()

        # Get filter type and assets
        filter_types = self._active_filters
        assets = self.cache_manager.list_assets(filter_types)

        # For empty search, show all immediately
        if not search_text:
            self._populate_table(assets)
            return

        # Always use worker thread to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(assets, search_text, self._asset_info)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_complete(self, filtered_assets: list):
        '''Handle search results from worker thread.'''
        self._populate_table(filtered_assets)

    def _on_search_finished(self):
        '''Handle search worker thread finished.'''
        self._is_searching = False

    def _on_deletion_complete(self, deleted_count: int, failed_count: int):
        '''Handle deletion completion from worker thread.'''
        self._refresh_assets()
        
        total = deleted_count + failed_count
        if failed_count == 0:
            QMessageBox.information(self, 'Success', f'Deleted {deleted_count} asset(s)')
        else:
            QMessageBox.warning(
                self,
                'Partial Success',
                f'Deleted {deleted_count}/{total} asset(s). {failed_count} asset(s) failed to delete.'
            )
        
        log_buffer.log('Scraper', f'Batch deletion completed: {deleted_count} deleted, {failed_count} failed')

    def _on_deletion_finished(self):
        '''Handle deletion worker thread finished.'''
        self._is_deleting = False

    def _load_persisted_names(self):
        """Load persisted resolved names from index.json."""
        loaded_count = 0
        for asset_key, asset_data in self.cache_manager.index['assets'].items():
            asset_id = asset_data['id']
            resolved_name = asset_data.get('resolved_name')
            creator_id = asset_data.get('resolved_creator_id')
            creator_name = asset_data.get('resolved_creator_name')
            creator_type = asset_data.get('resolved_creator_type')
            if resolved_name is not None or creator_id is not None:
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': asset_data.get('hash', ''),
                        'resolved_name': resolved_name,
                        'creator_id': creator_id,
                        'creator_name': creator_name,
                        'creator_type': creator_type,
                        'row': None,
                    }
                    loaded_count += 1
                else:
                    if resolved_name is not None:
                        self._asset_info[asset_id]['resolved_name'] = resolved_name
                    if creator_id is not None:
                        self._asset_info[asset_id]['creator_id'] = creator_id
                        self._asset_info[asset_id]['creator_name'] = creator_name
                        self._asset_info[asset_id]['creator_type'] = creator_type
        log_buffer.log('Scraper', f'[Cache Viewer] Loaded {loaded_count} persisted asset names from index')

    def _on_show_names_toggled(self, checked: bool):
        """Handle Show Names toggle."""
        self._show_names = checked

        # Disable updates for performance
        self.table.setUpdatesEnabled(False)
        try:
            # Update all rows to show either resolved name or hash
            for asset_id, info in self._asset_info.items():
                row = info.get('row')
                if row is None:
                    continue
                if row >= self.table.rowCount():
                    continue

                if checked and info.get('resolved_name'):
                    display_val = info['resolved_name']
                else:
                    display_val = info.get('hash', '')

                item = self.table.item(row, 1)  # Hash/Name is now col 1
                if item:
                    item.setText(display_val)
        finally:
            # Re-enable updates
            self.table.setUpdatesEnabled(True)

    def _update_asset_row_cache(self):
        """Update the asset_id->row mapping cache after table structure changes.
        
        Called after table populate or sort operations. This enables O(1) lookups
        instead of O(n) linear searches in _find_row_for_asset.
        
        OPTIMIZATION: Caches asset positions so the background name resolver thread
        doesn't have to linearly search the table on every sync.
        """
        self._asset_row_cache.clear()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # Name column has UserRole data
            if item:
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if asset_data:
                    asset_id = asset_data.get('id')
                    if asset_id:
                        self._asset_row_cache[asset_id] = row

    def _find_row_for_asset(self, asset_id: str) -> int | None:
        """Find the actual row index for an asset.
        
        OPTIMIZATION: Uses cached asset_id->row mapping for O(1) lookup when valid.
        Falls back to linear search (with validation) if cache miss/stale, which also
        updates the cache automatically.
        
        Args:
            asset_id: The asset ID to find
            
        Returns:
            The current row index if found, None otherwise
        """
        # Try cache first (O(1) path - common case)
        if asset_id in self._asset_row_cache:
            row = self._asset_row_cache[asset_id]
            # Validate cache is still correct (handles sort/filter invalidation)
            if row < self.table.rowCount():
                item = self.table.item(row, 1)
                if item:
                    asset_data = item.data(Qt.ItemDataRole.UserRole)
                    if asset_data and asset_data.get('id') == asset_id:
                        return row  # Cache hit!
            # Cache is stale, fall through to linear search and update
        
        # Linear search (O(n) path - fallback, also updates cache on hit)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # Name column (col 1) has UserRole data
            if item:
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if asset_data and asset_data.get('id') == asset_id:
                    self._asset_row_cache[asset_id] = row  # Update cache for next time
                    return row
        return None

    def _sync_visible_rows_with_asset_info(self):
        """Update visible table rows with resolved names/creators from _asset_info.

        Called after the background name-resolver finishes a batch.

        Performance contract: O(k) where k = number of assets with new data.
        Never falls back to O(n) linear scan — if an asset isn't in
        _asset_row_cache it's simply not in the current view (filtered/sorted
        out) and we skip it.  The cache is always rebuilt by
        _update_asset_row_cache() after every _populate_table call, so a
        cache miss genuinely means "not visible", not "cache stale".
        """
        row_count = self.table.rowCount()
        if row_count == 0:
            return

        self.table.setUpdatesEnabled(False)
        try:
            for asset_id, info in self._asset_info.items():
                # Fast O(1) cache lookup — no linear scan ever.
                row = self._asset_row_cache.get(asset_id)
                if row is None or row >= row_count:
                    continue  # Not in current view; skip.

                # Validate cache is still correct (sort/filter may shift rows).
                # Validation is O(1) — just one item() call.
                item = self.table.item(row, 1)
                if not item:
                    continue
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if not asset_data or asset_data.get('id') != asset_id:
                    # Cache is stale for this asset — skip rather than scan.
                    # It will be corrected on the next _populate_table call.
                    continue

                # Update name column if resolved
                if self._show_names and info.get('resolved_name'):
                    if item.text() != info['resolved_name']:
                        item.setText(info['resolved_name'])

                # Update creator column if resolved
                creator_name = info.get('creator_name')
                creator_id   = info.get('creator_id')
                if creator_name is not None or creator_id is not None:
                    creator_item = self.table.item(row, 2)
                    if creator_item:
                        desired = creator_name if creator_name is not None else str(creator_id)
                        if creator_item.text() != desired:
                            creator_item.setText(desired)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

        # If a search is active, re-run it so that assets whose names were
        # just resolved (and now match the query) appear in the results.
        if self.search_box.text().strip():
            self._search_debounce.start(400)

    def _update_row_name(self, asset_id: str, name: str):
        """Update a single row's name cell (thread-safe via QTimer)."""
        info = self._asset_info.get(asset_id)
        if not info:
            return
        row = info.get('row')
        if row is None or row >= self.table.rowCount():
            return
        # Only update if Show Names is enabled
        if self._show_names:
            item = self.table.item(row, 1)  # Hash/Name is col 1
            if item:
                item.setText(name)
                # Force immediate repaint of this row
                self.table.viewport().update()

    def _update_row_creator(self, asset_id: str, creator_id: int | None, creator_name: str | None):
        """Update a single row's creator cell (thread-safe via QTimer).
        
        Args:
            asset_id: The asset ID
            creator_id: The numeric creator ID (for fallback display)
            creator_name: The resolved creator name (preferred display)
        """
        info = self._asset_info.get(asset_id)
        if not info:
            return
        row = info.get('row')
        if row is None or row >= self.table.rowCount():
            return
        item = self.table.item(row, 2)  # Creator is col 2
        if item:
            # Prefer name, fallback to ID
            if creator_name is not None:
                item.setText(creator_name)
            elif creator_id is not None:
                item.setText(str(creator_id))
            # Force immediate repaint of this row
            self.table.viewport().update()

    def _save_resolved_name_to_index(self, asset_id: str, name: str):
        """Save resolved name to index.json for persistence."""
        asset_keys = list(self.cache_manager.index['assets'].keys())
        for asset_key in asset_keys:
            if asset_key not in self.cache_manager.index['assets']:
                continue
            asset_data = self.cache_manager.index['assets'][asset_key]
            if asset_data['id'] == asset_id:
                asset_data['resolved_name'] = name
                break

    def _save_resolved_creator_to_index(self, asset_id: str, creator_id: int | None,
                                         creator_name: str | None, creator_type: int | None):
        """Save resolved creator info to index.json for persistence."""
        asset_keys = list(self.cache_manager.index['assets'].keys())
        for asset_key in asset_keys:
            if asset_key not in self.cache_manager.index['assets']:
                continue
            asset_data = self.cache_manager.index['assets'][asset_key]
            if asset_data['id'] == asset_id:
                asset_data['resolved_creator_id'] = creator_id
                asset_data['resolved_creator_name'] = creator_name
                asset_data['resolved_creator_type'] = creator_type
                break

    def _get_roblosecurity(self) -> str | None:
        """Get .ROBLOSECURITY cookie from Roblox local storage."""
        import os
        import json
        import base64
        import re

        try:
            import win32crypt
        except ImportError:
            return None

        path = os.path.expandvars(r'%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat')
        try:
            if not os.path.exists(path):
                return None
            with open(path, 'r') as f:
                data = json.load(f)
            cookies_data = data.get('CookiesData')
            if not cookies_data:
                return None
            enc = base64.b64decode(cookies_data)
            dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
            s = dec.decode(errors='ignore')
            m = re.search(r'\.ROBLOSECURITY\s+([^\s;]+)', s)
            return m.group(1) if m else None
        except Exception:
            return None

    def _fetch_asset_names(self, asset_ids: list[str], cookie: str | None) -> dict[str, dict] | None:
        """Fetch asset names and creator info from Roblox Develop API (batch up to 50).

        Returns a dict keyed by asset_id with values:
            {'name': str, 'creator_id': int|None, 'creator_type': int|None}
        """
        import requests

        if not asset_ids:
            return None

        # Build session with auth
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update({
            'User-Agent': 'Roblox/WinInet',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Referer': 'https://www.roblox.com/',
            'Origin': 'https://www.roblox.com',
        })
        if cookie:
            try:
                # Prefer setting cookie on the session so requests handles it properly
                sess.cookies.set('.ROBLOSECURITY', cookie)
            except Exception:
                # Fallback to header if cookie set fails
                sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

        # Build query: assetIds=123,456,789
        query = ','.join(str(aid) for aid in asset_ids)
        url = f'https://develop.roblox.com/v1/assets?assetIds={query}'

        
        try:
            response = sess.get(url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch names: {e}')
            return None

        data = response.json().get('data', [])
        
        result = {}
        for item in data:
            aid = item.get('id')
            if aid is None:
                continue

            # Newer API returns a nested 'creator' object; older APIs used
            # flat 'creatorTargetId' and 'creatorType' fields. Support both.
            creator_obj = item.get('creator') or {}
            creator_id = None
            creator_type = None

            # New format: {'type': 'User'|'Group', 'typeId': 1|2, 'targetId': <id>}
            if isinstance(creator_obj, dict) and creator_obj:
                creator_id = creator_obj.get('targetId')
                creator_type = creator_obj.get('typeId')

            # Fallback to legacy flat fields
            if creator_id is None:
                creator_id = item.get('creatorTargetId')
            if creator_type is None:
                creator_type = item.get('creatorType')

            # Normalise numeric types (ensure int or None)
            try:
                if creator_type is not None:
                    creator_type = int(creator_type)
            except Exception:
                creator_type = None
            try:
                if creator_id is not None:
                    creator_id = int(creator_id)
            except Exception:
                creator_id = None

            result[str(aid)] = {
                'name': item.get('name', 'Unknown'),
                'creator_id': creator_id,
                'creator_type': creator_type,  # 1 = User, 2 = Group
            }

            

        

        return result

    def _fetch_creator_names(self, creators: dict[int, int], sess) -> dict[int, str]:
        """Resolve creator IDs to display names.

        Args:
            creators: dict mapping creator_id (int) → creator_type (int)
                      creator_type 1 = User, 2 = Group
            sess: requests.Session to reuse

        Returns:
            dict mapping creator_id (int) → creator display name (str)
        """
        import requests

        result: dict[int, str] = {}
        if not creators:
            return result

        user_ids = [cid for cid, ctype in creators.items() if ctype == 1]
        group_ids = [cid for cid, ctype in creators.items() if ctype == 2]

        

        # Batch-resolve users via POST /v1/users
        if user_ids:
            try:
                resp = sess.post(
                    'https://users.roblox.com/v1/users',
                    json={'userIds': user_ids, 'excludeBannedUsers': False},
                    timeout=10,
                )
                resp.raise_for_status()
                for entry in resp.json().get('data', []):
                    uid = entry.get('id')
                    name = entry.get('name') or entry.get('displayName') or 'Unknown'
                    if uid is not None:
                        result[uid] = name
                
            except Exception as e:
                # If user batch lookup fails, continue without user names
                log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch user names: {e}')

        # Resolve groups one-by-one (no batch endpoint on v1)
        for gid in group_ids:
            try:
                resp = sess.get(
                    f'https://groups.roblox.com/v1/groups/{gid}',
                    timeout=10,
                )
                resp.raise_for_status()
                name = resp.json().get('name', 'Unknown')
                result[gid] = name
                
            except Exception as e:
                # If a single group lookup fails, skip that group
                log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch group {gid}: {e}')

        

        return result

    def _name_resolver_loop(self):
        """Background thread to resolve asset names and creator names."""
        import time
        import requests

        while True:
            # Skip if Show Names is OFF
            if not self._show_names:
                time.sleep(0.2)
                continue

            # Get authentication cookie
            cookie = self._get_roblosecurity()
            if not cookie:
                # No cookie - wait longer to avoid spam
                time.sleep(5)
                continue

            try:
                # Build pending list - assets without resolved names
                # Build pending list - assets without resolved names.
                # Prioritise assets in visible rows, then resolve the rest so that
                # search-by-name works even for assets not currently displayed.
                _row_count = self.table.rowCount()
            except RuntimeError:
                # Widget has been deleted (app shutting down)
                break
                
            visible = []
            hidden = []
            for asset_id, info in self._asset_info.items():
                if info.get('resolved_name') is not None:
                    continue
                row = info.get('row')
                if row is not None and row < _row_count:
                    visible.append(asset_id)
                else:
                    hidden.append(asset_id)
            pending = visible + hidden

            

            if not pending:
                time.sleep(0.2)
                continue

            # Batch size and delay
            batch_size = 50
            delay = 0.2 if len(pending) > 50 else 0.5

            # Take the first batch
            batch = pending[:batch_size]

            # Fetch names + creator IDs
            try:
                asset_data_map = self._fetch_asset_names(batch, cookie)
            except Exception as e:
                log_buffer.log('Scraper', f'[Name Resolver] Fetch failed: {e}')
                time.sleep(delay)
                continue

            if not asset_data_map:
                time.sleep(delay)
                continue

            # Build a reusable session for creator lookups (same auth headers)
            sess = requests.Session()
            sess.trust_env = False
            sess.proxies = {}
            sess.headers.update({
                'User-Agent': 'Roblox/WinInet',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            })
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

            # Collect creator IDs that need name resolution
            creators_to_resolve: dict[int, int] = {}  # creator_id → creator_type
            for asset_id, data in asset_data_map.items():
                cid = data.get('creator_id')
                ctype = data.get('creator_type')
                if cid is not None and ctype is not None and cid not in creators_to_resolve:
                    creators_to_resolve[cid] = ctype

            log_buffer.log('Scraper', f'[Name Resolver] Collected {len(creators_to_resolve)} unique creator ID(s) to resolve')

            # Fetch creator display names
            creator_names: dict[int, str] = {}
            if creators_to_resolve:
                try:
                    creator_names = self._fetch_creator_names(creators_to_resolve, sess)
                except Exception as e:
                    log_buffer.log('Scraper', f'[Name Resolver] Creator fetch failed: {e}')

            log_buffer.log('Scraper', f'[Name Resolver] Resolved {len(creator_names)} creator name(s)')

            # Update cache and UI
            for asset_id, data in asset_data_map.items():
                info = self._asset_info.get(asset_id)
                if not info:
                    continue

                name = data.get('name', 'Unknown')
                creator_id = data.get('creator_id')
                creator_type = data.get('creator_type')
                creator_name = creator_names.get(creator_id) if creator_id is not None else None
                # Store resolved name in memory
                info['resolved_name'] = name
                info['creator_id'] = creator_id
                info['creator_type'] = creator_type
                info['creator_name'] = creator_name

                # Save to index.json for persistence
                self._save_resolved_name_to_index(asset_id, name)
                self._save_resolved_creator_to_index(asset_id, creator_id, creator_name, creator_type)

            # Save index after batch update (less frequent saves)
            try:
                self.cache_manager._save_index()
            except Exception as e:
                log_buffer.log('Scraper', f'[Name Resolver] Failed to save index: {e}')

            # CRITICAL: After resolving a batch, immediately sync all visible rows with the updated data
            # This ensures that the last asset (and all assets) show their resolved names/creators
            # without waiting for the next _populate_table call
            # Emit signal which is thread-safe and connected to the sync slot
            self._sync_table_requested.emit()

            time.sleep(delay)

    def _get_selected_asset(self) -> dict | None:
        """Get the currently selected asset."""
        current_row = self.table.currentRow()
        if current_row < 0:
            return None

        id_item = self.table.item(current_row, 1)  # col 1 = Hash/Name (carries UserRole)
        if not id_item:
            return None

        return id_item.data(Qt.ItemDataRole.UserRole)

    def _export_selected(self):
        """Export the selected asset."""
        asset = self._get_selected_asset()
        if not asset:
            QMessageBox.warning(self, 'No Selection', 'Please select an asset to export')
            return

        # Ask for export location
        default_name = f"{asset['id']}.bin"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            'Export Asset',
            default_name,
            'All Files (*.*)'
        )

        if not file_path:
            return

        from pathlib import Path

        # Sanitize resolved name if present (though the user's chosen filename already safe)
        asset_id = asset['id']
        resolved_name = None
        if asset_id in self._asset_info:
            resolved_name = self._asset_info[asset_id].get('resolved_name')
        safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

        export_path = self.cache_manager.export_asset(
            asset['id'],
            asset['type'],
            Path(file_path),
            resolved_name=safe_name
        )

        if export_path:
            log_buffer.log('Scraper', f"Exported asset {asset['id']} to {export_path}")
            QMessageBox.information(self, 'Success', f'Asset exported to:\n{export_path}')
        else:
            QMessageBox.critical(self, 'Error', 'Failed to export asset')

    def _export_all(self):
        """Export all visible assets."""
        # Get current filter
        filter_types = self._active_filters
        assets = self.cache_manager.list_assets(filter_types)

        # Apply search filter across all columns (same as _refresh_assets)
        search_text = self.search_box.text().strip().lower()
        if search_text:
            filtered = []
            for a in assets:
                asset_id = a['id'].lower()
                type_name = a['type_name'].lower()
                url = a.get('url', '').lower()
                hash_val = a.get('hash', '').lower()
                size_str = self._format_size(a.get('raw_size', a.get('size', 0))).lower()
                cached_at = a.get('cached_at', '').lower()

                resolved_name = ''
                if asset_id in self._asset_info:
                    name = self._asset_info[asset_id].get('resolved_name')
                    resolved_name = name.lower() if name else ''

                if (search_text in asset_id or
                    search_text in type_name or
                    search_text in url or
                    search_text in hash_val or
                    search_text in resolved_name or
                    search_text in size_str or
                    search_text in cached_at):
                    filtered.append(a)
            assets = filtered

        if not assets:
            QMessageBox.warning(self, 'No Assets', 'No assets to export')
            return

        reply = QMessageBox.question(
            self,
            'Export All',
            f'Export {len(assets)} asset(s) to the export folder?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        exported_count = 0
        for asset in assets:
            asset_id = asset['id']
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')
            safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

            if self.cache_manager.export_asset(asset['id'], asset['type'], resolved_name=safe_name):
                exported_count += 1

        log_buffer.log('Scraper', f'Exported {exported_count}/{len(assets)} assets')
        QMessageBox.information(
            self,
            'Export Complete',
            f'Exported {exported_count} asset(s)\n\nLocation: {self.cache_manager.export_dir}'
        )

    def _delete_selected(self):
        """Delete the selected asset(s) using background worker thread."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, 'No Selection', 'Please select asset(s) to delete')
            return

        # Collect assets to delete
        assets_to_delete = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    assets_to_delete.append(asset)

        if not assets_to_delete:
            return

        # Confirm deletion
        count = len(assets_to_delete)
        reply = QMessageBox.question(
            self,
            'Delete Assets',
            f"Delete {count} asset(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Use background worker thread for fast batch deletion
            self._is_deleting = True
            self._delete_worker = DeleteWorkerThread(assets_to_delete, self.cache_manager)
            self._delete_worker.deletion_complete.connect(self._on_deletion_complete)
            self._delete_worker.finished.connect(self._on_deletion_finished)
            self._delete_worker.start()

    def _clear_cache(self):
        """Delete the entire cache database and files (old Delete DB functionality)."""
        reply = QMessageBox.question(
            self,
            'Delete Database',
            'This will delete all cached assets AND the database index.\nThis cannot be undone. Continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                import shutil
                cache_dir = self.cache_manager.cache_dir
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                # Reset the index
                self.cache_manager.index = {'assets': {}}
                self.cache_manager._save_index()
                self._last_asset_count = 0
                self._asset_info.clear()
                # Clear scraper tracking so assets can be re-scraped
                if self.cache_scraper:
                    self.cache_scraper.clear_tracking()
                self._refresh_assets()
                log_buffer.log('Scraper', 'Database deleted and reset')
                QMessageBox.information(self, 'Success', 'Database deleted successfully')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed to delete database: {e}')

    def _delete_roblox_cache(self):
        """Delete Roblox cache using system tray method."""
        from ..gui import DeleteCacheWindow

        window = DeleteCacheWindow()
        window.show()

    def _redirect_counter_focus(self, current_row: int, current_col: int,
                                previous_row: int, previous_col: int) -> None:
        """If Qt moves focus onto column 0 (the counter), immediately redirect it
        to column 1 of the same row.  This ensures there is always exactly one
        selection anchor and prevents the double-selection / jump bug that occurs
        during keyboard navigation when column 0 is non-selectable.
        """
        if current_col == 0 and current_row >= 0:
            self.table.blockSignals(True)
            self.table.setCurrentCell(current_row, 1)
            self.table.blockSignals(False)

    def _on_selection_changed(self):
        """Handle table selection change to preview asset."""
        asset = self._get_selected_asset()
        if not asset:
            self._selected_asset_id = None
            self._clear_preview()
            return
            
        # Track if preview was hidden before showing it
        was_hidden = self.preview_panel.isHidden()
        self.preview_panel.show()
        
        # Auto-snap splitter ONLY if it was previously hidden (first selection)
        if was_hidden:
            QTimer.singleShot(0, self._auto_snap_splitter)

        # Track selected asset ID for persistence across refreshes
        self._selected_asset_id = asset['id']

        # Stop all loaders first
        self._stop_all_loaders()

        # Hide all preview widgets first
        self.obj_viewer.hide()
        self.image_label.hide()
        self.loading_label.hide()
        self.audio_wrapper.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.font_wrapper.hide()

        # Clean up texture pack widget
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

        # Stop any playing audio
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        # Remove global audio key event filter if installed
        try:
            if self._audio_key_filter_installed:
                from PyQt6.QtWidgets import QApplication
                QApplication.instance().removeEventFilter(self)
                self._audio_key_filter_installed = False
        except Exception:
            pass

        # Stop animation playback
        self.animation_viewer.stop()

        asset_type = asset['type']
        asset_id = asset['id']

        # Update preview group title to show resolved name or hash for clarity
        try:
            info = self._asset_info.get(asset_id, {})
            resolved = info.get('resolved_name') if info else None
            display = resolved or asset.get('hash') or str(asset_id)
            # Trim long names/hashes to keep the UI tidy
            if len(display) > 60:
                display = display[:57] + '...'
            self.preview_group.setTitle(f'Preview: {display}')
        except Exception:
            try:
                self.preview_group.setTitle('Preview')
            except Exception:
                pass

        try:
            # Get asset data
            data = self.cache_manager.get_asset(asset_id, asset_type)
            if not data:
                self._show_text_preview(f'Failed to load asset {asset_id}')
                return

            # Show loading for async previews
            if asset_type in [4, 39, 1, 13, 63]:  # Mesh, SolidModel, Image, Decal, TexturePack
                self._show_loading()

            # Preview based on type
            if asset_type == 4:  # Mesh
                self._preview_mesh(data, asset_id)
            elif asset_type == 39:  # SolidModel
                self._preview_solidmodel(data, asset_id)
            elif asset_type in [1, 13]:  # Image, Decal
                self._preview_image(data)
            elif asset_type == 3:  # Audio
                self._preview_audio(data, asset_id)
            elif asset_type == 24:  # Animation
                self._preview_animation(data, asset_id)
            elif asset_type == 74:  # FontFace - actual font file
                self._preview_font(data)
            elif asset_type == 73:  # FontFamily - JSON metadata
                is_json, _ = self._is_json_data(data)
                if is_json:
                    self._preview_json(data, asset)
                else:
                    self._show_text_preview('FontFamily data could not be parsed as JSON')
            elif asset_type == 63:  # TexturePack
                self._preview_texturepack(data, asset_id)
            else:
                # Check if unknown asset is actually JSON
                is_json, _ = self._is_json_data(data)
                if is_json:
                    self._preview_json(data, asset)
                else:
                    # Show as hex dump for other types
                    self._preview_hex(data, asset)

        except Exception as e:
            self._show_text_preview(f'Error previewing asset: {e}')

    def _show_context_menu(self, position):
        """Show right-click context menu."""
        menu = QMenu(self)

        # Get selected rows
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        # Add actions
        add_to_replacer_action = menu.addAction('Add IDs to Replacer')

        # Export submenu with format options
        export_menu = menu.addMenu('Export Selected')

        # Get asset types from selection to determine available formats
        asset_types = set()
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    asset_types.add(asset['type'])

        # Determine available formats (intersection of all selected types)
        available_formats = None
        for asset_type in asset_types:
            formats = set(self.cache_manager.get_available_export_formats(asset_type))
            if available_formats is None:
                available_formats = formats
            else:
                available_formats &= formats

        if not available_formats:
            available_formats = {'raw', 'bin'}

        # Add format options
        export_actions = {}
        format_labels = {
            'converted_obj': 'Converted (.obj)',
            'converted_rbxmx': 'Converted (.rbxmx)',
            'converted_png': 'Converted (.png)',
            'converted_audio': 'Converted (.ogg/.mp3)',
            'converted': 'Converted (.xml)',
            'converted_images': 'Converted (Images)',
            'bin': 'Binary (decompressed)',
            'raw': 'Raw (original cache)',
        }
        for fmt in ['converted_obj', 'converted_rbxmx', 'converted_png', 'converted_audio', 'converted', 'converted_images', 'bin', 'raw']:
            if fmt in available_formats:
                action = export_menu.addAction(format_labels[fmt])
                export_actions[action] = fmt

        menu.addSeparator()

        # Copy submenu
        copy_menu = menu.addMenu('Copy')
        copy_hash_action = copy_menu.addAction('Hash/Name')
        copy_id_action = copy_menu.addAction('Asset ID')
        copy_url_action = copy_menu.addAction('URL')
        copy_menu.addSeparator()
        copy_creator_name_action = copy_menu.addAction('Creator Name')
        copy_creator_id_action = copy_menu.addAction('Creator ID')

        # Add "Copy Converted" if at least one selected asset supports conversion
        copy_converted_action = None
        if any(f.startswith('converted') for f in available_formats):
            copy_menu.addSeparator()
            copy_converted_action = copy_menu.addAction('Converted Data')

        # Add Open Creator action below the Copy menu
        open_creator_action = menu.addAction('Open Creator')

        menu.addSeparator()
        delete_action = menu.addAction('Delete Selected')

        # Execute menu
        action = menu.exec(self.table.viewport().mapToGlobal(position))

        if action == add_to_replacer_action:
            self._add_selected_to_replacer()
        elif action in export_actions:
            self._export_selected_multiple(export_format=export_actions[action])
        elif action == delete_action:
            self._delete_selected()
        elif action == copy_hash_action:
            self._copy_column(1)   # Hash/Name
        elif action == copy_id_action:
            self._copy_column(3)   # Asset ID (shifted by Creator col)
        elif action == copy_url_action:
            self._copy_column(7)   # URL (shifted by Creator col)
        elif action == copy_creator_name_action:
            self._copy_creator_info('name')
        elif action == copy_creator_id_action:
            self._copy_creator_info('id')
        elif action == open_creator_action:
            self._open_creator_in_browser()
        elif action == copy_converted_action:
            self._copy_converted()

    def _copy_column(self, column: int):
        """Copy column contents for selected rows."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        values = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, column)
            if item:
                values.append(item.text())

        if values:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText('\n'.join(values))
            log_buffer.log('Scraper', f'Copied {len(values)} value(s) to clipboard')

    def _copy_creator_info(self, mode: str):
        """Copy creator name or creator ID for selected rows.

        Args:
            mode: 'name' to copy creator display name, 'id' to copy creator ID.
        """
        from PyQt6.QtWidgets import QApplication

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        values = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)  # Hash/Name carries UserRole asset data
            if not item:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if not asset:
                continue
            info = self._asset_info.get(asset['id'])
            if not info:
                continue
            if mode == 'name':
                val = info.get('creator_name') or ''
            else:
                val = str(info.get('creator_id') or '')
            if val:
                values.append(val)

        if values:
            QApplication.clipboard().setText('\n'.join(values))
            log_buffer.log('Scraper', f'Copied {len(values)} creator {mode}(s) to clipboard')

    def _open_creator_in_browser(self):
        """Open the creator (user or group) page(s) for the selected asset(s) in the default browser."""
        import webbrowser

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        opened = 0
        seen = set()
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if not item:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if not asset:
                continue
            info = self._asset_info.get(asset['id'])
            if not info:
                continue
            creator_id = info.get('creator_id')
            creator_type_val = info.get('creator_type')
            # creator_type may be numeric (1=user, 2=group) or a string; normalise to a boolean
            if isinstance(creator_type_val, int):
                is_group = (creator_type_val == 2)
            else:
                try:
                    is_group = 'group' in (str(creator_type_val) or '').lower() or 'community' in (str(creator_type_val) or '').lower()
                except Exception:
                    is_group = False
            if not creator_id:
                continue
            key = (('group' if is_group else 'user'), str(creator_id))
            if key in seen:
                continue
            seen.add(key)
            try:
                if is_group:
                    url = f'https://www.roblox.com/communities/{creator_id}'
                else:
                    url = f'https://www.roblox.com/users/{creator_id}'
                webbrowser.open(url)
                opened += 1
            except Exception:
                log_buffer.log('Scraper', f'Failed to open creator {creator_id} in browser')

        if opened:
            log_buffer.log('Scraper', f'Opened {opened} creator page(s) in browser')

    def _copy_converted(self):
        """Copy converted files to clipboard as Windows file objects."""
        import tempfile
        from pathlib import Path
        from PyQt6.QtCore import QUrl, QMimeData
        from PyQt6.QtWidgets import QApplication

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        # Only process first selected asset
        row = selected_rows[0].row()
        item = self.table.item(row, 1)
        if not item:
            return

        asset = item.data(Qt.ItemDataRole.UserRole)
        if not asset:
            return

        asset_id = asset['id']
        asset_type = asset['type']

        # Get resolved name if available
        resolved_name = None
        if asset_id in self._asset_info:
            resolved_name = self._asset_info[asset_id].get('resolved_name')

        try:
            # Get asset data
            data = self.cache_manager.get_asset(asset_id, asset_type)
            if not data:
                QMessageBox.warning(self, 'Error', f'Failed to load asset {asset_id}')
                return

            # Create temp directory for converted files
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_clipboard'
            temp_dir.mkdir(exist_ok=True)

            temp_file = None
            base_name = resolved_name if resolved_name else asset_id
            safe_base = self._sanitize_filename(base_name)

            # Convert based on type and save to temp file
            if asset_type == 4:  # Mesh - save as OBJ file
                from . import mesh_processing
                try:
                    obj_content = mesh_processing.convert(data)
                    if obj_content:
                        filename = f'{safe_base}.obj'
                        temp_file = temp_dir / filename
                        temp_file.write_text(obj_content, encoding='utf-8')
                    else:
                        QMessageBox.warning(self, 'Error', 'Failed to convert mesh to OBJ')
                        return
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Mesh conversion error: {e}')
                    return

            elif asset_type in (1, 13):  # Image, Decal - save as PNG
                try:
                    _KTX_MAGIC = (b'\xabKTX 11\xbb\r\n\x1a\n', b'\xabKTX 20\xbb\r\n\x1a\n')
                    export_data = data
                    if data[:12] in _KTX_MAGIC:
                        from .tools.ktx_to_png import convert as _ktx_convert
                        converted = _ktx_convert(data)
                        if converted:
                            export_data = converted
                    filename = f'{safe_base}.png'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(export_data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Image save error: {e}')
                    return

            elif asset_type == 3:  # Audio - save as OGG/MP3
                try:
                    # Determine extension
                    if data.startswith(b'OggS'):
                        ext = 'ogg'
                    elif data.startswith(b'ID3') or data.startswith(b'\xFF\xFB'):
                        ext = 'mp3'
                    else:
                        ext = 'ogg'

                    filename = f'{safe_base}.{ext}'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Audio save error: {e}')
                    return

            elif asset_type == 24:  # Animation - save as RBXMX
                try:
                    # Decompress if needed
                    if data.startswith(b'\x1f\x8b'):
                        data = gzip_module.decompress(data)

                    filename = f'{safe_base}.rbxmx'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Animation save error: {e}')
                    return

            elif asset_type == 63:  # TexturePack - save XML
                try:
                    filename = f'{safe_base}_texturepack.xml'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'TexturePack save error: {e}')
                    return

            elif asset_type == 39:  # SolidModel - convert to OBJ and save
                try:
                    import gzip as gzip_module
                    from .tools.solidmodel_converter.converter import deserialize_rbxm, _export_obj_from_doc

                    decompressed = data
                    if data.startswith(b'\x1f\x8b'):
                        decompressed = gzip_module.decompress(data)

                    # Deserialize and export to a temporary OBJ file then copy into our temp_dir
                    doc = deserialize_rbxm(decompressed)
                    with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as f:
                        temp_obj_path = Path(f.name)

                    try:
                        _export_obj_from_doc(doc, temp_obj_path, decompose=False)
                        obj_content = temp_obj_path.read_text(encoding='utf-8')
                        filename = f'{safe_base}.obj'
                        temp_file = temp_dir / filename
                        temp_file.write_text(obj_content, encoding='utf-8')
                    finally:
                        try:
                            if temp_obj_path.exists():
                                temp_obj_path.unlink()
                        except Exception:
                            pass
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'SolidModel conversion error: {e}')
                    return

            # Copy file to clipboard
            if temp_file and temp_file.exists():
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(temp_file))])
                QApplication.clipboard().setMimeData(mime_data)
                log_buffer.log('Scraper', f'Copied file to clipboard: {temp_file.name}')
                QMessageBox.information(self, 'Success', f'File copied to clipboard:\n{temp_file.name}\n\nYou can now paste it anywhere.')

        except Exception as e:
            QMessageBox.warning(self, 'Error', f'Copy error: {e}')

    def _export_selected_multiple(self, export_format: str = 'converted'):
        """Export multiple selected assets."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, 'No Selection', 'Please select asset(s) to export')
            return

        # Collect assets to export
        assets_to_export = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    assets_to_export.append(asset)

        if not assets_to_export:
            return

        # Export all with sanitized resolved names
        exported_count = 0
        for asset in assets_to_export:
            asset_id = asset['id']
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')
            safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

            if self.cache_manager.export_asset(
                asset['id'], asset['type'],
                resolved_name=safe_name,
                export_format=export_format
            ):
                exported_count += 1

        # Determine export location based on format (matching cache_manager logic)
        if export_format == 'raw':
            format_dir = self.cache_manager.export_dir / 'raw'
        elif export_format == 'bin':
            format_dir = self.cache_manager.export_dir / 'bin'
        else:  # All 'converted*' formats go to 'converted' directory
            format_dir = self.cache_manager.export_dir / 'converted'
        
        log_buffer.log('Scraper', f'Exported {exported_count}/{len(assets_to_export)} assets as {export_format}')
        QMessageBox.information(
            self,
            'Export Complete',
            f'Exported {exported_count} asset(s) as {export_format}\n\nLocation: {format_dir}'
        )

    def _add_selected_to_replacer(self):
        """Add selected asset IDs to replacer."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        asset_ids = []
        for row_index in selected_rows:
            row = row_index.row()
            # Asset ID is in column 3 (columns: 0 marker, 1 Hash/Name, 2 Creator, 3 Asset ID)
            id_item = self.table.item(row, 3)
            if id_item:
                asset_ids.append(id_item.text())

        if not asset_ids:
            return

        # Try to find the replacer entry field (walk up parent chain)
        replacer_window = None
        widget = self
        while widget is not None:
            if hasattr(widget, 'replace_entry'):
                replacer_window = widget
                break
            widget = widget.parent() if hasattr(widget, 'parent') else None

        if replacer_window:
            # Add to existing IDs if there are any
            current_text = replacer_window.replace_entry.text().strip()
            if current_text:
                new_text = current_text + ', ' + ', '.join(asset_ids)
            else:
                new_text = ', '.join(asset_ids)
            replacer_window.replace_entry.setText(new_text)

            log_buffer.log('Scraper', f'Added {len(asset_ids)} asset ID(s) to replacer')
            QMessageBox.information(
                self,
                'Added to Replacer',
                f'Added {len(asset_ids)} asset ID(s) to replacer:\n{", ".join(asset_ids[:5])}{"..." if len(asset_ids) > 5 else ""}'
            )
        else:
            # Fallback: copy to clipboard if not in replacer window
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(', '.join(asset_ids))

            log_buffer.log('Scraper', f'Copied {len(asset_ids)} asset ID(s) to clipboard')
            QMessageBox.information(
                self,
                'Copied to Clipboard',
                f'Copied {len(asset_ids)} asset ID(s) to clipboard:\n{", ".join(asset_ids[:5])}{"..." if len(asset_ids) > 5 else ""}'
            )

    def _stop_preview(self):
        """Stop current preview and hide button."""
        self._selected_asset_id = None
        self._clear_preview()
        self.stop_preview_btn.hide()
        self.table.clearSelection()
        self.table.setCurrentItem(None)
        # Show default preview message
        self.image_label.setText('Select an asset to preview')
        self.image_label.show()

    def _clear_preview(self):
        """Clear all preview widgets and stop any running loaders."""
        # Stop all worker threads first
        self._stop_all_loaders()

        # Hide and clear UI widgets
        self.obj_viewer.hide()
        self.obj_viewer.clear()
        self.image_label.clear()
        
        # Completely hide the preview window as requested
        self.preview_panel.hide()
        # Reset preview group title back to default
        try:
            if hasattr(self, 'preview_group'):
                self.preview_group.setTitle('Preview')
        except Exception:
            pass
        
        # Deselect currently tracked asset in tree/internal state
        self._selected_asset_id = None
        self.table.clearSelection()
        self.table.setCurrentItem(None)

        self._current_pixmap = None
        self.audio_wrapper.hide()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        self.animation_viewer.hide()
        self.animation_viewer.clear()
        self.text_viewer.hide()
        self.text_viewer.clear()

        # Clean up texture pack widgets
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

    def _stop_all_loaders(self):
        """Stop all running preview loader threads."""
        if self._image_loader is not None:
            self._image_loader.stop()
            self._image_loader.quit()
            self._image_loader.wait()
            self._image_loader = None

        if self._mesh_loader is not None:
            self._mesh_loader.stop()
            self._mesh_loader.quit()
            self._mesh_loader.wait()
            self._mesh_loader = None

        if self._animation_loader is not None:
            self._animation_loader.stop()
            self._animation_loader.quit()
            self._animation_loader.wait()
            self._animation_loader = None

        if self._texturepack_loader is not None:
            self._texturepack_loader.stop()
            self._texturepack_loader.quit()
            self._texturepack_loader.wait()
            self._texturepack_loader = None

    def eventFilter(self, obj, event):
        """Global event filter to catch space key and toggle audio play/pause."""
        try:
            if event.type() == QEvent.Type.KeyPress:
                # Space toggles play/pause when audio preview is active
                if event.key() == Qt.Key.Key_Space:
                    if self.audio_player and self.audio_wrapper.isVisible():
                        try:
                            # Toggle play/pause on the audio widget
                            self.audio_player._toggle_play_pause()
                        except Exception:
                            pass
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _remove_audio_key_filter(self):
        """Remove global audio key event filter if installed."""
        try:
            if self._audio_key_filter_installed:
                from PyQt6.QtWidgets import QApplication
                QApplication.instance().removeEventFilter(self)
                self._audio_key_filter_installed = False
        except Exception:
            pass

    def _on_splitter_moved(self, pos: int, index: int):
        """Handle splitter resize to rescale image."""
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def _show_loading(self):
        """Show loading indicator."""
        self.loading_label.show()

    def _hide_loading(self):
        """Hide loading indicator."""
        self.loading_label.hide()

    def _preview_mesh(self, data: bytes, asset_id: str):
        """Preview a mesh asset in 3D using background thread."""
        # Track which asset this loader is for so we can ignore stale results
        self._mesh_loader_asset_id = asset_id
        self._mesh_loader = MeshLoaderThread(data, asset_id)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)
        self._mesh_loader.error.connect(lambda e: self._show_text_preview(f'Mesh error: {e}'))
        self._mesh_loader.start()

    def _on_mesh_ready(self, obj_content: str):
        """Handle mesh loaded from background thread."""
        # Ignore if selection has changed since loader started
        try:
            if getattr(self, '_mesh_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale mesh result ignored')
                return
        except Exception:
            pass

        self._hide_loading()
        self.obj_viewer.load_obj(obj_content, '')
        self.obj_viewer.show()
        self.stop_preview_btn.show()

    def _preview_solidmodel(self, data: bytes, asset_id: str):
        """Preview a SolidModel asset in 3D using background thread."""
        # Track which asset this loader is for so we can ignore stale results
        self._mesh_loader_asset_id = asset_id
        self._mesh_loader = SolidModelLoaderThread(data, asset_id)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)
        self._mesh_loader.error.connect(lambda e: self._show_text_preview(f'SolidModel error: {e}'))
        self._mesh_loader.start()

    def _preview_image(self, data: bytes):
        """Preview an image asset using background thread."""
        # Track current selection so we ignore stale image results
        self._image_loader_asset_id = getattr(self, '_selected_asset_id', None)
        self._image_loader = ImageLoaderThread(data)
        self._image_loader.image_ready.connect(self._on_image_ready)
        self._image_loader.error.connect(lambda e: self._show_text_preview(f'Image error: {e}'))
        self._image_loader.start()

    def _on_image_ready(self, pixmap: QPixmap):
        """Handle image loaded from background thread."""
        # Ignore if selection has changed since loader started
        try:
            if getattr(self, '_image_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale image result ignored')
                return
        except Exception:
            pass

        self._hide_loading()
        self._current_pixmap = pixmap
        self._scale_and_show_image(pixmap)
        self.image_label.show()
        self.stop_preview_btn.show()

    def _scale_and_show_image(self, pixmap: QPixmap):
        """Scale pixmap to fit container and display it."""
        container_width = self.preview_scroll.viewport().width() - 20
        container_height = self.preview_scroll.viewport().height() - 20

        if container_width < 100:
            container_width = 400
        if container_height < 100:
            container_height = 400

        # Scale to fit within container while maintaining aspect ratio
        scaled = pixmap.scaled(
            container_width, container_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        self.image_label.setPixmap(scaled)

    def _show_image_context_menu(self, pos):
        """Show context menu for image preview."""
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        from PyQt6.QtWidgets import QApplication

        menu = QMenu(self)
        copy_action = menu.addAction('Copy Image')

        action = menu.exec(self.image_label.mapToGlobal(pos))
        if action == copy_action:
            QApplication.clipboard().setPixmap(self._current_pixmap)

    def _preview_texturepack(self, data: bytes, asset_id: str):
        """Preview a texture pack by showing all texture maps."""
        import xml.etree.ElementTree as ET

        try:
            # Clean up previous texture pack if any
            if self.texturepack_widget is not None:
                self.texturepack_widget.deleteLater()
                self.texturepack_widget = None
            if self._texturepack_loader is not None:
                self._texturepack_loader.stop()
                self._texturepack_loader.quit()
                self._texturepack_loader.wait()
                self._texturepack_loader = None

            # Parse XML to get texture map IDs
            xml_text = data.decode('utf-8', errors='replace')
            self._texturepack_xml = xml_text  # Store for context menu
            root = ET.fromstring(xml_text)

            # Extract texture map IDs in document order.
            # map_index is POSITIONAL — the Nth map tag present in this XML.
            # This matches the fidelity slot encoding Roblox uses in batch requests.
            _KNOWN_MAP_TAGS = {'color', 'albedo', 'normal', 'metalness', 'roughness',
                               'emissive', 'height', 'orm', 'diffuse', 'normalmap',
                               'bumpmap', 'heightmap', 'displacement'}
            maps = {}          # display_name -> map_id_str
            maps_indices = {}  # display_name -> positional slot_idx
            slot_idx = 0
            for child in root:
                tag_lower = child.tag.lower().lstrip('{').split('}')[-1]
                if tag_lower not in _KNOWN_MAP_TAGS:
                    continue
                text = (child.text or '').strip()
                if text.isdigit() and text != '0':
                    display_name = tag_lower.capitalize()
                    maps[display_name] = text
                    maps_indices[display_name] = slot_idx
                slot_idx += 1  # every known slot tag advances the index

            if not maps:
                self._show_text_preview(f'No texture maps found in texture pack {asset_id}')
                return

            # Clear texture data storage
            self._texturepack_data = {}

            # Create container widget for texture pack preview
            self.texturepack_widget = QWidget()
            tp_layout = QVBoxLayout()
            tp_layout.setContentsMargins(0, 0, 0, 0)
            tp_layout.setSpacing(10)

            # Store references for async loading
            self._tp_image_labels = {}
            self._tp_pixmaps = {}  # Store pixmaps for copy

            # Create placeholder for each texture map
            for map_name, map_id in maps.items():
                map_index = maps_indices.get(map_name, '?')
                slot_key = f'{asset_id}:{map_index}'
                # Header: Name  |  sub-asset ID  |  slot X  (slot X is what goes in replace_ids)
                header = QLabel(f'{map_name}  |  {asset_id}:{map_index}')
                header.setStyleSheet('font-weight: bold; color: #888; padding: 5px;')
                header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                tp_layout.addWidget(header)

                # Image placeholder with context menu
                img_label = QLabel('Loading...')
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_label.setStyleSheet('background-color: palette(base); padding: 10px; min-height: 100px;')
                img_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                img_label.setProperty('map_name', map_name)
                img_label.setProperty('map_id', map_id)
                img_label.setProperty('map_index', map_index)
                img_label.setProperty('slot_key', slot_key)
                img_label.customContextMenuRequested.connect(
                    lambda pos, lbl=img_label: self._show_texturepack_context_menu(pos, lbl)
                )
                tp_layout.addWidget(img_label)
                self._tp_image_labels[map_name] = img_label

            tp_layout.addStretch()
            self.texturepack_widget.setLayout(tp_layout)
            self.preview_container_layout.addWidget(self.texturepack_widget)
            self.texturepack_widget.show()
            self.stop_preview_btn.show()

            # Start async loading of textures
            self._texturepack_loader = TexturePackLoaderThread(maps, self.cache_manager, self.cache_scraper)
            self._texturepack_loader.texture_loaded.connect(self._on_texturepack_texture_loaded)
            self._texturepack_loader.texture_error.connect(self._on_texturepack_texture_error)
            self._texturepack_loader.start()

        except Exception as e:
            self._show_text_preview(f'Texture pack preview error: {e}')

    def _on_texturepack_texture_loaded(self, map_name: str, map_id: str, hash_val: str, data: bytes):
        """Handle loaded texture from texture pack."""
        # Hide loading on first texture
        self._hide_loading()

        try:
            if map_name not in self._tp_image_labels:
                return

            img_label = self._tp_image_labels[map_name]

            # Check if widget still exists
            try:
                _ = img_label.isVisible()
            except RuntimeError:
                return

            # Store texture data for context menu
            self._texturepack_data[map_name] = {
                'id': map_id,
                'hash': hash_val,
                'data': data
            }
            # Update label property with hash
            img_label.setProperty('map_hash', hash_val)

            # Load image
            image = Image.open(io.BytesIO(data))
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            elif image.mode == 'RGB':
                image = image.convert('RGBA')

            # Scale up small images to 512x512 minimum
            min_size = 512
            if image.width < min_size or image.height < min_size:
                # Scale to at least 512 on the smaller dimension
                scale_factor = max(min_size / image.width, min_size / image.height)
                new_width = int(image.width * scale_factor)
                new_height = int(image.height * scale_factor)
                image = image.resize((new_width, new_height), Image.Resampling.NEAREST)

            qimage = QImage(
                image.tobytes(),
                image.width,
                image.height,
                QImage.Format.Format_RGBA8888
            )
            pixmap = QPixmap.fromImage(qimage)

            # Store original pixmap for copy
            self._tp_pixmaps[map_name] = pixmap

            # Scale to fit container
            container_width = self.preview_scroll.viewport().width() - 30
            if container_width < 100:
                container_width = 400

            if pixmap.width() > container_width:
                scaled = pixmap.scaledToWidth(container_width, Qt.TransformationMode.SmoothTransformation)
            else:
                scaled = pixmap

            img_label.setPixmap(scaled)
            img_label.setStyleSheet('')

        except Exception as e:
            self._on_texturepack_texture_error(map_name, str(e))

    def _on_texturepack_texture_error(self, map_name: str, error: str):
        """Handle texture load error."""
        try:
            if map_name not in self._tp_image_labels:
                return

            img_label = self._tp_image_labels[map_name]

            try:
                _ = img_label.isVisible()
            except RuntimeError:
                return

            img_label.setText(f'Error: {error}')
            img_label.setStyleSheet('color: #ff6b6b; padding: 10px;')
        except Exception:
            pass

    def _show_texturepack_context_menu(self, pos, label: QLabel):
        """Show context menu for texturepack image."""
        from PyQt6.QtWidgets import QApplication

        map_name = label.property('map_name')
        map_id = label.property('map_id')
        map_hash = label.property('map_hash') or ''
        slot_key = label.property('slot_key') or ''

        menu = QMenu(self)

        # Copy image
        copy_image_action = menu.addAction('Copy Image')

        menu.addSeparator()

        # Copy name/slot-key/sub-asset-id/hash
        copy_name_action = menu.addAction(f'Copy Name ({map_name})')
        # "Copy ID" intentionally copies slot key, because this is the exact
        # value users should paste into replace_ids for per-slot replacement.
        copy_id_action = menu.addAction(f'Copy ID ({slot_key}) (Use this for Replacer)')
        copy_subasset_action = menu.addAction(f'Copy Sub-Asset ID ({map_id}) (Cannot be Replaced)')
        copy_hash_action = None
        if map_hash:
            copy_hash_action = menu.addAction(f'Copy Hash ({map_hash[:16]}...)')

        menu.addSeparator()

        # Copy XML
        copy_xml_action = menu.addAction('Copy TexturePack XML')

        action = menu.exec(label.mapToGlobal(pos))

        if action == copy_image_action:
            pixmap = self._tp_pixmaps.get(map_name)
            if pixmap and not pixmap.isNull():
                QApplication.clipboard().setPixmap(pixmap)
        elif action == copy_name_action:
            QApplication.clipboard().setText(map_name)
        elif action == copy_id_action:
            QApplication.clipboard().setText(slot_key)
        elif action == copy_subasset_action:
            QApplication.clipboard().setText(str(map_id))
        elif action == copy_hash_action and map_hash:
            QApplication.clipboard().setText(map_hash)
        elif action == copy_xml_action:
            QApplication.clipboard().setText(self._texturepack_xml)

    def _preview_audio(self, data: bytes, asset_id: str):
        """Preview an audio asset."""
        import tempfile
        from pathlib import Path

        try:
            # Track asset id for this audio preview to avoid stale UI updates
            self._audio_preview_asset_id = asset_id

            # If selection changed since request, abort
            if getattr(self, '_selected_asset_id', None) != asset_id:
                log_buffer.log('Preview', 'Ignored stale audio preview request')
                return
            # Create temporary file for audio
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_audio'
            temp_dir.mkdir(exist_ok=True)

            # Determine file extension (default to mp3)
            temp_file = temp_dir / f'{asset_id}.mp3'

            # Write audio data to temp file
            with open(temp_file, 'wb') as f:
                f.write(data)

            # Create audio player with config manager for volume persistence
            self.audio_player = AudioPlayerWidget(str(temp_file), self, self.config_manager)

            # Clear previous audio widgets
            while self.audio_container_layout.count():
                child = self.audio_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Add new audio player
            # Ensure selection is still the same asset before showing
            if getattr(self, '_selected_asset_id', None) != asset_id:
                try:
                    self.audio_player.deleteLater()
                except Exception:
                    pass
                # Ensure we don't keep a dangling reference
                try:
                    self.audio_player = None
                except Exception:
                    pass
                log_buffer.log('Preview', 'Aborted adding audio widget for stale selection')
                return

            self.audio_container_layout.addWidget(self.audio_player)
            self.audio_wrapper.show()
            self.stop_preview_btn.show()

            # Install global event filter to catch Space for play/pause while audio preview is active
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.instance().installEventFilter(self)
                self._audio_key_filter_installed = True
            except Exception:
                self._audio_key_filter_installed = False

            # When audio stops or widget is deleted, remove the event filter
            try:
                self.audio_player.stopped.connect(lambda: self._remove_audio_key_filter())
            except Exception:
                pass

        except Exception as e:
            self._show_text_preview(f'Audio preview error: {e}')
            log_buffer.log('Scraper', f'Audio preview error: {e}')

    def _preview_animation(self, data: bytes, asset_id: str):
        """Preview an animation asset (RBXM XML format) using background thread."""
        # Track which asset this animation loader is for
        self._animation_loader_asset_id = asset_id
        self._show_loading()
        self._animation_loader = AnimationLoaderThread(data, asset_id)
        self._animation_loader.animation_ready.connect(self._on_animation_ready)
        self._animation_loader.error.connect(lambda e: self._show_text_preview(f'Animation error: {e}'))
        self._animation_loader.start()

    def _on_animation_ready(self, data: bytes):
        """Handle animation data ready from background thread."""
        # Ignore if selection changed since loader started
        try:
            if getattr(self, '_animation_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale animation result ignored')
                return
        except Exception:
            pass

        self._hide_loading()
        try:
            # Load in the animation viewer (must be on main thread for OpenGL)
            if self.animation_viewer.load_animation(data):
                self.animation_viewer.show()
                self.stop_preview_btn.show()
                return

            # Fallback: try to decode as XML for text display
            text = data.decode('utf-8', errors='replace')

            # Check if it's XML
            if text.strip().startswith('<'):
                # Format XML for display
                import xml.etree.ElementTree as ET
                try:
                    ET.fromstring(data)
                    # Pretty print XML
                    import xml.dom.minidom
                    dom = xml.dom.minidom.parseString(data)
                    pretty_xml = dom.toprettyxml(indent='  ')
                    # Remove extra blank lines
                    lines = [line for line in pretty_xml.split('\n') if line.strip()]
                    self._show_text_preview('\n'.join(lines[:500]))  # Limit lines
                except Exception:
                    # Fallback to raw text
                    self._show_text_preview(f'Animation data\nSize: {self._format_size(len(data))}\n\n{text[:5000]}')
            else:
                # Binary format, show hex
                reason = "This animation could not be loaded because it appears to be an unrecognized or unsupported animation format."
                self._preview_hex(data, {'id': '', 'type_name': 'Animation'}, reason=reason)

        except Exception as e:
            self._show_text_preview(f'Animation preview error: {e}')

    def _preview_font(self, data: bytes):
        """Preview a font asset (TTF, OTF, TTC)."""
        try:
            log_buffer.log('Preview', f'Loading font ({len(data)} bytes)')
            
            # Create font viewer widget
            font_viewer = FontViewerWidget(data, self)
            
            # Clear previous font widgets
            while self.font_container_layout.count():
                child = self.font_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            
            # Add new font viewer
            self.font_container_layout.addWidget(font_viewer)
            self.font_wrapper.show()
            self.stop_preview_btn.show()
            
        except Exception as e:
            self._show_text_preview(f'Font preview error: {e}')
            log_buffer.log('Preview', f'Font preview error: {e}')

    def _is_json_data(self, data: bytes) -> tuple[bool, dict | list | None]:
        """
        Detect if binary data is valid JSON.
        
        Returns:
            tuple: (is_json: bool, parsed_data: dict|list|None)
        """
        if not data or len(data) < 2:
            return False, None

        # Check for gzip compression
        if data[:2] == b'\x1f\x8b':
            try:
                data = gzip_module.decompress(data)
            except Exception:
                return False, None

        # Try UTF-8 decoding first (most common)
        for encoding in ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be']:
            try:
                text = data.decode(encoding)
                parsed = json.loads(text)
                return True, parsed
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

        return False, None

    def _preview_json(self, data: bytes, asset: dict):
        """Display JSON data in the JSON viewer."""
        is_json, parsed_data = self._is_json_data(data)
        
        if not is_json or parsed_data is None:
            # Fallback to hex dump
            self._preview_hex(data, asset)
            return

        # Persist the detected JSON type to the cache index
        asset_id = asset['id']
        asset_type = asset['type']
        try:
            self.cache_manager.set_detected_type(asset_id, asset_type, 'Json')
            
            # Update the table type display immediately to show "Json"
            current_row = self.table.currentRow()
            if current_row >= 0:
                type_item = self.table.item(current_row, 4)  # Type column is index 4
                if type_item:
                    # Update to 'Json' (now persistent)
                    type_item.setText('Json')
        except Exception:
            pass

        # Load and display in JSON viewer
        self.json_viewer.load_json(parsed_data)
        self._hide_loading()
        self.json_viewer.show()
        self.stop_preview_btn.show()

    def _preview_hex(self, data: bytes, asset: dict, reason: str = None):
        """Show hex dump preview."""
        # Show first 1KB as hex dump
        preview_size = min(1024, len(data))
        hex_lines = []

        hex_lines.append(f"Asset ID: {asset['id']}")
        hex_lines.append(f"Type: {asset['type_name']}")
        hex_lines.append(f"Size: {self._format_size(len(data))}")
        if reason:
            hex_lines.append(f"\nWhy is this a Hex Dump?: {reason}")
        hex_lines.append(f"\nFirst {preview_size} bytes (hex dump):\n")

        for i in range(0, preview_size, 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
            ascii_part = ''.join(
                chr(b) if 32 <= b < 127 else '.'
                for b in data[i:i+16]
            )
            hex_lines.append(f'{i:08x}  {hex_part:<48}  {ascii_part}')

        if len(data) > preview_size:
            hex_lines.append(f'\n... ({len(data) - preview_size} more bytes)')

        self._show_text_preview('\n'.join(hex_lines))
        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.text_viewer.setFont(mono_font)
        self.text_viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def _show_text_preview(self, text: str):
        """Show text in the text viewer."""
        self._hide_loading()
        self.text_viewer.setFont(self._text_viewer_default_font)
        self.text_viewer.setLineWrapMode(self._text_viewer_default_wrap)
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()
        self.stop_preview_btn.show()

    # ------------------------------------------------------------------
    # Load Asset dialog
    # ------------------------------------------------------------------

    def _show_load_asset_dialog(self):
        """Show a dialog for manually entering asset IDs to download from Roblox."""
        from ..utils import get_icon_path

        dialog = QDialog(self)
        dialog.setWindowTitle('Load Assets')
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel('Load Assets from Roblox')
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        hint = QLabel('Enter asset IDs separated by commas, spaces, newlines, or semicolons.')
        hint.setStyleSheet('color: gray; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setPlaceholderText('e.g. 1818, 1234567890, 9876543210')
        layout.addWidget(text_edit)

        status_label = QLabel('')
        status_label.setStyleSheet('color: #888; font-size: 9pt;')
        layout.addWidget(status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        load_btn = QPushButton('Load Asset IDs')
        btn_layout.addWidget(load_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)

        def on_load_clicked():
            content = text_edit.toPlainText().strip()
            if not content:
                return

            # Parse IDs: support commas, spaces, newlines, semicolons as separators
            content = content.replace('\n', ',').replace(';', ',').replace(' ', ',')
            raw_ids = []
            for part in content.split(','):
                part = part.strip()
                if part:
                    try:
                        raw_ids.append(int(part))
                    except ValueError:
                        pass  # Skip non-numeric entries

            if not raw_ids:
                status_label.setText('No valid asset IDs found.')
                status_label.setStyleSheet('color: #cc5555; font-size: 9pt;')
                return

            # Deduplicate while preserving order
            seen = set()
            asset_ids = []
            for aid in raw_ids:
                if aid not in seen:
                    seen.add(aid)
                    asset_ids.append(aid)

            # Disable button while loading
            load_btn.setEnabled(False)
            text_edit.setReadOnly(True)
            status_label.setText(f'Loading {len(asset_ids)} asset(s)...')
            status_label.setStyleSheet('color: #888; font-size: 9pt;')

            log_buffer.log('Scraper', f'[Load Asset] Starting load of {len(asset_ids)} asset ID(s)')

            # Stop any existing loader
            if self._asset_loader is not None:
                self._asset_loader.stop()
                self._asset_loader.quit()
                self._asset_loader.wait()
                self._asset_loader = None

            self._asset_loader = AssetLoaderThread(
                asset_ids, self.cache_manager, self.cache_scraper
            )

            def on_status(msg):
                status_label.setText(msg)

            def on_finished(loaded, failed):
                self._on_load_assets_complete(loaded, failed, dialog,
                                              load_btn, text_edit, status_label)

            self._asset_loader.status_message.connect(on_status)
            self._asset_loader.finished_loading.connect(on_finished)
            self._asset_loader.start()

        load_btn.clicked.connect(on_load_clicked)

        # Handle cleanup when dialog is closed (by user or programmatically)
        def on_dialog_finished():
            if self._asset_loader is not None:
                self._asset_loader.stop()
                self._asset_loader.quit()
                self._asset_loader.wait()
                self._asset_loader = None

        dialog.finished.connect(on_dialog_finished)

        # Non-modal: use show() so the rest of the app remains interactive
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _on_load_assets_complete(self, loaded: int, failed: int,
                                  dialog: QDialog, load_btn: QPushButton,
                                  text_edit: QTextEdit, status_label: QLabel):
        """Handle completion of the asset loading thread."""
        # Merge resolved metadata into _asset_info so names/creators show immediately
        if self._asset_loader is not None:
            resolved = getattr(self._asset_loader, '_resolved_metadata', {})
            creator_names = getattr(self._asset_loader, '_resolved_creator_names', {})
            for asset_id, meta in resolved.items():
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': '',
                        'resolved_name': None,
                        'creator_id': None,
                        'creator_name': None,
                        'creator_type': None,
                        'row': None,
                    }
                info = self._asset_info[asset_id]
                info['resolved_name'] = meta.get('name')
                info['creator_id'] = meta.get('creator_id')
                info['creator_type'] = meta.get('creator_type')
                cid = meta.get('creator_id')
                if cid is not None and cid in creator_names:
                    info['creator_name'] = creator_names[cid]
                elif meta.get('creator_name'):
                    info['creator_name'] = meta['creator_name']

                # Persist to index
                self._save_resolved_name_to_index(asset_id, meta.get('name', ''))
                self._save_resolved_creator_to_index(
                    asset_id,
                    meta.get('creator_id'),
                    info.get('creator_name'),
                    meta.get('creator_type'),
                )

            # Save index once
            try:
                self.cache_manager._save_index()
            except Exception:
                pass

        # Re-enable UI
        load_btn.setEnabled(True)
        text_edit.setReadOnly(False)

        if failed == 0:
            status_label.setText(f'Done! Loaded {loaded} asset(s).')
            status_label.setStyleSheet('color: #55cc66; font-size: 9pt;')
        else:
            status_label.setText(f'Done! Loaded {loaded}, failed {failed}.')
            status_label.setStyleSheet('color: #ccaa55; font-size: 9pt;')

        log_buffer.log('Scraper', f'[Load Asset] Finished: {loaded} loaded, {failed} failed')

        # Refresh the table to show newly loaded assets
        self._refresh_assets()