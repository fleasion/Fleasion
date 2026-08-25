import json

from fleasion.modifications.font_utils import (
    CUSTOM_FONT_PATH,
    CUSTOM_FONT_REL,
    FAMILIES_REL,
    apply_custom_font,
    restore_font_families,
)


def _family_bytes(asset_id: str, *, faces: int = 1) -> bytes:
    return json.dumps(
        {
            'name': 'Test Family',
            'faces': [
                {
                    'name': f'Face {index}',
                    'weight': 400,
                    'style': 'normal',
                    'assetId': asset_id,
                }
                for index in range(faces)
            ],
        }
    ).encode('utf-8')


def test_packaged_family_manifests_are_materialized_and_removed_on_restore(tmp_path):
    resource_root = tmp_path / 'asset_overlay'
    stash_dir = tmp_path / 'stash'
    packaged = {
        'Arimo.json': _family_bytes('rbxasset://fonts/Arimo.ttf'),
        'BuilderSans.json': _family_bytes('rbxasset://fonts/BuilderSans.ttf', faces=2),
    }

    apply_custom_font(
        b'\x00\x01\x00\x00font-one',
        [resource_root],
        stash_dir,
        family_manifest_loader=lambda _root: packaged,
    )

    assert (resource_root / CUSTOM_FONT_REL).read_bytes() == b'\x00\x01\x00\x00font-one'
    for name in packaged:
        family = json.loads((resource_root / FAMILIES_REL / name).read_text(encoding='utf-8'))
        assert family['faces']
        assert all(face['assetId'] == CUSTOM_FONT_PATH for face in family['faces'])

    # Re-applying must not mistake Fleasion-generated overlay manifests for
    # pre-existing user files and stash them as originals.
    apply_custom_font(
        b'\x00\x01\x00\x00font-two',
        [resource_root],
        stash_dir,
        family_manifest_loader=lambda _root: packaged,
    )
    restore_font_families([resource_root], stash_dir)

    assert not (resource_root / CUSTOM_FONT_REL).exists()
    assert not any((resource_root / FAMILIES_REL).glob('*.json'))


def test_preexisting_overlay_family_manifest_is_restored(tmp_path):
    resource_root = tmp_path / 'asset_overlay'
    stash_dir = tmp_path / 'stash'
    family_path = resource_root / FAMILIES_REL / 'BuilderSans.json'
    family_path.parent.mkdir(parents=True)
    original = _family_bytes('rbxasset://fonts/UserOverride.ttf')
    family_path.write_bytes(original)

    apply_custom_font(
        b'\x00\x01\x00\x00font',
        [resource_root],
        stash_dir,
        family_manifest_loader=lambda _root: {
            'BuilderSans.json': _family_bytes('rbxasset://fonts/Packaged.ttf')
        },
    )

    modified = json.loads(family_path.read_text(encoding='utf-8'))
    assert modified['faces'][0]['assetId'] == CUSTOM_FONT_PATH

    restore_font_families([resource_root], stash_dir)

    assert family_path.read_bytes() == original
