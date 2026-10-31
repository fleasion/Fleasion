from PySide6.QtWidgets import QWidget
from pyvista import PolyData
from vtk import vtkActor, vtkCamera, vtkRenderer

class QtInteractor:
    interactor: QWidget
    renderer: vtkRenderer
    bounds: tuple[float, float, float, float, float, float]
    camera: vtkCamera
    def __init__(self, parent: QWidget) -> None: ...
    def enable_anti_aliasing(self, mode: str) -> None: ...
    def set_background(self, color: tuple[float, float, float]) -> None: ...
    def add_axes(self) -> object: ...
    def add_mesh(
        self,
        mesh: PolyData,
        *,
        opacity: float,
        smooth_shading: bool,
        color: tuple[float, float, float],
        ambient: float,
        diffuse: float,
        specular: float,
        specular_power: float,
    ) -> vtkActor: ...
    def render(self) -> None: ...
    def update(self) -> None: ...
    def close(self) -> None: ...
