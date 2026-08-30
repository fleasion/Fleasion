"""Minimal PyOpenGL surface used by Fleasion's legacy fixed-function viewers."""  # ruff: ignore[docstring-in-stub]

from typing import Protocol, SupportsInt

class _IntIndexable(Protocol):
    def __getitem__(self, index: int, /) -> SupportsInt: ...

GL_ALL_ATTRIB_BITS: int
GL_AMBIENT: int
GL_AMBIENT_AND_DIFFUSE: int
GL_BLEND: int
GL_COLOR_BUFFER_BIT: int
GL_COLOR_MATERIAL: int
GL_COMPILE: int
GL_CONSTANT_ALPHA: int
GL_DEPTH_BUFFER_BIT: int
GL_DEPTH_TEST: int
GL_DIFFUSE: int
GL_ENABLE_BIT: int
GL_FALSE: int
GL_FILL: int
GL_FRONT_AND_BACK: int
GL_LIGHT0: int
GL_LIGHT1: int
GL_LIGHT2: int
GL_LIGHTING: int
GL_LIGHT_MODEL_TWO_SIDE: int
GL_LINE: int
GL_LINES: int
GL_LINE_BIT: int
GL_MODELVIEW: int
GL_NORMALIZE: int
GL_NO_ERROR: int
GL_ONE_MINUS_CONSTANT_ALPHA: int
GL_ONE_MINUS_SRC_ALPHA: int
GL_POLYGON: int
GL_POLYGON_OFFSET_FILL: int
GL_POSITION: int
GL_PROJECTION: int
GL_QUADS: int
GL_RENDERER: int
GL_RGBA: int
GL_SHININESS: int
GL_SMOOTH: int
GL_SPECULAR: int
GL_SRC_ALPHA: int
GL_TRIANGLES: int
GL_TRUE: int
GL_UNSIGNED_BYTE: int
GL_VENDOR: int
GL_VERSION: int
GL_VIEWPORT: int

def glBegin(mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glBlendColor(red: float, green: float, blue: float, alpha: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glBlendFunc(sfactor: int, dfactor: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glCallList(list_: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glClear(mask: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glClearColor(red: float, green: float, blue: float, alpha: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glColor3f(red: float, green: float, blue: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glColor3fv(values: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glColor4f(red: float, green: float, blue: float, alpha: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glColorMaterial(face: int, mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glDeleteLists(list_: int, range_: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glDepthMask(flag: int | bool) -> None: ...  # ruff: ignore[invalid-function-name]
def glDisable(cap: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glEnable(cap: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glEnd() -> None: ...  # ruff: ignore[invalid-function-name]
def glEndList() -> None: ...  # ruff: ignore[invalid-function-name]
def glFrustum(  # ruff: ignore[invalid-function-name]
    left: float, right: float, bottom: float, top: float, near: float, far: float
) -> None: ...
def glGenLists(range_: int) -> int: ...  # ruff: ignore[invalid-function-name]
def glGetError() -> int: ...  # ruff: ignore[invalid-function-name]
def glGetIntegerv(pname: int) -> _IntIndexable: ...  # ruff: ignore[invalid-function-name]
def glGetString(name: int) -> bytes | None: ...  # ruff: ignore[invalid-function-name]
def glLightModeli(pname: int, param: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glLightfv(light: int, pname: int, params: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glLineWidth(width: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glLoadIdentity() -> None: ...  # ruff: ignore[invalid-function-name]
def glMaterialf(face: int, pname: int, param: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glMaterialfv(face: int, pname: int, params: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glMatrixMode(mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glMultMatrixf(matrix: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glNewList(list_: int, mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glNormal3f(nx: float, ny: float, nz: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glNormal3fv(values: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glOrtho(  # ruff: ignore[invalid-function-name]
    left: float, right: float, bottom: float, top: float, near: float, far: float
) -> None: ...
def glPolygonMode(face: int, mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glPolygonOffset(factor: float, units: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glPopAttrib() -> None: ...  # ruff: ignore[invalid-function-name]
def glPopMatrix() -> None: ...  # ruff: ignore[invalid-function-name]
def glPushAttrib(mask: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glPushMatrix() -> None: ...  # ruff: ignore[invalid-function-name]
def glReadPixels(  # ruff: ignore[invalid-function-name]
    x: int, y: int, width: int, height: int, format_: int, type_: int
) -> bytes | bytearray | memoryview: ...
def glRotatef(angle: float, x: float, y: float, z: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glShadeModel(mode: int) -> None: ...  # ruff: ignore[invalid-function-name]
def glTranslatef(x: float, y: float, z: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glVertex3f(x: float, y: float, z: float) -> None: ...  # ruff: ignore[invalid-function-name]
def glVertex3fv(values: object) -> None: ...  # ruff: ignore[invalid-function-name]
def glViewport(x: SupportsInt, y: SupportsInt, width: SupportsInt, height: SupportsInt) -> None: ...  # ruff: ignore[invalid-function-name]
