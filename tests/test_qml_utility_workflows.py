from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QCoreApplication

from fleasion.cache.roblox_document import export_roblox_document
from fleasion.qml_api.animation_conversion import AnimationConversionApi
from fleasion.qml_api.subplace_blacklist import (
    GameJoinInterceptorChain,
    SubplaceBlacklistApi,
    parse_subplace_ids,
)
from fleasion.qml_api.reserved_rejoin import ReservedRejoinInterceptor
from fleasion.utils.animation_conversion import (
    PreparedAnimation,
    convert_animation_rig,
    prepare_animation_source,
)


def _pose_xml(name: str) -> str:
    return f"""<Item class="Pose" referent="RBX{name.replace(' ', '')}">
<Properties>
  <CoordinateFrame name="CFrame">
    <X>0</X><Y>0</Y><Z>0</Z>
    <R00>1</R00><R01>0</R01><R02>0</R02>
    <R10>0</R10><R11>1</R11><R12>0</R12>
    <R20>0</R20><R21>0</R21><R22>1</R22>
  </CoordinateFrame>
  <string name="Name">{name}</string>
  <float name="Weight">1</float>
</Properties>
</Item>"""


def _animation_xml(rig: str) -> bytes:
    torso = 'Torso' if rig == 'R6' else 'LowerTorso'
    return f"""<roblox version="4">
<Item class="KeyframeSequence" referent="RBXSequence">
<Properties><string name="Name">Animation</string></Properties>
<Item class="Keyframe" referent="RBXKeyframe">
<Properties><float name="Time">0</float></Properties>
{_pose_xml('HumanoidRootPart')}
{_pose_xml(torso)}
</Item>
</Item>
</roblox>""".encode()


def test_animation_conversion_pipeline_maps_both_player_rigs(tmp_path: Path) -> None:
    source = tmp_path / 'walk.rbxmx'
    source.write_bytes(_animation_xml('R6'))

    prepared = prepare_animation_source(source)
    converted = convert_animation_rig(prepared.xml_bytes, 'R15')
    names = {value.text for value in ET.fromstring(converted).iterfind(".//string[@name='Name']")}

    assert prepared.detected_rig == 'R6'
    assert not prepared.converted_from_binary
    assert 'LowerTorso' in names
    assert 'LeftUpperArm' in names

    returned = convert_animation_rig(converted, 'R6')
    returned_names = {
        value.text for value in ET.fromstring(returned).iterfind(".//string[@name='Name']")
    }
    assert 'Torso' in returned_names
    assert 'Left Arm' in returned_names


def test_animation_source_preparation_converts_binary_rbxm(tmp_path: Path) -> None:
    binary, _ = export_roblox_document(
        _animation_xml('R6'),
        'converted_document_rbxm',
        asset_type=24,
    )
    source = tmp_path / 'walk.rbxm'
    source.write_bytes(binary)

    prepared = prepare_animation_source(source)

    assert prepared.detected_rig == 'R6'
    assert prepared.converted_from_binary
    assert ET.fromstring(prepared.xml_bytes).find("Item[@class='KeyframeSequence']") is not None


def test_animation_conversion_rejects_documents_without_keyframes() -> None:
    document = b'<roblox version="4"><Item class="Folder"><Properties /></Item></roblox>'

    try:
        convert_animation_rig(document, 'R6')
    except ValueError as exc:
        assert 'KeyframeSequence' in str(exc)
    else:
        raise AssertionError('Expected invalid animation to be rejected')


def test_animation_bridge_loads_and_saves_asynchronously(tmp_path: Path) -> None:
    source = tmp_path / 'walk.rbxmx'
    source.write_bytes(_animation_xml('R6'))
    output = tmp_path / 'walk_r15.rbxmx'
    api = AnimationConversionApi()  # pyright: ignore[reportCallIssue]
    try:
        assert api.loadSource(str(source))
        _wait_for_task(api)
        assert api.detectedRig == 'R6'
        assert api.canConvertToR15
        assert api.convert('R15', str(output))
        _wait_for_task(api)
        assert output.is_file()
        assert api.lastOutputPath == str(output)
    finally:
        api.shutdown()


def test_subplace_id_parser_matches_legacy_normalization() -> None:
    assert parse_subplace_ids('001, +2; bad\n3 3') == ['1', '2', '3', '3']


class _Config:
    def __init__(self) -> None:
        self.subplace_blacklist: list[str] = []
        self.subplace_blacklist_mode = 'block'
        self.multi_instance_launching = False
        self.username_spoofer: dict[str, object] = {}


class _Request:
    def __init__(self, path: str, payload: dict[str, Any]) -> None:
        self.url = f'https://gamejoin.roblox.com{path}'
        self.pretty_url = self.url
        self.raw_content = json.dumps(payload).encode()
        self.headers: dict[str, str] = {}

    @property
    def content(self) -> bytes:
        return self.raw_content


class _Flow:
    def __init__(self, path: str, payload: dict[str, Any]) -> None:
        self.request = _Request(path, payload)
        self.response = None
        self.drop_request = False
        self.drop_status_code = 0
        self.drop_body = b''


def test_subplace_blacklist_blocks_or_stalls_and_honors_bypass() -> None:
    current_time = [100.0]
    config = _Config()
    api = SubplaceBlacklistApi(  # pyright: ignore[reportCallIssue]
        config,
        clock=lambda: current_time[0],
    )
    try:
        assert api.applyBlacklist('001, bad, 2, 2')
        assert config.subplace_blacklist == ['1', '2']
        blocked = _Flow('/v1/join-game', {'placeId': 1, 'gameJoinAttemptId': 'a'})
        api.request(blocked)
        assert blocked.drop_request
        assert blocked.drop_status_code == 200
        assert json.loads(blocked.drop_body)['status'] == 12

        api.mode = 'stall'
        stalled = _Flow('/v1/join-reserved-game', {'placeId': '2'})
        api.request(stalled)
        assert json.loads(stalled.drop_body) == {
            'jobId': None,
            'status': 1,
            'joinScriptUrl': None,
            'authenticationUrl': None,
            'authenticationTicket': None,
            'message': '',
            'joinScript': None,
            'queuePosition': 0,
        }

        private = _Flow('/v1/join-private-game', {'placeId': 1})
        api.request(private)
        assert not private.drop_request

        api.bypassForFiveSeconds()
        bypassed = _Flow('/v1/join-game-instance', {'placeId': 1})
        api.request(bypassed)
        assert not bypassed.drop_request
        current_time[0] += 5.1
        api.request(bypassed)
        assert bypassed.drop_request
    finally:
        api.shutdown()


def test_blacklist_runs_after_reserved_rejoin_redirect() -> None:
    config = _Config()
    blacklist = SubplaceBlacklistApi(config)  # pyright: ignore[reportCallIssue]
    rejoin = ReservedRejoinInterceptor()
    chain = GameJoinInterceptorChain(rejoin, blacklist)
    try:
        blacklist.applyBlacklist('202')
        rejoin.set_credentials('202', 'secret')
        assert rejoin.arm()
        flow = _Flow(
            '/v1/join-game',
            {'placeId': 101, 'gameJoinAttemptId': 'attempt'},
        )

        chain.request(flow)

        assert flow.request.url.endswith('/v1/join-reserved-game')
        assert flow.drop_request
        assert json.loads(flow.drop_body)['status'] == 12
    finally:
        blacklist.shutdown()


def test_utilities_api_composes_new_workflows_with_the_registered_interceptor(
    tmp_path: Path,
) -> None:
    from fleasion.qml_api.account_store import AccountStore
    from fleasion.qml_api.utilities import UtilitiesApi

    config = _Config()
    utilities = UtilitiesApi(  # pyright: ignore[reportCallIssue]
        config,
        account_store=AccountStore(tmp_path / 'accounts.json', tmp_path / 'accounts.key'),
    )
    try:
        blacklist = cast('Any', utilities.subplaceBlacklist)
        converter = cast('Any', utilities.animationConverter)
        assert blacklist.applyBlacklist('404')
        assert converter.sourceLoaded is False
        flow = _Flow('/v1/join-play-together-game', {'placeId': 404})

        utilities.interceptor().request(flow)

        assert flow.drop_request
    finally:
        utilities.shutdown()


def _wait_for_task(api: Any) -> None:
    application = QCoreApplication.instance()
    deadline = time.monotonic() + 5.0
    task = cast('Any', api.task)
    while task.busy and time.monotonic() < deadline:
        if application is not None:
            application.processEvents()
        time.sleep(0.005)
    if application is not None:
        application.processEvents()
    assert not task.busy


def test_linux_account_switch_surfaces_safe_write_reason(monkeypatch) -> None:
    from types import SimpleNamespace

    from fleasion.qml_api import utilities as utilities_module
    from fleasion.qml_api.account_store import StoredAccount
    from fleasion.qml_api.utilities import UtilitiesApi
    from fleasion.utils.roblox_auth import LinuxAuthWriteError

    class SignalStub:
        def __init__(self) -> None:
            self.values: list[tuple[object, ...]] = []

        def emit(self, *values: object) -> None:
            self.values.append(values)

    errors = SignalStub()
    account = StoredAccount('Player', 'encrypted', '123')
    fake = SimpleNamespace(
        _accounts=[account],
        _store=SimpleNamespace(cookie=lambda _account: 'secret-cookie'),
        errorOccurred=errors,
        notificationRequested=SignalStub(),
        selectedAccountChanged=SignalStub(),
        _selected_username='',
        _push_username_current_user=lambda _account: None,
    )
    monkeypatch.setattr(
        utilities_module,
        'set_roblosecurity',
        lambda _cookie: (_ for _ in ()).throw(
            LinuxAuthWriteError(
                'cookie_store_permission_denied',
                "Sober's local cookie store is read-only.",
            )
        ),
    )

    assert not UtilitiesApi.switchToAccount(fake, 0)  # type: ignore[arg-type]
    assert errors.values == [("Sober's local cookie store is read-only.",)]
