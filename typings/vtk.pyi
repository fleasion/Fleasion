"""Minimal VTK surface used by Fleasion's legacy animation preview."""  # ruff: ignore[docstring-in-stub]

class vtkMatrix4x4:  # ruff: ignore[invalid-class-name]
    def Identity(self) -> None: ...  # ruff: ignore[invalid-function-name]
    def SetElement(self, row: int, column: int, value: float) -> None: ...  # ruff: ignore[invalid-function-name]
    def GetElement(self, row: int, column: int) -> float: ...  # ruff: ignore[invalid-function-name]
    @staticmethod
    def Multiply4x4(a: vtkMatrix4x4, b: vtkMatrix4x4, out: vtkMatrix4x4) -> None: ...  # ruff: ignore[invalid-function-name]
    @staticmethod
    def Invert(a: vtkMatrix4x4, out: vtkMatrix4x4) -> None: ...  # ruff: ignore[invalid-function-name]

class vtkLight:  # ruff: ignore[invalid-class-name]
    def SetLightTypeToHeadlight(self) -> None: ...  # ruff: ignore[invalid-function-name]
    def SetLightTypeToSceneLight(self) -> None: ...  # ruff: ignore[invalid-function-name]
    def SetPosition(self, x: float, y: float, z: float) -> None: ...  # ruff: ignore[invalid-function-name]
    def SetFocalPoint(self, x: float, y: float, z: float) -> None: ...  # ruff: ignore[invalid-function-name]
    def SetIntensity(self, intensity: float) -> None: ...  # ruff: ignore[invalid-function-name]

class vtkActor:  # ruff: ignore[invalid-class-name]
    def SetUserMatrix(self, matrix: vtkMatrix4x4) -> None: ...  # ruff: ignore[invalid-function-name]

class vtkRenderer:  # ruff: ignore[invalid-class-name]
    def RemoveAllLights(self) -> None: ...  # ruff: ignore[invalid-function-name]
    def AddLight(self, light: vtkLight) -> None: ...  # ruff: ignore[invalid-function-name]

class vtkCamera:  # ruff: ignore[invalid-class-name]
    focal_point: tuple[float, float, float]
    position: tuple[float, float, float]
    up: tuple[float, float, float]
    view_angle: float
    def SetClippingRange(self, near: float, far: float) -> None: ...  # ruff: ignore[invalid-function-name]
    def Azimuth(self, angle: float) -> None: ...  # ruff: ignore[invalid-function-name]
