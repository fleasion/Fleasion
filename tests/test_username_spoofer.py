import json
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_username_spoofer():
    root = Path(__file__).resolve().parents[1]
    module_name = "Fleasion.proxy.addons.username_spoofer"
    stubbed_names = (
        "Fleasion",
        "Fleasion.proxy",
        "Fleasion.proxy.addons",
        "Fleasion.utils",
        "Fleasion.utils.roblox_auth",
        module_name,
    )
    previous_modules = {name: sys.modules.get(name) for name in stubbed_names}

    try:
        for package_name in ("Fleasion", "Fleasion.proxy", "Fleasion.proxy.addons"):
            package = sys.modules.setdefault(package_name, types.ModuleType(package_name))
            package.__path__ = []

        utils = types.ModuleType("Fleasion.utils")
        utils.log_buffer = types.SimpleNamespace(log=lambda *_args, **_kwargs: None)
        sys.modules["Fleasion.utils"] = utils

        roblox_auth = types.ModuleType("Fleasion.utils.roblox_auth")
        roblox_auth.get_roblosecurity = lambda: None
        sys.modules["Fleasion.utils.roblox_auth"] = roblox_auth

        spec = importlib.util.spec_from_file_location(
            module_name,
            root / "src" / "Fleasion" / "proxy" / "addons" / "username_spoofer.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.UsernameSpoofer
    finally:
        for name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


UsernameSpoofer = _load_username_spoofer()


class _Request:
    def __init__(self, url: str):
        self.pretty_url = url


class _Response:
    def __init__(self, payload: dict):
        self.content = json.dumps(payload).encode("utf-8")


class _Flow:
    def __init__(self, url: str, payload: dict):
        self.request = _Request(url)
        self.response = _Response(payload)


class UsernameSpooferTests(unittest.TestCase):
    def test_gamejoin_creator_spoof_sets_authenticated_user_as_user_creator(self):
        spoofer = UsernameSpoofer()
        spoofer.set_runtime_state({"self_game_creator": True})
        flow = _Flow(
            "https://gamejoin.roblox.com/v1/join-game",
            {
                "joinScript": {
                    "CreatorId": 123,
                    "CreatorType": "Group",
                    "nested": {"creatorId": 456, "creatorType": "Group"},
                }
            },
        )

        with patch.object(UsernameSpoofer, "_fetch_authenticated_user_id", return_value=987):
            spoofer.response(flow)

        payload = json.loads(flow.response.content.decode("utf-8"))
        self.assertEqual(payload["joinScript"]["CreatorId"], 987)
        self.assertEqual(payload["joinScript"]["CreatorType"], "User")
        self.assertEqual(payload["joinScript"]["nested"]["creatorId"], 987)
        self.assertEqual(payload["joinScript"]["nested"]["creatorType"], "User")

    def test_gamejoin_creator_spoof_preserves_creator_type_representation(self):
        spoofer = UsernameSpoofer()
        spoofer.set_runtime_state({"self_game_creator": True})
        flow = _Flow(
            "https://gamejoin.roblox.com/v1/join-game",
            {
                "engineNumber": {"CreatorId": 123, "CreatorType": 1},
                "engineEnumString": {"CreatorId": 123, "CreatorType": "Enum.CreatorType.Group"},
                "joinScriptEnum": {"CreatorId": 123, "CreatorTypeEnum": "Group"},
                "idOnly": {"CreatorId": 123},
                "webNumber": {"creatorTargetId": 456, "creatorType": 2},
            },
        )

        with patch.object(UsernameSpoofer, "_fetch_authenticated_user_id", return_value=987):
            spoofer.response(flow)

        payload = json.loads(flow.response.content.decode("utf-8"))
        self.assertEqual(payload["engineNumber"]["CreatorId"], 987)
        self.assertEqual(payload["engineNumber"]["CreatorType"], 0)
        self.assertEqual(payload["engineEnumString"]["CreatorId"], 987)
        self.assertEqual(payload["engineEnumString"]["CreatorType"], "Enum.CreatorType.User")
        self.assertEqual(payload["joinScriptEnum"]["CreatorId"], 987)
        self.assertEqual(payload["joinScriptEnum"]["CreatorTypeEnum"], "User")
        self.assertEqual(payload["idOnly"]["CreatorId"], 987)
        self.assertNotIn("CreatorType", payload["idOnly"])
        self.assertEqual(payload["webNumber"]["creatorTargetId"], 987)
        self.assertEqual(payload["webNumber"]["creatorType"], 1)

    def test_gamejoin_creator_spoof_fetches_user_id_for_each_replacement(self):
        spoofer = UsernameSpoofer()
        spoofer.set_runtime_state({"self_game_creator": True})
        flows = [
            _Flow("https://gamejoin.roblox.com/v1/join-game", {"joinScript": {"CreatorId": 1}}),
            _Flow("https://gamejoin.roblox.com/v1/join-game-instance", {"joinScript": {"CreatorId": 2}}),
        ]

        with patch.object(UsernameSpoofer, "_fetch_authenticated_user_id", side_effect=[111, 222]) as fetch:
            for flow in flows:
                spoofer.response(flow)

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(json.loads(flows[0].response.content)["joinScript"]["CreatorId"], 111)
        self.assertEqual(json.loads(flows[1].response.content)["joinScript"]["CreatorId"], 222)

    def test_game_creator_spoof_counts_as_enabled(self):
        spoofer = UsernameSpoofer()

        spoofer.set_runtime_state({"self_game_creator": True})

        self.assertTrue(spoofer.is_enabled())


if __name__ == "__main__":
    unittest.main()
