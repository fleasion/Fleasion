"""Simple 3D OBJ viewer widget using PyQt6 OpenGL with display list caching."""

import math
import time
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy, QMessageBox, QMenu
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QSurfaceFormat, QGuiApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
import numpy as np
from OpenGL.GL import *
from OpenGL.GLU import *


class ObjViewerWidget(QOpenGLWidget):
    """OpenGL widget for displaying OBJ files with optimized rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        # Prevent scrollbars from spawning by allowing the widget to compress gracefully
        # Slightly larger minimum so viewers are usable on small panels
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Mesh Data
        self.vertices = []
        self.colors = []
        self.faces = []
        self.normals = []
        self.face_normals = []

        # Camera state (orbit) - Default to a nice 3/4 isometric-style view
        self.camera_mode = 'orbit' # 'orbit' or 'fps'
        self.rotation_x = 20.0 
        self.rotation_y = -30.0
        self.zoom = -5.0
        
        # Camera state (fps)
        self.cam_pos = np.array([0.0, 0.0, 0.0], dtype=float)
        self.cam_yaw = 0.0
        self.cam_pitch = 0.0
        self.base_speed = 0.05  # Optimized for the normalized 1.0 unit model size
        
        self.auto_rotate = False
        self.last_pos = None
        self.keys_pressed = set()
        self.last_tick_time = time.time()

        # Display list for cached rendering
        self.mesh_display_list = 0
        self.needs_rebuild = False
        
        # Display options
        self.show_wireframe = False
        self.show_grid = True

        # Setup format
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)

        # Main update tick: use the monitor's refresh rate where possible
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_tick)
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            refresh = float(screen.refreshRate()) if screen is not None else 60.0
            if not refresh or refresh <= 0:
                refresh = 60.0
        except Exception:
            refresh = 60.0
        interval_ms = max(1, int(round(1000.0 / refresh)))
        self.timer.start(interval_ms)

    def get_refresh_interval_ms(self) -> int:
        """Return a safe refresh interval (ms) based on the current screen or primary screen."""
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            refresh = float(screen.refreshRate()) if screen is not None else 60.0
            if not refresh or refresh <= 0:
                refresh = 60.0
        except Exception:
            refresh = 60.0
        return max(1, int(round(1000.0 / refresh)))

    def load_obj_data(self, obj_content: str):
        """Load OBJ file content."""
        self.vertices = []
        self.colors = []
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
                # Vertex Colors support
                if len(parts) >= 7:
                    self.colors.append([float(parts[4]), float(parts[5]), float(parts[6])])
                else:
                    self.colors.append([1.0, 1.0, 1.0]) # Default white color fallback
            elif parts[0] == 'vn':
                self.normals.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif parts[0] == 'f':
                face_v = []
                face_n = []
                # Triangulated support
                for part in parts[1:]:
                    indices = part.split('/')
                    face_v.append(int(indices[0]) - 1)
                    if len(indices) >= 3 and indices[2]:
                        face_n.append(int(indices[2]) - 1)
                
                if len(face_v) >= 3:
                    self.faces.append({'v': face_v, 'n': face_n})

        if self.vertices:
            self._normalize_model()
            self._compute_face_normals()

        # Retain angles between meshes, but cleanly exit FPS mode & reset distance
        if self.camera_mode == 'fps':
            self.camera_mode = 'orbit'
            self.rotation_x = self.cam_pitch
            self.rotation_y = self.cam_yaw
        self.zoom = -5.0

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
        """Pre-compute face normals for performance with bounds checks."""
        self.face_normals = []
        if not self.vertices:
            return
        vertices = np.array(self.vertices)
        v_count = len(vertices)

        for face in self.faces:
            v_indices = face.get('v', [])
            if len(v_indices) >= 3:
                # ensure the first three indices are valid integers within range
                if any((not isinstance(idx, int)) or idx < 0 or idx >= v_count for idx in v_indices[:3]):
                    # invalid face. append a default normal and skip detailed compute.
                    self.face_normals.append([0.0, 1.0, 0.0])
                    continue

                v0 = vertices[v_indices[0]]
                v1 = vertices[v_indices[1]]
                v2 = vertices[v_indices[2]]

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
            glBegin(GL_TRIANGLES)

            for i, face in enumerate(self.faces):
                v_indices = face['v']
                n_indices = face['n']
                
                # Only explicitly rendering triangles to optimize glBegin calls
                if len(v_indices) < 3: 
                    continue

                # Fallback to face normals if vertex normals missing
                if not n_indices and i < len(self.face_normals):
                    glNormal3fv(self.face_normals[i])

                for j in range(3):
                    v_idx = v_indices[j]
                    if 0 <= v_idx < len(self.vertices):
                        if self.colors:
                            glColor3fv(self.colors[v_idx])
                        if n_indices and j < len(n_indices) and n_indices[j] < len(self.normals):
                            glNormal3fv(self.normals[n_indices[j]])
                        glVertex3fv(self.vertices[v_idx])

            glEnd()

        glEndList()
        self.needs_rebuild = False

    def initializeGL(self):
        """Initialize OpenGL with high quality lighting."""
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0) # Key Light
        glEnable(GL_LIGHT1) # Fill Light
        glEnable(GL_LIGHT2) # Rim Light
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_NORMALIZE)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glLightModeli(GL_LIGHT_MODEL_TWO_SIDE, GL_TRUE) # Better inside shading

        # 1. Main Key Light (Warm, strong)
        glLightfv(GL_LIGHT0, GL_POSITION, [1.0, 1.0, 1.0, 0.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.15, 0.15, 0.15, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.85, 0.8, 0.75, 1.0])  # Slightly warm
        glLightfv(GL_LIGHT0, GL_SPECULAR, [0.6, 0.6, 0.6, 1.0])

        # 2. Fill Light (Cool, weaker)
        glLightfv(GL_LIGHT1, GL_POSITION, [-1.0, 0.5, -1.0, 0.0])
        glLightfv(GL_LIGHT1, GL_DIFFUSE, [0.2, 0.25, 0.35, 1.0])  # Slightly cool
        glLightfv(GL_LIGHT1, GL_SPECULAR, [0.1, 0.1, 0.1, 1.0])

        # 3. Rim / Back Light (Bright, emphasizes edges)
        glLightfv(GL_LIGHT2, GL_POSITION, [0.0, 1.0, -2.0, 0.0])
        glLightfv(GL_LIGHT2, GL_DIFFUSE, [0.4, 0.4, 0.5, 1.0])
        glLightfv(GL_LIGHT2, GL_SPECULAR, [0.5, 0.5, 0.5, 1.0])
        
        # Specular material settings - increase shininess to make it look less chalky
        glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR, [0.6, 0.6, 0.6, 1.0])
        glMaterialf(GL_FRONT_AND_BACK, GL_SHININESS, 64.0)

        glClearColor(0.12, 0.12, 0.15, 1.0)
        glShadeModel(GL_SMOOTH)

    def resizeGL(self, w: int, h: int):
        """Handle standard clean projection resize."""
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
        
        self._draw_background()

        # Camera Transform setup
        if self.camera_mode == 'orbit':
            glTranslatef(0.0, 0.0, self.zoom)
            glRotatef(self.rotation_x, 1.0, 0.0, 0.0)
            glRotatef(self.rotation_y, 0.0, 1.0, 0.0)
        else:
            # FPS Look & Move transform
            glRotatef(self.cam_pitch, 1.0, 0.0, 0.0)
            glRotatef(self.cam_yaw, 0.0, 1.0, 0.0)
            glTranslatef(-self.cam_pos[0], -self.cam_pos[1], -self.cam_pos[2])

        if self.vertices and self.faces:
            if self.needs_rebuild:
                self._build_display_list()

            if self.mesh_display_list != 0:
                if self.show_wireframe:
                    glEnable(GL_POLYGON_OFFSET_FILL)
                    glPolygonOffset(1.0, 1.0)
                
                glCallList(self.mesh_display_list)
                
                if self.show_wireframe:
                    glDisable(GL_POLYGON_OFFSET_FILL)
                    glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
                    glDisable(GL_LIGHTING)
                    
                    # Prevent over-exposure by using blending + thin lines
                    glPushAttrib(GL_ENABLE_BIT | GL_COLOR_BUFFER_BIT | GL_LINE_BIT)
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                    # Note: we use glBlendColor if we wanted, but since list overrides 
                    # glColor3fv, we just lower the alpha of everything drawn.
                    # Actually standard OpenGL allows constant alpha blending:
                    glBlendColor(1.0, 1.0, 1.0, 0.75)
                    glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA)
                    
                    try:
                        glLineWidth(0.5)
                    except Exception:
                        pass # Some drivers don't support width < 1.0
                        
                    glCallList(self.mesh_display_list)
                    
                    glPopAttrib()
                    glEnable(GL_LIGHTING)
                    glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

        if self.show_grid:
            self._draw_grid()

        # Draw XYZ axis indicator
        self._draw_axis_indicator()

    def _draw_background(self):
        """Draw a subtle gradient background for better depth perception."""
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(-1, 1, -1, 1, -1, 1)

        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)

        glBegin(GL_QUADS)
        # Top color (darker)
        glColor3f(0.12, 0.12, 0.15)
        glVertex3f(-1.0, 1.0, 0.0)
        glVertex3f(1.0, 1.0, 0.0)
        # Bottom color (lighter/bluish)
        glColor3f(0.25, 0.25, 0.30)
        glVertex3f(1.0, -1.0, 0.0)
        glVertex3f(-1.0, -1.0, 0.0)
        glEnd()

        glDepthMask(GL_TRUE)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)

        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

    def _draw_grid(self):
        """Draw a subtle floor grid to provide spatial context."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        glColor4f(1.0, 1.0, 1.0, 0.08) # Very subtle white lines
        glLineWidth(1.0)
        
        grid_size = 2.0
        grid_step = 0.25
        
        # Draw on the XZ plane slightly below the model
        bottom_y = -1.0
        
        glBegin(GL_LINES)
        val = -grid_size
        while val <= grid_size + 0.001:
            # Lines parallel to Z
            if abs(val) < 0.01:
                glColor4f(1.0, 0.2, 0.2, 0.15) # X axis highlight
            else:
                glColor4f(1.0, 1.0, 1.0, 0.08)
            glVertex3f(val, bottom_y, -grid_size)
            glVertex3f(val, bottom_y, grid_size)
            
            # Lines parallel to X
            if abs(val) < 0.01:
                glColor4f(0.2, 0.4, 1.0, 0.15) # Z axis highlight
            else:
                glColor4f(1.0, 1.0, 1.0, 0.08)
            glVertex3f(-grid_size, bottom_y, val)
            glVertex3f(grid_size, bottom_y, val)
            
            val += grid_step
        glEnd()
        
        glPopAttrib()

    def _draw_axis_indicator(self):
        """Draw XYZ axis indicator in bottom left corner."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glPushMatrix()

        # FETCH the true physical viewport so High-DPI screens don't bug out
        current_viewport = glGetIntegerv(GL_VIEWPORT)

        indicator_size = 80
        margin = 10
        glViewport(margin, margin, indicator_size, indicator_size)

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(-2, 2, -2, 2, -10, 10)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Apply same rotation as main model logic
        if self.camera_mode == 'orbit':
            glRotatef(self.rotation_x, 1.0, 0.0, 0.0)
            glRotatef(self.rotation_y, 0.0, 1.0, 0.0)
        else:
            glRotatef(self.cam_pitch, 1.0, 0.0, 0.0)
            glRotatef(self.cam_yaw, 0.0, 1.0, 0.0)

        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glLineWidth(2.0)

        axis_length = 1.5

        # X axis (red)
        glColor3f(1.0, 0.2, 0.2)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(axis_length, 0, 0)
        glEnd()

        # Y axis (green)
        glColor3f(0.2, 1.0, 0.2)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(0, axis_length, 0)
        glEnd()

        # Z axis (blue)
        glColor3f(0.2, 0.4, 1.0)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(0, 0, axis_length)
        glEnd()

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()

        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glPopAttrib()
        
        # Safely restore the exact viewport OpenGL was using
        glViewport(current_viewport[0], current_viewport[1], current_viewport[2], current_viewport[3])
        
    # Physical scan codes for WASD and movement keys (layout-independent)
    # These correspond to physical key positions on a standard keyboard.
    _SCAN_W = 0x11
    _SCAN_A = 0x1E
    _SCAN_S = 0x1F
    _SCAN_D = 0x20
    _SCAN_Q = 0x10
    _SCAN_E = 0x12
    _SCAN_SPACE = 0x39
    _SCAN_LSHIFT = 0x2A
    _SCAN_WASD = {_SCAN_W, _SCAN_A, _SCAN_S, _SCAN_D}

    def _is_scan_pressed(self, scan_code):
        """Check if a physical key (by scan code) is currently held."""
        return scan_code in self.keys_pressed

    def keyPressEvent(self, event):
        """Handle FPS movement entry using physical scan codes for layout independence."""
        scan = event.nativeScanCode()

        if self.camera_mode == 'orbit':
            if scan in self._SCAN_WASD:
                self._transition_to_fps()
                # After transition, handle the key normally
            elif event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Shift}:
                # Ignore these keys while in orbit mode
                return
            # For all other keys (including non-FPS keys), continue to normal handling

        # Store the scan code for continuous movement polling
        self.keys_pressed.add(scan)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        scan = event.nativeScanCode()
        self.keys_pressed.discard(scan)
        super().keyReleaseEvent(event)
        
    def focusOutEvent(self, event):
        self.keys_pressed.clear()
        super().focusOutEvent(event)

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
            if self.camera_mode == 'orbit':
                self.rotation_x += dy * 0.5
                self.rotation_y += dx * 0.5
            else:
                self.cam_pitch += dy * 0.5
                self.cam_yaw += dx * 0.5
            self.update()

        self.last_pos = event.pos()

    def wheelEvent(self, event):
        """Handle mouse wheel."""
        delta = event.angleDelta().y()
        if self.camera_mode == 'orbit':
            self.zoom += delta / 120.0
            self.zoom = max(-50.0, min(-0.1, self.zoom))
        else:
            speed = self.base_speed * 5.0 * (delta / 120.0)
            yaw = math.radians(self.cam_yaw)
            pitch = math.radians(self.cam_pitch)
            
            # Mathematical inverse calculation from camera to world directional vectors
            forward = np.array([
                math.cos(pitch) * math.sin(yaw), 
                -math.sin(pitch), 
                -math.cos(pitch) * math.cos(yaw)
            ])
            self.cam_pos += forward * speed
            
        self.update()

    def set_auto_rotate(self, enabled: bool):
        """Enable/disable auto-rotation."""
        self.auto_rotate = enabled
        if enabled and self.camera_mode == 'fps':
            self.reset_view()

    def _transition_to_fps(self):
        """Seamlessly convert Orbit camera transformations into FPS transformations."""
        if self.camera_mode == 'fps': return
        self.camera_mode = 'fps'
        self.auto_rotate = False
        
        # Normalize angles to [-180, 180] so the transition doesn't cause a view flick
        pitch = self.rotation_x % 360.0
        if pitch > 180.0:
            pitch -= 360.0
        yaw = self.rotation_y % 360.0
        if yaw > 180.0:
            yaw -= 360.0

        self.cam_pitch = pitch
        self.cam_yaw = yaw

        rx = math.radians(pitch)
        ry = math.radians(yaw)

        # Calculates camera coordinate backwards cleanly utilizing inverted matrices 
        px = self.zoom * math.cos(rx) * math.sin(ry)
        py = -self.zoom * math.sin(rx)
        pz = -self.zoom * math.cos(rx) * math.cos(ry)

        self.cam_pos = np.array([px, py, pz], dtype=float)

    def _update_tick(self):
        """Update loop handles smooth movements independent of framerate UI stalls."""
        needs_update = False

        current_time = time.time()
        dt = current_time - self.last_tick_time
        self.last_tick_time = current_time
        if dt > 0.1: dt = 0.016

        if self.camera_mode == 'orbit':
            if self.auto_rotate:
                self.rotation_y += 62.5 * dt
                needs_update = True
                
        elif self.camera_mode == 'fps':
            speed = self.base_speed * (dt * 62.5)
            
            if self._is_scan_pressed(self._SCAN_E):
                speed *= 3.0
            if self._is_scan_pressed(self._SCAN_Q):
                speed *= 0.33

            moved = False
            yaw = math.radians(self.cam_yaw)
            pitch = math.radians(self.cam_pitch)

            # Properly transposed camera vectors allows Unity-style flycam controls
            forward = np.array([
                math.cos(pitch) * math.sin(yaw), 
                -math.sin(pitch), 
                -math.cos(pitch) * math.cos(yaw)
            ])
            right = np.array([
                math.cos(yaw), 
                0.0, 
                math.sin(yaw)
            ])
            up = np.array([0.0, 1.0, 0.0])  # Native World Up

            if self._is_scan_pressed(self._SCAN_W):
                self.cam_pos += forward * speed
                moved = True
            if self._is_scan_pressed(self._SCAN_S):
                self.cam_pos -= forward * speed
                moved = True
            if self._is_scan_pressed(self._SCAN_A):
                self.cam_pos -= right * speed
                moved = True
            if self._is_scan_pressed(self._SCAN_D):
                self.cam_pos += right * speed
                moved = True
            if self._is_scan_pressed(self._SCAN_SPACE):
                self.cam_pos += up * speed
                moved = True
            if self._is_scan_pressed(self._SCAN_LSHIFT):
                # Only fly down if no extension keys (Ctrl, Alt, Win) are held
                mods = QGuiApplication.keyboardModifiers()
                if not (mods & (Qt.KeyboardModifier.ControlModifier | 
                                Qt.KeyboardModifier.AltModifier | 
                                Qt.KeyboardModifier.MetaModifier)):
                    self.cam_pos -= up * speed
                    moved = True

            if moved:
                needs_update = True

        if needs_update:
            self.update()

    def reset_view(self):
        """Reset camera to default view."""
        self.camera_mode = 'orbit'
        self.rotation_x = 20.0
        self.rotation_y = -30.0
        self.zoom = -5.0
        self.update()

    def toggle_wireframe(self, enabled: bool):
        self.show_wireframe = enabled
        self.update()

    def toggle_grid(self, enabled: bool):
        self.show_grid = enabled
        self.update()

    def clear(self):
        """Clear the mesh data and display list."""
        self.vertices = []
        self.colors = []
        self.faces = []
        self.normals = []
        self.face_normals = []
        self.camera_mode = 'orbit'
        self.rotation_x = 20.0
        self.rotation_y = -30.0
        self.zoom = -5.0
        
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
    
    clear_requested = pyqtSignal()

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # 3D Viewer
        self.viewer = ObjViewerWidget()
        if self.config_manager:
            # Ensure settings exist and are saved if missing
            updated = False
            if 'obj_show_wireframe' not in self.config_manager.settings:
                self.config_manager.settings['obj_show_wireframe'] = False
                updated = True
            if 'obj_show_grid' not in self.config_manager.settings:
                self.config_manager.settings['obj_show_grid'] = True
                updated = True
            
            if updated:
                self.config_manager.save()

            self.viewer.show_wireframe = self.config_manager.settings.get('obj_show_wireframe', False)
            self.viewer.show_grid = self.config_manager.settings.get('obj_show_grid', True)
        layout.addWidget(self.viewer, stretch=1)

        # Controls
        controls_layout = QHBoxLayout()
        
        # Original default buttons
        reset_btn = QPushButton('Reset View')
        reset_btn.clicked.connect(self.viewer.reset_view)
        controls_layout.addWidget(reset_btn)

        self.options_btn = QPushButton('Options')
        self.options_menu = QMenu(self)

        self.action_auto_rotate = self.options_menu.addAction('Auto Rotate')
        self.action_auto_rotate.setCheckable(True)
        self.action_auto_rotate.toggled.connect(self.viewer.set_auto_rotate)

        self.action_wireframe = self.options_menu.addAction('Wireframe')
        self.action_wireframe.setCheckable(True)
        self.action_wireframe.setChecked(self.viewer.show_wireframe)
        self.action_wireframe.toggled.connect(self._toggle_wireframe_and_save)

        self.action_grid = self.options_menu.addAction('Grid')
        self.action_grid.setCheckable(True)
        self.action_grid.setChecked(self.viewer.show_grid)
        self.action_grid.toggled.connect(self._toggle_grid_and_save)

        self.options_btn.setMenu(self.options_menu)
        controls_layout.addWidget(self.options_btn)

        controls_layout.addStretch()

        # Vertex/Face counts
        self.stats_label = QLabel('')
        controls_layout.addWidget(self.stats_label)

        # Clean native Help Button AFTER the stats
        self.help_btn = QPushButton('?')
        self.help_btn.setMaximumWidth(30) # Keep it perfectly square/small but native height
        self.help_btn.setToolTip("View Camera Controls")
        self.help_btn.clicked.connect(self.show_help)
        controls_layout.addWidget(self.help_btn)

        layout.addLayout(controls_layout)
        self.setLayout(layout)

    def show_help(self):
        """Show a cleanly formatted message box with controls."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Camera Controls")
        msg.setText(
            "<h3>Orbit Mode (Default)</h3>"
            "<ul>"
            "<li><b>Click + Drag:</b> Rotate model</li>"
            "<li><b>Scroll Wheel:</b> Zoom in/out</li>"
            "</ul>"
            "<h3>FPS Mode</h3>"
            "<p><i>Pressing WASD at any time smoothly transitions you into FPS Mode.</i></p>"
            "<ul>"
            "<li><b>W/A/S/D:</b> Move forward/left/back/right</li>"
            "<li><b>Space / Shift:</b> Move Up / Down</li>"
            "<li><b>Click + Drag:</b> Look around freely</li>"
            "<li><b>Scroll Wheel:</b> Move in/out</li>"
            "<li><b>Hold Q / E:</b> Move slower / faster</li>"
            "</ul>"
        )
        msg.exec()

    def _toggle_wireframe_and_save(self, enabled: bool):
        self.viewer.toggle_wireframe(enabled)
        if self.config_manager:
            self.config_manager.settings['obj_show_wireframe'] = enabled
            self.config_manager.save()

    def _toggle_grid_and_save(self, enabled: bool):
        self.viewer.toggle_grid(enabled)
        if self.config_manager:
            self.config_manager.settings['obj_show_grid'] = enabled
            self.config_manager.save()

    def load_obj(self, obj_content: str, asset_id: str = ''):
        """Load OBJ file content."""
        self.viewer.load_obj_data(obj_content)

        v_count = len(self.viewer.vertices)
        f_count = len(self.viewer.faces)
        self.stats_label.setText(f'{v_count:,} verts, {f_count:,} faces')

    def clear(self):
        """Clear the viewer."""
        self.viewer.clear()
        self.stats_label.setText('')