"""Proxy package."""

__all__ = ['ProxyMaster', 'check_and_patch_running_roblox_ca']


def __getattr__(name: str):
    if name in __all__:
        from .master import ProxyMaster, check_and_patch_running_roblox_ca

        values = {
            'ProxyMaster': ProxyMaster,
            'check_and_patch_running_roblox_ca': check_and_patch_running_roblox_ca,
        }
        return values[name]
    raise AttributeError(name)
