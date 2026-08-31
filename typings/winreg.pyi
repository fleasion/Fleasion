from types import TracebackType
from typing import Self

class HKEYType:
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def Close(self) -> None: ...

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

def OpenKey(
    key: HKEYType | int, sub_key: str, reserved: int = 0, access: int = ...
) -> HKEYType: ...
def CreateKey(key: HKEYType | int, sub_key: str) -> HKEYType: ...
def CreateKeyEx(
    key: HKEYType | int, sub_key: str, reserved: int = 0, access: int = ...
) -> HKEYType: ...
def DeleteValue(key: HKEYType, value: str) -> None: ...
def EnumKey(key: HKEYType, index: int) -> str: ...
def QueryValueEx(key: HKEYType, value_name: str | None) -> tuple[RegistryValue, int]: ...
def SetValueEx(
    key: HKEYType,
    value_name: str | None,
    reserved: int,
    value_type: int,
    value: object,
) -> None: ...
