import json

from fleasion.utils import roblox_dirs


def test_normalise_roblox_dir_rejects_embedded_null():
    assert roblox_dirs._normalise_roblox_dir('/tmp/Roblox\x00bad') is None


def test_load_saved_roblox_dirs_skips_malformed_entries_and_keeps_scanning(tmp_path, monkeypatch):
    valid = tmp_path / 'valid-roblox-resource-dir'
    (valid / 'content').mkdir(parents=True)
    cache_file = tmp_path / 'roblox_dirs.json'
    cache_file.write_text(
        json.dumps(
            {
                'roblox_dirs': [
                    '/tmp/Roblox\x00bad',
                    {'not': 'a path'},
                    str(valid),
                ]
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(roblox_dirs, 'ROBLOX_DIRS_FILE', cache_file)

    assert roblox_dirs.load_saved_roblox_dirs() == [valid]
