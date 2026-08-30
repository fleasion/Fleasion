"""Minimal Windows winreg surface used by Fleasion."""  # ruff: ignore[docstring-in-stub]

from types import TracebackType

class HKEYType:
    def __enter__(self) -> HKEYType: ...  # ruff: ignore[non-self-return-type]
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def Close(self) -> None: ...  # ruff: ignore[invalid-function-name]

HKEY_CURRENT_USER: int
HKEY_LOCAL_MACHINE: int
KEY_ALL_ACCESS: int
KEY_QUERY_VALUE: int
KEY_SET_VALUE: int
REG_DWORD: int
REG_EXPAND_SZ: int
REG_MULTI_SZ: int
REG_SZ: int

type RegistryValue = str | int | list[str] | bytes | None

def OpenKey(  # ruff: ignore[invalid-function-name]
    key: HKEYType | int, sub_key: str, reserved: int = 0, access: int = ...
) -> HKEYType: ...
def CreateKey(key: HKEYType | int, sub_key: str) -> HKEYType: ...  # ruff: ignore[invalid-function-name]
def CreateKeyEx(  # ruff: ignore[invalid-function-name]
    key: HKEYType | int, sub_key: str, reserved: int = 0, access: int = ...
) -> HKEYType: ...
def DeleteValue(key: HKEYType, value: str) -> None: ...  # ruff: ignore[invalid-function-name]
def EnumKey(key: HKEYType, index: int) -> str: ...  # ruff: ignore[invalid-function-name]
def QueryValueEx(key: HKEYType, value_name: str | None) -> tuple[RegistryValue, int]: ...  # ruff: ignore[invalid-function-name]
def SetValueEx(  # ruff: ignore[invalid-function-name]
    key: HKEYType,
    value_name: str | None,
    reserved: int,
    type: int,  # ruff: ignore[builtin-argument-shadowing]
    value: object,
) -> None: ...
