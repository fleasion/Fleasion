"""Simple 3D OBJ viewer widget using PyQt6 OpenGL with display list caching."""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
import numpy as np
from OpenGL.GL import *
from OpenGL.GLU import *


class ObjViewerWidget(QOpenGLWidget):
    """OpenGL widget for displaying OBJ files with optimized rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vertices = []
        self.faces = []
        self.normals = []
        self.face_normals = []  # Pre-computed face normals

        self.rotation_x = 0
        self.rotation_y = 0
        self.zoom = -5.0
        self.auto_rotate = False

        self.last_pos = None

        # Display list for cached rendering
        self.mesh_display_list = 0
        self.needs_rebuild = False

        # Setup format
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)

        # Auto-rotate timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._auto_rotate)

    def load_obj_data(self, obj_content: str):
        """Load OBJ file content."""
        self.vertices = []
        self.faces = []
        self.normals = []
        self.face_normals = []

        for line in obj_content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split()
            if not parts:
                continue

            if parts[0] == 'v':
                self.vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif parts[0] == 'vn':
                self.normals.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif parts[0] == 'f':
                face = []
                for part in parts[1:4]:
                    indices = part.split('/')
                    v_idx = int(indices[0]) - 1
                    face.append(v_idx)
                if len(face) == 3:
                    self.faces.append(face)

        if self.vertices:
            self._normalize_model()
            self._compute_face_normals()

        # Mark for display list rebuild
        self.needs_rebuild = True
        self.update()

    def _normalize_model(self):
        """Center and normalize model to fit in view."""
        if not self.vertices:
            return

        vertices = np.array(self.vertices)
        center = vertices.mean(axis=0)
        vertices -= center

        max_dim = np.abs(vertices).max()
        if max_dim > 0:
            vertices /= max_dim

        self.vertices = vertices.tolist()

    def _compute_face_normals(self):
        """Pre-compute face normals for performance."""
        self.face_normals = []
        vertices = np.array(self.vertices)

        for face in self.faces:
            if len(face) >= 3:
                v0 = vertices[face[0]]
                v1 = vertices[face[1]]
                v2 = vertices[face[2]]

                edge1 = v1 - v0
                edge2 = v2 - v0
                normal = np.cross(edge1, edge2)
                norm = np.linalg.norm(normal)
                if norm > 0:
                    normal = normal / norm
                else:
                    normal = np.array([0.0, 1.0, 0.0])

                self.face_normals.append(normal.tolist())
            else:
                self.face_normals.append([0.0, 1.0, 0.0])

    def _build_display_list(self):
        """Build display list for fast rendering."""
        if self.mesh_display_list != 0:
            glDeleteLists(self.mesh_display_list, 1)

        self.mesh_display_list = glGenLists(1)
        glNewList(self.mesh_display_list, GL_COMPILE)

        if self.vertices and self.faces:
            # Draw filled mesh
            glColor3f(0.7, 0.7, 0.9)
            glBegin(GL_TRIANGLES)

            for i, face in enumerate(self.faces):
                if len(face) >= 3 and i < len(self.face_normals):
                    glNormal3fv(self.face_normals[i])
                    for idx in face[:3]:
                        if 0 <= idx < len(self.vertices):
                            glVertex3fv(self.vertices[idx])

            glEnd()

        glEndList()
        self.needs_rebuild = False

    def initializeGL(self):
        """Initialize OpenGL."""
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_LIGHT1)
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_NORMALIZE)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)

        # Main light
        glLightfv(GL_LIGHT0, GL_POSITION, [1.0, 1.0, 1.0, 0.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.3, 0.3, 0.3, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.7, 0.7, 0.7, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [0.2, 0.2, 0.2, 1.0])

        # Fill light from below
        glLightfv(GL_LIGHT1, GL_POSITION, [-1.0, -0.5, -1.0, 0.0])
        glLightfv(GL_LIGHT1, GL_DIFFUSE, [0.2, 0.2, 0.2, 1.0])

        glClearColor(0.15, 0.15, 0.18, 1.0)
        glShadeModel(GL_SMOOTH)

    def resizeGL(self, w: int, h: int):
        """Handle resize."""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = w / h if h > 0 else 1.0
        gluPerspective(45.0, aspect, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)

    def paintGL(self):
        """Render the scene using cached display list."""
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        # Camera
        glTranslatef(0.0, 0.0, self.zoom)
        glRotatef(self.rotation_x, 1.0, 0.0, 0.0)
        glRotatef(self.rotation_y, 0.0, 1.0, 0.0)

        if not self.vertices or not self.faces:
            return

        # Rebuild display list if needed
        if self.needs_rebuild:
            self._build_display_list()

        # Draw cached mesh
        if self.mesh_display_list != 0:
            glCallList(self.mesh_display_list)

    def mousePressEvent(self, event):
        """Handle mouse press."""
        self.last_pos = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse drag."""
        if self.last_pos is None:
            return

        dx = event.pos().x() - self.last_pos.x()
        dy = event.pos().y() - self.last_pos.y()

        if event.buttons() & Qt.MouseButton.LeftButton:
            self.rotation_x += dy * 0.5
            self.rotation_y += dx * 0.5
            self.update()

        self.last_pos = event.pos()

    def wheelEvent(self, event):
        """Handle mouse wheel for zoom."""
        delta = event.angleDelta().y()
        self.zoom += delta / 120.0
        self.zoom = max(-20.0, min(-1.0, self.zoom))
        self.update()

    def set_auto_rotate(self, enabled: bool):
        """Enable/disable auto-rotation."""
        self.auto_rotate = enabled
        if enabled:
            self.timer.start(33)  # ~30 FPS
        else:
            self.timer.stop()

    def _auto_rotate(self):
        """Auto-rotate the model."""
        if self.auto_rotate:
            self.rotation_y += 1.0
            self.update()

    def reset_view(self):
        """Reset camera to default view."""
        self.rotation_x = 0
        self.rotation_y = 0
        self.zoom = -5.0
        self.update()

    def clear(self):
        """Clear the mesh data and display list."""
        self.vertices = []
        self.faces = []
        self.normals = []
        self.face_normals = []
        if self.mesh_display_list != 0:
            try:
                glDeleteLists(self.mesh_display_list, 1)
            except Exception:
                pass
            self.mesh_display_list = 0
        self.needs_rebuild = False
        self.update()


class ObjViewerPanel(QWidget):
    """Panel with 3D viewer and controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        self.info_label = QLabel('No mesh loaded')
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info_label)

        # 3D Viewer
        self.viewer = ObjViewerWidget()
        layout.addWidget(self.viewer, stretch=1)

        # Controls
        controls_layout = QHBoxLayout()

        reset_btn = QPushButton('Reset View')
        reset_btn.clicked.connect(self.viewer.reset_view)
        controls_layout.addWidget(reset_btn)

        self.rotate_btn = QPushButton('Auto Rotate')
        self.rotate_btn.setCheckable(True)
        self.rotate_btn.toggled.connect(self.viewer.set_auto_rotate)
        controls_layout.addWidget(self.rotate_btn)

        controls_layout.addStretch()

        self.stats_label = QLabel('')
        controls_layout.addWidget(self.stats_label)

        layout.addLayout(controls_layout)

        self.setLayout(layout)

    def load_obj(self, obj_content: str, asset_id: str = ''):
        """Load OBJ file content."""
        self.viewer.load_obj_data(obj_content)

        vertex_count = len(self.viewer.vertices)
        face_count = len(self.viewer.faces)

        self.info_label.setText(f'Asset ID: {asset_id}')
        self.stats_label.setText(f'{vertex_count:,} vertices, {face_count:,} faces')

    def clear(self):
        """Clear the viewer."""
        self.viewer.clear()
        self.rotate_btn.setChecked(False)
        self.info_label.setText('No mesh loaded')
        self.stats_label.setText('')
