"""Shared helper for reading the .ROBLOSECURITY cookie from Roblox local storage."""

import base64
import json
import os
import re

try:
    import win32crypt  # type: ignore
except Exception:
    win32crypt = None


def get_roblosecurity() -> str | None:
    """Return the .ROBLOSECURITY cookie value from the local Roblox cookie store.

    Uses Windows DPAPI (win32crypt) to decrypt the stored cookie data.
    Returns None if the cookie is not found or cannot be decrypted.
    """
    if win32crypt is None:
        return None

    path = os.path.expandvars(r"%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat")
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies_data = data.get("CookiesData")
        if not cookies_data:
            return None
        enc = base64.b64decode(cookies_data)
        dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
        s = dec.decode("utf-8", errors="ignore")
        m = re.search(r"\.ROBLOSECURITY\s+([^\s;]+)", s)
        return m.group(1) if m else None
    except Exception:
        return None
