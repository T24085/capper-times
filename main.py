#!/usr/bin/env python3
# main.py
# Simple overlay countdown timer with optional LAN sync (UDP broadcast)
# Windows-oriented (PyQt6 + keyboard + pywin32 for click-through)
#
# Usage:
#   python main.py        # runs with network sync enabled
#   python main.py --no-network  # run local-only
#
# Requirements (pip):
#   pip install PyQt6 keyboard pywin32

import sys
import time
import json
import socket
import uuid
import threading
import argparse
import os
from typing import Optional

# Suppress Qt warnings/errors to console
os.environ['QT_LOGGING_RULES'] = '*.debug=false'

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import pyqtSignal

try:
    import keyboard
except Exception as e:
    print("Missing dependency 'keyboard'. Install with: pip install keyboard")
    raise

try:
    import websockets
    import asyncio
except ImportError:
    websockets = None
    asyncio = None

# For click-through window on Windows:
try:
    import win32con
    import win32gui
    import win32api
except Exception:
    win32gui = None
    win32con = None
    win32api = None

# Config
HOTKEY_1 = "v"             # key to press for capper 1
HOTKEY_2 = "b"             # key to press for capper 2
UDP_PORT = 54545           # port for LAN sync
TIMER_OPTIONS_1 = [35, 25, 20]  # cycle order as requested
TIMER_OPTIONS_2 = [35, 25, 20]
CAP_COLORS = ["#00FF00", "#7A3DF0"]
BOARD_ASSETS = ["Generator", "Turret 1", "Turret 2", "Radar / Sensor"]
BOARD_STATE_COLORS = ["#00FF00", "#FFCC00", "#FF0000"]
DEFAULT_ROLE = "Capper 1"
LOCKED_ROLES = ["Capper 1", "Capper 2"]
WINDOW_WIDTH = 940
WINDOW_HEIGHT = 200
BOARD_WIDTH = 170
TIMER_WIDTH = 600
DEFAULT_SERVER_URL = os.environ.get(
    "CAPTIMER_SERVER",
    "wss://web-production-03594.up.railway.app",
)
_APPDATA_ROOT = os.environ.get("APPDATA") or os.path.expanduser("~")
PRESET_DIR = os.path.join(_APPDATA_ROOT, "CapperTimer")
PRESET_FILE = os.path.join(PRESET_DIR, "capper-presets.json")
MAP_PRESETS = [
    "Custom",
    "DX",
    "Hollow",
    "Raindance",
    "Wavemist",
    "Torment",
    "Katabatic",
    "Dry Dock",
]

MY_ID = str(uuid.uuid4())


class WebSocketClient:
    """WebSocket client for connecting to remote server"""
    def __init__(self, server_url, app_instance):
        self.server_url = server_url
        self.app = app_instance
        self.websocket = None
        self.running = False
        self.loop = None
        
    async def _connect(self):
        """Connect to WebSocket server"""
        try:
            # Configure WebSocket with ping/pong keepalive for Railway
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=20,  # Send ping every 20 seconds
                ping_timeout=10,   # Wait 10 seconds for pong
                close_timeout=10   # Wait 10 seconds when closing
            )
            self.running = True
            print(f"Connected to server: {self.server_url}")
            QtCore.QTimer.singleShot(0, lambda: self.app.update_status("WebSocket: connected"))
            QtCore.QTimer.singleShot(0, self.app.on_ws_connected)
            # Start listening
            asyncio.create_task(self._listen())
            return True
        except Exception as e:
            print(f"Failed to connect to server: {e}")
            QtCore.QTimer.singleShot(0, lambda: self.app.update_status("WebSocket: failed"))
            return False
    
    async def _listen(self):
        """Listen for messages from server"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    cmd = data.get("cmd")
                    if cmd == "start" and "seconds" in data:
                        # Ignore our own messages
                        if data.get("sender") == MY_ID:
                            continue
                        capper = int(data.get("capper", 1))
                        index = capper - 1
                        if index not in (0, 1):
                            continue
                        sec = float(data["seconds"])
                        print(f"Received timer start from remote (capper {capper}): {sec}s")
                        # Update timer in Qt thread using signal (thread-safe)
                        # Capture sec in lambda to avoid closure issues
                        self.app.window.start_timer_signal.emit(index, float(sec))
                    elif cmd == "board_update":
                        if data.get("sender") == MY_ID:
                            continue
                        board = data.get("board")
                        index = int(data.get("index", -1))
                        state = int(data.get("state", -1))
                        if board not in ("defense", "offense"):
                            continue
                        if index < 0 or index >= len(BOARD_ASSETS):
                            continue
                        if state not in (0, 1, 2):
                            continue
                        self.app.window.board_update_signal.emit(board, index, state)
                    elif cmd == "role_status":
                        roles = data.get("roles", {})
                        if isinstance(roles, dict):
                            self.app.handle_role_status(roles)
                    elif cmd == "role_result":
                        role = data.get("role")
                        ok = bool(data.get("ok"))
                        self.app.handle_role_result(role, ok)
                except Exception as e:
                    print(f"Error processing WebSocket message: {e}")
                    continue
        except websockets.exceptions.ConnectionClosed:
            self.running = False
            print("Disconnected from server")
            QtCore.QTimer.singleShot(0, lambda: self.app.update_status("WebSocket: disconnected"))
        except Exception as e:
            print(f"WebSocket error: {e}")
            self.running = False
            QtCore.QTimer.singleShot(0, lambda: self.app.update_status("WebSocket: error"))
    
    async def send_timer(self, seconds, sender_id, capper):
        """Send timer start to server"""
        if self.websocket and self.running:
            try:
                msg = json.dumps(
                    {"cmd": "start", "seconds": seconds, "sender": sender_id, "capper": capper}
                )
                await self.websocket.send(msg)
            except Exception as e:
                print(f"Failed to send: {e}")

    async def send_board_update(self, board, index, state, sender_id):
        """Send board state update to server"""
        if self.websocket and self.running:
            try:
                msg = json.dumps(
                    {
                        "cmd": "board_update",
                        "board": board,
                        "index": index,
                        "state": state,
                        "sender": sender_id,
                    }
                )
                await self.websocket.send(msg)
            except Exception as e:
                print(f"Failed to send: {e}")

    async def send_role_claim(self, role, sender_id):
        if self.websocket and self.running:
            try:
                msg = json.dumps({"cmd": "role_claim", "role": role, "sender": sender_id})
                await self.websocket.send(msg)
            except Exception as e:
                print(f"Failed to send: {e}")

    async def send_role_release(self, role, sender_id):
        if self.websocket and self.running:
            try:
                msg = json.dumps({"cmd": "role_release", "role": role, "sender": sender_id})
                await self.websocket.send(msg)
            except Exception as e:
                print(f"Failed to send: {e}")
    
    def close(self):
        """Close connection"""
        self.running = False
        if self.websocket and self.loop:
            asyncio.run_coroutine_threadsafe(self.websocket.close(), self.loop)


class OverlayLabel(QtWidgets.QWidget):
    def __init__(self, lines=2, parent=None):
        super().__init__(parent)
        self._texts = [""] * lines
        self._colors = CAP_COLORS[:lines]
        self._font = QtGui.QFont("Segoe UI", 48, QtGui.QFont.Weight.Bold)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_text(self, index: int, text: str, color: Optional[str] = None):
        if 0 <= index < len(self._texts):
            if color is not None:
                self._colors[index] = color
            self._texts[index] = text
            self.update()

    def texts(self):
        return list(self._texts)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        # Clear previous frame fully on a translucent surface
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QtCore.Qt.GlobalColor.transparent)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setFont(self._font)

        line_count = max(len(self._texts), 1)
        col_width = int(self.rect().width() / line_count)
        for i, text in enumerate(self._texts):
            rect = QtCore.QRect(i * col_width, 0, col_width, self.rect().height())
            painter.setPen(QtGui.QColor(self._colors[i]))
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class BoardWidget(QtWidgets.QWidget):
    def __init__(self, title, assets, width, strike_destroyed=False, parent=None):
        super().__init__(parent)
        self._title = title
        self._assets = list(assets)
        self._states = [0] * len(self._assets)
        self._selected = 0
        self._strike_destroyed = strike_destroyed
        self._title_font = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Weight.Bold)
        self._font = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold)
        self.setMinimumSize(width, WINDOW_HEIGHT)
        self.setMaximumSize(width, WINDOW_HEIGHT)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_states(self, states):
        if len(states) != len(self._assets):
            return
        self._states = [max(0, min(2, int(s))) for s in states]
        self.update()

    def set_state(self, index, state):
        if 0 <= index < len(self._assets):
            self._states[index] = max(0, min(2, int(state)))
            self.update()

    def set_selected(self, index):
        if 0 <= index < len(self._assets):
            self._selected = index
            self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QtCore.Qt.GlobalColor.transparent)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)

        title_height = 18
        painter.setFont(self._title_font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(
            QtCore.QRect(0, 0, self.rect().width(), title_height),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self._title,
        )

        row_height = int((self.rect().height() - title_height) / max(len(self._assets), 1))
        painter.setFont(self._font)
        for i, asset in enumerate(self._assets):
            y = title_height + i * row_height
            rect = QtCore.QRect(4, y, self.rect().width() - 8, row_height)
            if i == self._selected:
                painter.fillRect(rect, QtGui.QColor(255, 255, 255, 30))
            color = BOARD_STATE_COLORS[self._states[i]]
            painter.setPen(QtGui.QColor(color))
            painter.drawText(
                rect,
                QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
                asset,
            )
            if self._strike_destroyed and self._states[i] == 2:
                mid_y = rect.center().y()
                painter.setPen(QtGui.QColor(color))
                painter.drawLine(rect.left(), mid_y, rect.right(), mid_y)
        painter.end()


class OverlayWindow(QtWidgets.QMainWindow):
    # Signal to start timer from any thread
    start_timer_signal = QtCore.pyqtSignal(int, float)
    board_update_signal = QtCore.pyqtSignal(str, int, int)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cap Timer Overlay")
        # Use simpler window flags first to ensure visibility
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        # Ensure transparent window background (required in PyQt6)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background-color: transparent;")
        
        # Connect signal to start method
        self.start_timer_signal.connect(self.start_timer)
        self.board_update_signal.connect(self._apply_board_update)

        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.defense_board = BoardWidget(
            "Defense", BOARD_ASSETS, BOARD_WIDTH, strike_destroyed=False, parent=container
        )
        self.offense_board = BoardWidget(
            "Offense", BOARD_ASSETS, BOARD_WIDTH, strike_destroyed=True, parent=container
        )

        # central display widget
        self.label = OverlayLabel(lines=2, parent=container)
        self.label.setMinimumSize(TIMER_WIDTH, WINDOW_HEIGHT)
        self.label.setMaximumSize(TIMER_WIDTH, WINDOW_HEIGHT)
        self.label.resize(TIMER_WIDTH, WINDOW_HEIGHT)

        layout.addWidget(self.defense_board)
        layout.addWidget(self.label)
        layout.addWidget(self.offense_board)

        self.setCentralWidget(container)
        
        # Ensure window geometry is correct
        self.setGeometry(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        # Set initial text
        self._ready_texts = ["READY", "READY"]
        self.label.set_text(0, self._ready_texts[0])
        self.label.set_text(1, self._ready_texts[1])
        self.label.show()

        # background opacity widget to improve visibility
        self.bg = None
        self._apply_click_through_later()

        # timer
        self._remaining = [0.0, 0.0]
        self._qtimer = QtCore.QTimer()
        self._qtimer.setInterval(50)  # 20 Hz
        self._qtimer.timeout.connect(self._tick)
        
        # Flash timer for red warning
        self._flash_timer = [QtCore.QTimer(), QtCore.QTimer()]
        self._flash_state = [False, False]
        for i, timer in enumerate(self._flash_timer):
            timer.setInterval(250)  # Flash every 250ms
            timer.timeout.connect(lambda idx=i: self._flash_tick(idx))

    def _set_label_text(self, index: int, text: str, color: Optional[str] = None):
        self.label.set_text(index, text, color=color)

    def _apply_board_update(self, board: str, index: int, state: int):
        self.update_board_state(board, index, state)

    def set_board_visible(self, board: str, visible: bool):
        if board == "defense":
            self.defense_board.setVisible(visible)
        elif board == "offense":
            self.offense_board.setVisible(visible)

    def set_board_selected(self, board: str, index: int):
        if board == "defense":
            self.defense_board.set_selected(index)
        elif board == "offense":
            self.offense_board.set_selected(index)

    def update_board_state(self, board: str, index: int, state: int):
        if board == "defense":
            self.defense_board.set_state(index, state)
        elif board == "offense":
            self.offense_board.set_state(index, state)

    def set_board_states(self, board: str, states):
        if board == "defense":
            self.defense_board.set_states(states)
        elif board == "offense":
            self.offense_board.set_states(states)

    def _apply_click_through_later(self):
        # Apply click-through after window is created on Windows
        if sys.platform.startswith("win") and win32gui:
            QtCore.QTimer.singleShot(200, self._make_click_through)

    def _make_click_through(self):
        """Make window click-through after ensuring it's rendered"""
        try:
            # Ensure window is shown first
            if not self.isVisible():
                self.show()
            
            # Don't use WA_TranslucentBackground - it conflicts with SetLayeredWindowAttributes
            # Instead, use Windows API directly for transparency
            
            # Wait a bit for window to be fully rendered
            QtCore.QTimer.singleShot(200, lambda: self._setup_layered_window())
        except Exception as e:
            print(f"Warning: Could not setup click-through: {e}")
    
    def _setup_layered_window(self):
        """Setup layered window attributes after window is rendered"""
        try:
            hwnd = int(self.winId())
            if hwnd == 0:
                # Window not ready yet, try again
                QtCore.QTimer.singleShot(100, lambda: self._setup_layered_window())
                return
            
            # Enable translucent background FIRST (before Windows API calls)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
            
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            
            # Enable layered window style
            ex_style |= win32con.WS_EX_LAYERED
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
            
            # Set window to be fully opaque (255 = fully opaque)
            # Don't call SetLayeredWindowAttributes if WA_TranslucentBackground is set
            # This avoids UpdateLayeredWindowIndirect errors
            
            # Wait a bit to ensure rendering is complete, then make click-through
            QtCore.QTimer.singleShot(500, lambda: self._enable_click_through(hwnd))
        except Exception as e:
            print(f"Warning: Could not setup layered window: {e}")
    
    def _enable_click_through(self, hwnd):
        """Enable click-through after window is fully rendered"""
        try:
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ex_style |= win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
            print("Click-through enabled - window is now transparent to mouse clicks")
        except Exception as e:
            print(f"Warning: Could not enable click-through: {e}")

    def start_timer(self, index: int, seconds: float):
        if not 0 <= index < len(self._remaining):
            return
        print(f"start_timer({index}) called with {seconds} seconds")
        # Stop any existing flash for this timer
        self._flash_timer[index].stop()
        self._flash_state[index] = False

        self._remaining[index] = float(seconds)
        # Ensure window is shown and visible first
        self.show()
        self.raise_()
        self.activateWindow()
        # Force window to front
        self.setWindowState(
            self.windowState()
            & ~QtCore.Qt.WindowState.WindowMinimized
            | QtCore.Qt.WindowState.WindowActive
        )
        self.raise_()
        # Clear "READY" text immediately and force update
        self.label.set_text(index, "")
        QtWidgets.QApplication.processEvents()  # Force clear to happen
        print(f"Cleared READY, updating label with remaining={self._remaining[index]}")
        # Update label with timer value
        self._update_label(index)
        QtWidgets.QApplication.processEvents()  # Force timer display
        print(f"Starting timer, label texts={self.label.texts()}")
        if not self._qtimer.isActive():
            self._qtimer.start()
        print(f"Timer started successfully with {self._remaining[index]}s remaining")

    def stop(self, index: int):
        if not 0 <= index < len(self._remaining):
            return
        self._flash_timer[index].stop()
        self._flash_state[index] = False
        self._remaining[index] = 0.0
        self._set_label_text(index, "", color=CAP_COLORS[index])
        if all(rem <= 0 for rem in self._remaining):
            self._qtimer.stop()

    def _tick(self):
        any_active = False
        for i, remaining in enumerate(self._remaining):
            if remaining <= 0:
                continue
            any_active = True
            self._remaining[i] -= 0.05
            if self._remaining[i] <= 0:
                self.stop(i)
                continue
            self._update_label(i)
        if not any_active:
            self._qtimer.stop()
    
    def _flash_tick(self, index: int):
        """Flash the label when <= 10 seconds"""
        if self._remaining[index] <= 10 and self._remaining[index] > 0:
            self._flash_state[index] = not self._flash_state[index]
            sec = int(self._remaining[index] + 0.999)
            text = f"{sec:02d}s"
            
            # Alternate between bright red and dimmed red
            color = "#FF0000" if self._flash_state[index] else "#CC0000"
            self._set_label_text(index, text, color=color)

    def _update_label(self, index: int):
        sec = int(self._remaining[index] + 0.999)  # ceil-ish display
        text = f"{sec:02d}s"

        # Determine color
        if self._remaining[index] <= 10:
            if not self._flash_timer[index].isActive():
                self._flash_timer[index].start()
            # Color handled by flash timer; update text only.
            self._set_label_text(index, text)
        else:
            if self._flash_timer[index].isActive():
                self._flash_timer[index].stop()
            self._flash_state[index] = False
            self._set_label_text(index, text, color=CAP_COLORS[index])


class SettingsWindow(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("Cap Timer Settings")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(360, 520)

        layout = QtWidgets.QVBoxLayout()

        form = QtWidgets.QFormLayout()

        self.times_input_1 = QtWidgets.QLineEdit()
        self.times_input_1.setPlaceholderText("Capper 1 times (e.g., 35,25,20)")
        self.hotkey_input_1 = QtWidgets.QLineEdit()
        self.hotkey_input_1.setPlaceholderText("Capper 1 hotkey (e.g., v)")

        self.times_input_2 = QtWidgets.QLineEdit()
        self.times_input_2.setPlaceholderText("Capper 2 times (e.g., 35,25,20)")
        self.hotkey_input_2 = QtWidgets.QLineEdit()
        self.hotkey_input_2.setPlaceholderText("Capper 2 hotkey (e.g., b)")

        form.addRow("Capper 1 times", self.times_input_1)
        form.addRow("Capper 1 hotkey", self.hotkey_input_1)
        form.addRow("Capper 2 times", self.times_input_2)
        form.addRow("Capper 2 hotkey", self.hotkey_input_2)

        self.monitor_select = QtWidgets.QComboBox()
        self._refresh_monitors()
        form.addRow("Display monitor", self.monitor_select)

        self.map_select = QtWidgets.QComboBox()
        for name in MAP_PRESETS:
            self.map_select.addItem(name)
        form.addRow("Map preset", self.map_select)

        preset_row = QtWidgets.QHBoxLayout()
        self.load_preset_btn = QtWidgets.QPushButton("Load")
        self.save_preset_btn = QtWidgets.QPushButton("Save")
        self.load_preset_btn.clicked.connect(self._on_load_preset)
        self.save_preset_btn.clicked.connect(self._on_save_preset)
        preset_row.addWidget(self.load_preset_btn)
        preset_row.addWidget(self.save_preset_btn)
        form.addRow("Preset actions", preset_row)

        role_group = QtWidgets.QGroupBox("Role")
        role_layout = QtWidgets.QHBoxLayout()
        self.role_buttons = {}
        self.role_labels = {}
        for role in ["Capper 1", "Capper 2", "Offense", "Defense"]:
            btn = QtWidgets.QRadioButton(role)
            self.role_buttons[role] = btn
            self.role_labels[role] = role
            role_layout.addWidget(btn)
        self.role_buttons[DEFAULT_ROLE].setChecked(True)
        role_group.setLayout(role_layout)

        view_group = QtWidgets.QGroupBox("Board visibility")
        view_layout = QtWidgets.QVBoxLayout()
        self.view_defense = QtWidgets.QCheckBox("Show Defense Board")
        self.view_offense = QtWidgets.QCheckBox("Show Offense Board")
        self.view_defense.setChecked(True)
        self.view_offense.setChecked(True)
        view_layout.addWidget(self.view_defense)
        view_layout.addWidget(self.view_offense)
        view_group.setLayout(view_layout)

        apply_btn = QtWidgets.QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)

        exit_btn = QtWidgets.QPushButton("Exit")
        exit_btn.clicked.connect(QtWidgets.QApplication.quit)

        self.status_label = QtWidgets.QLabel("WebSocket: idle")

        layout.addLayout(form)
        layout.addWidget(role_group)
        layout.addWidget(view_group)
        layout.addWidget(apply_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(exit_btn)

        self.setLayout(layout)

    def _refresh_monitors(self):
        self.monitor_select.clear()
        screens = QtWidgets.QApplication.screens()
        for i, screen in enumerate(screens):
            name = screen.name() or f"Monitor {i + 1}"
            geom = screen.availableGeometry()
            label = f"{i + 1}: {name} ({geom.width()}x{geom.height()})"
            self.monitor_select.addItem(label, i)

    def _current_role(self):
        for role, btn in self.role_buttons.items():
            if btn.isChecked():
                return role
        return DEFAULT_ROLE

    def set_role(self, role):
        if role in self.role_buttons:
            self.role_buttons[role].setChecked(True)

    def update_role_availability(self, role_owners, my_id):
        for role in LOCKED_ROLES:
            owner = role_owners.get(role)
            btn = self.role_buttons.get(role)
            if not btn:
                continue
            available = owner is None or owner == my_id
            btn.setEnabled(available)
            if available:
                btn.setText(self.role_labels[role])
            else:
                btn.setText(f"{self.role_labels[role]} (taken)")

    def prompt_role(self, current_role):
        roles = ["Capper 1", "Capper 2", "Offense", "Defense"]
        if current_role in roles:
            current_index = roles.index(current_role)
        else:
            current_index = 0
        choice, ok = QtWidgets.QInputDialog.getItem(
            self,
            "Select Role",
            "Choose your role:",
            roles,
            current_index,
            False,
        )
        if ok and choice:
            return choice
        return None

    def load_current(
        self,
        times_1,
        hotkey_1,
        times_2,
        hotkey_2,
        monitor_index,
        map_name=None,
        role=None,
        show_defense=True,
        show_offense=True,
    ):
        self._refresh_monitors()
        self.times_input_1.setText(",".join(str(t) for t in times_1))
        self.hotkey_input_1.setText(hotkey_1)
        self.times_input_2.setText(",".join(str(t) for t in times_2))
        self.hotkey_input_2.setText(hotkey_2)
        if 0 <= monitor_index < self.monitor_select.count():
            self.monitor_select.setCurrentIndex(monitor_index)
        if map_name and map_name in MAP_PRESETS:
            self.map_select.setCurrentText(map_name)
        if role in self.role_buttons:
            self.role_buttons[role].setChecked(True)
        self.view_defense.setChecked(bool(show_defense))
        self.view_offense.setChecked(bool(show_offense))

    def set_status(self, text: str):
        self.status_label.setText(text)

    def _on_apply(self):
        times_text_1 = self.times_input_1.text().strip()
        hotkey_text_1 = self.hotkey_input_1.text().strip().lower()
        times_text_2 = self.times_input_2.text().strip()
        hotkey_text_2 = self.hotkey_input_2.text().strip().lower()
        monitor_index = int(self.monitor_select.currentData())
        self.app.update_settings(
            times_text_1,
            hotkey_text_1,
            times_text_2,
            hotkey_text_2,
            monitor_index,
            self.map_select.currentText(),
            self._current_role(),
            self.view_defense.isChecked(),
            self.view_offense.isChecked(),
        )

    def _load_presets(self):
        try:
            if os.path.exists(PRESET_FILE):
                with open(PRESET_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"Failed to load presets: {e}")
        return {}

    def load_last_preset(self):
        presets = self._load_presets()
        last_map = presets.get("_last_map")
        if last_map in MAP_PRESETS:
            self.map_select.setCurrentText(last_map)
            preset = presets.get(last_map)
            if isinstance(preset, dict):
                self.times_input_1.setText(preset.get("times_1", ""))
                self.hotkey_input_1.setText(preset.get("hotkey_1", HOTKEY_1))
                self.times_input_2.setText(preset.get("times_2", ""))
                self.hotkey_input_2.setText(preset.get("hotkey_2", HOTKEY_2))
                monitor_index = int(preset.get("monitor_index", 0))
                if 0 <= monitor_index < self.monitor_select.count():
                    self.monitor_select.setCurrentIndex(monitor_index)
            self._on_apply()
        role = presets.get("_last_role")
        if role in self.role_buttons:
            self.role_buttons[role].setChecked(True)
            self._on_apply()

    def _save_presets(self, data):
        try:
            os.makedirs(PRESET_DIR, exist_ok=True)
            with open(PRESET_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"Failed to save presets: {e}")

    def _save_last_role(self, role):
        presets = self._load_presets()
        presets["_last_role"] = role
        self._save_presets(presets)

    def _on_load_preset(self):
        map_name = self.map_select.currentText()
        presets = self._load_presets()
        preset = presets.get(map_name)
        if not isinstance(preset, dict):
            return
        self.times_input_1.setText(preset.get("times_1", ""))
        self.hotkey_input_1.setText(preset.get("hotkey_1", HOTKEY_1))
        self.times_input_2.setText(preset.get("times_2", ""))
        self.hotkey_input_2.setText(preset.get("hotkey_2", HOTKEY_2))
        monitor_index = int(preset.get("monitor_index", 0))
        if 0 <= monitor_index < self.monitor_select.count():
            self.monitor_select.setCurrentIndex(monitor_index)
        if map_name in MAP_PRESETS:
            self.map_select.setCurrentText(map_name)
        self._on_apply()

    def _on_save_preset(self):
        map_name = self.map_select.currentText()
        presets = self._load_presets()
        presets[map_name] = {
            "times_1": self.times_input_1.text().strip(),
            "hotkey_1": self.hotkey_input_1.text().strip().lower(),
            "times_2": self.times_input_2.text().strip(),
            "hotkey_2": self.hotkey_input_2.text().strip().lower(),
            "monitor_index": int(self.monitor_select.currentData()),
        }
        presets["_last_map"] = map_name
        presets["_last_role"] = self._current_role()
        self._save_presets(presets)


class CapTimerApp:
    def __init__(self, network=True, server_url=None):
        self.network_enabled = network
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = OverlayWindow()
        self.settings = SettingsWindow(self)
        self.cycle_index = [-1, -1]
        self.lock = threading.Lock()
        self.hotkey_handlers = [None, None]
        self.arrow_handlers = []
        self.monitor_index = 0
        self.selected_map = "Custom"
        self.role = DEFAULT_ROLE
        self.show_defense = True
        self.show_offense = True
        self.role_owners = {role: None for role in LOCKED_ROLES}
        self.claimed_role = None
        self.pending_role = None
        self.board_states = {
            "defense": [0] * len(BOARD_ASSETS),
            "offense": [0] * len(BOARD_ASSETS),
        }
        self.board_selected = {"defense": 0, "offense": 0}
        self._refresh_board_display("defense")
        self._refresh_board_display("offense")
        self.window.set_board_selected("defense", self.board_selected["defense"])
        self.window.set_board_selected("offense", self.board_selected["offense"])
        self.window.board_update_signal.connect(self._apply_board_update)
        
        # WebSocket support
        self.ws_client = None
        self.ws_loop = None
        if server_url and websockets:
            self.update_status("WebSocket: connecting...")
            print(f"Connecting to WebSocket server: {server_url}")
            self.ws_loop = asyncio.new_event_loop()
            self.ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
            self.ws_thread.start()
            self.ws_client = WebSocketClient(server_url, self)
            # Connect asynchronously
            asyncio.run_coroutine_threadsafe(self.ws_client._connect(), self.ws_loop)
            # Store loop reference in client
            self.ws_client.loop = self.ws_loop
        elif server_url:
            print("WARNING: websockets library not available. Install with: pip install websockets")
            self.update_status("WebSocket: missing dependency")
            QtWidgets.QMessageBox.warning(
                None,
                "Missing Dependency",
                "The 'websockets' library is missing, so network sync is disabled.",
            )
        
        # Keep UDP for LAN fallback (only if no WebSocket)
        if self.network_enabled and not server_url:
            self.sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock_tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock_tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener_thread = threading.Thread(target=self._udp_listener, daemon=True)
            self.listener_thread.start()
        else:
            self.sock_tx = None

        # global hotkey
        keyboard_thread = threading.Thread(target=self._setup_hotkeys, daemon=True)
        keyboard_thread.start()
    
    def _run_ws_loop(self):
        """Run asyncio event loop in separate thread"""
        asyncio.set_event_loop(self.ws_loop)
        self.ws_loop.run_forever()

    def _setup_hotkeys(self):
        try:
            print(
                f"Setting up hotkeys: '{HOTKEY_1}' (capper 1), '{HOTKEY_2}' (capper 2)"
            )
            # keyboard.on_press_key runs callbacks in background threads already
            self.hotkey_handlers[0] = keyboard.on_press_key(
                HOTKEY_1, lambda e: self._on_hotkey(0)
            )
            self.hotkey_handlers[1] = keyboard.on_press_key(
                HOTKEY_2, lambda e: self._on_hotkey(1)
            )
            self.arrow_handlers.append(
                keyboard.on_press_key("up", lambda e: self._on_arrow("up"))
            )
            self.arrow_handlers.append(
                keyboard.on_press_key("down", lambda e: self._on_arrow("down"))
            )
            self.arrow_handlers.append(
                keyboard.on_press_key("left", lambda e: self._on_arrow("left"))
            )
            self.arrow_handlers.append(
                keyboard.on_press_key("right", lambda e: self._on_arrow("right"))
            )
            print(f"Hotkeys registered successfully!")
        except Exception as e:
            print(f"ERROR: Failed to register hotkeys: {e}")
            print("On Windows, you may need to run as Administrator for global hotkeys to work.")
            print("Try right-clicking PowerShell/Terminal and selecting 'Run as Administrator'")
        # Keep the thread alive by waiting
        while True:
            time.sleep(1)
    
    def update_settings(
        self,
        times_text_1: str,
        hotkey_text_1: str,
        times_text_2: str,
        hotkey_text_2: str,
        monitor_index: int,
        map_name: Optional[str] = None,
        role: Optional[str] = None,
        show_defense: Optional[bool] = None,
        show_offense: Optional[bool] = None,
    ):
        global HOTKEY_1, HOTKEY_2, TIMER_OPTIONS_1, TIMER_OPTIONS_2
        with self.lock:
            new_times_1 = []
            if times_text_1:
                for part in times_text_1.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    try:
                        new_times_1.append(int(part))
                    except ValueError:
                        continue
            if new_times_1:
                TIMER_OPTIONS_1 = new_times_1
                self.cycle_index[0] = -1

            new_times_2 = []
            if times_text_2:
                for part in times_text_2.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    try:
                        new_times_2.append(int(part))
                    except ValueError:
                        continue
            if new_times_2:
                TIMER_OPTIONS_2 = new_times_2
                self.cycle_index[1] = -1

            if hotkey_text_1 and hotkey_text_1 != HOTKEY_1:
                try:
                    if self.hotkey_handlers[0] is not None:
                        keyboard.unhook(self.hotkey_handlers[0])
                    HOTKEY_1 = hotkey_text_1
                    self.hotkey_handlers[0] = keyboard.on_press_key(
                        HOTKEY_1, lambda e: self._on_hotkey(0)
                    )
                    print(f"Capper 1 hotkey updated to '{HOTKEY_1}'")
                except Exception as e:
                    print(f"ERROR: Failed to update capper 1 hotkey: {e}")

            if hotkey_text_2 and hotkey_text_2 != HOTKEY_2:
                try:
                    if self.hotkey_handlers[1] is not None:
                        keyboard.unhook(self.hotkey_handlers[1])
                    HOTKEY_2 = hotkey_text_2
                    self.hotkey_handlers[1] = keyboard.on_press_key(
                        HOTKEY_2, lambda e: self._on_hotkey(1)
                    )
                    print(f"Capper 2 hotkey updated to '{HOTKEY_2}'")
                except Exception as e:
                    print(f"ERROR: Failed to update capper 2 hotkey: {e}")

            if monitor_index != self.monitor_index:
                self.monitor_index = monitor_index
                self.position_window()
            if map_name:
                self.selected_map = map_name
            if role:
                if role in LOCKED_ROLES:
                    if self.role_owners.get(role) == MY_ID or self.role_owners.get(role) is None:
                        self._request_role(role)
                    else:
                        self.update_status(f"Role '{role}' is already taken")
                else:
                    self._set_role(role)
            if show_defense is not None:
                self.show_defense = bool(show_defense)
                self.window.set_board_visible("defense", self.show_defense)
            if show_offense is not None:
                self.show_offense = bool(show_offense)
                self.window.set_board_visible("offense", self.show_offense)

    def update_status(self, text: str):
        self.settings.set_status(text)

    def on_ws_connected(self):
        if self.role in LOCKED_ROLES:
            self._request_role(self.role)

    def handle_role_status(self, roles):
        for role in LOCKED_ROLES:
            self.role_owners[role] = roles.get(role)
        self.settings.update_role_availability(self.role_owners, MY_ID)
        if self.role in LOCKED_ROLES and self.role_owners.get(self.role) not in (None, MY_ID):
            self.update_status(f"Role '{self.role}' is taken")

    def handle_role_result(self, role, ok):
        if role not in LOCKED_ROLES:
            return
        if ok:
            self.claimed_role = role
            self.pending_role = None
            self._set_role(role)
            self.update_status(f"Role '{role}' claimed")
        else:
            self.pending_role = None
            self.settings.set_role(self.role)
            self.update_status(f"Role '{role}' is already taken")

    def _set_role(self, role):
        if role == self.role:
            return
        if self.role in LOCKED_ROLES:
            self._release_role(self.role)
        self.role = role
        self.settings.set_role(role)
        self.settings._save_last_role(role)

    def _request_role(self, role):
        if not self.ws_client or not self.ws_client.running:
            self._set_role(role)
            return
        self.pending_role = role
        asyncio.run_coroutine_threadsafe(
            self.ws_client.send_role_claim(role, MY_ID), self.ws_loop
        )

    def _release_role(self, role):
        if self.ws_client and self.ws_client.running:
            asyncio.run_coroutine_threadsafe(
                self.ws_client.send_role_release(role, MY_ID), self.ws_loop
            )

    def _effective_board_states(self, board: str):
        states = list(self.board_states[board])
        for i in range(len(states)):
            if states[i] == 1:
                states[i] = 0
        if states and states[0] == 2:
            for i in range(1, len(states)):
                states[i] = 1
        return states

    def _refresh_board_display(self, board: str):
        self.window.set_board_states(board, self._effective_board_states(board))

    def _on_hotkey(self, index: int):
        # cycle index => start chosen timer and broadcast if enabled
        print(f"Hotkey pressed for capper {index + 1}!")
        with self.lock:
            if index == 0:
                options = TIMER_OPTIONS_1
            else:
                options = TIMER_OPTIONS_2
            if not options:
                return
            self.cycle_index[index] = (self.cycle_index[index] + 1) % len(options)
            sec = options[self.cycle_index[index]]
            print(f"Emitting signal with {sec} seconds for capper {index + 1}")
            # Use Qt signal to safely call start() from background thread
            self.window.start_timer_signal.emit(index, float(sec))
            print(f"Signal emitted")

            # Send via WebSocket if connected
            if self.ws_client and self.ws_client.running:
                asyncio.run_coroutine_threadsafe(
                    self.ws_client.send_timer(sec, MY_ID, index + 1), self.ws_loop
                )
            # Fallback to UDP if WebSocket not available
            elif self.network_enabled and self.sock_tx:
                msg = {"cmd": "start", "seconds": sec, "sender": MY_ID, "capper": index + 1}
                try:
                    # broadcast to LAN
                    self.sock_tx.sendto(json.dumps(msg).encode("utf-8"), ("255.255.255.255", UDP_PORT))
                except Exception:
                    pass

    def _on_arrow(self, direction: str):
        board = None
        if self.role == "Defense":
            board = "defense"
        elif self.role == "Offense":
            board = "offense"
        else:
            return
        if board == "defense" and not self.show_defense:
            return
        if board == "offense" and not self.show_offense:
            return

        if direction in ("up", "down"):
            delta = -1 if direction == "up" else 1
            current = self.board_selected[board]
            current = (current + delta) % len(BOARD_ASSETS)
            self.board_selected[board] = current
            self.window.set_board_selected(board, current)
            return

        delta = -1 if direction == "left" else 1
        index = self.board_selected[board]
        state = self.board_states[board][index]
        if index == 0:
            state = 2 if state == 0 else 0
        else:
            state = 2 if state == 0 else 0
        self.board_states[board][index] = state
        self._refresh_board_display(board)
        self._broadcast_board_update(board, index, state)

    def _broadcast_board_update(self, board: str, index: int, state: int):
        if self.ws_client and self.ws_client.running:
            asyncio.run_coroutine_threadsafe(
                self.ws_client.send_board_update(board, index, state, MY_ID), self.ws_loop
            )
        elif self.network_enabled and self.sock_tx:
            msg = {
                "cmd": "board_update",
                "board": board,
                "index": index,
                "state": state,
                "sender": MY_ID,
            }
            try:
                self.sock_tx.sendto(json.dumps(msg).encode("utf-8"), ("255.255.255.255", UDP_PORT))
            except Exception:
                pass

    def _apply_board_update(self, board: str, index: int, state: int):
        if board not in self.board_states:
            return
        if index < 0 or index >= len(self.board_states[board]):
            return
        if state not in (0, 1, 2):
            return
        if state == 1:
            state = 0
        self.board_states[board][index] = state
        self._refresh_board_display(board)

    def _udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", UDP_PORT))
        except Exception:
            return
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if not isinstance(msg, dict):
                    continue
                if msg.get("sender") == MY_ID:
                    continue
                if msg.get("cmd") == "start" and "seconds" in msg:
                    capper = int(msg.get("capper", 1))
                    index = capper - 1
                    if index not in (0, 1):
                        continue
                    sec = float(msg["seconds"])
                    print(f"Received timer start from UDP (capper {capper}): {sec}s")
                    # Use signal for thread-safe communication with Qt thread
                    self.window.start_timer_signal.emit(index, float(sec))
                elif msg.get("cmd") == "board_update":
                    board = msg.get("board")
                    index = int(msg.get("index", -1))
                    state = int(msg.get("state", -1))
                    if board not in ("defense", "offense"):
                        continue
                    if index < 0 or index >= len(BOARD_ASSETS):
                        continue
                    if state not in (0, 1, 2):
                        continue
                    self.window.board_update_signal.emit(board, index, state)
            except Exception:
                continue

    def run(self):
        self.position_window()
        if self.role == DEFAULT_ROLE:
            selected = self.settings.prompt_role(self.role)
            if selected:
                self._set_role(selected)
        # Show settings window
        self.settings.load_current(
            TIMER_OPTIONS_1,
            HOTKEY_1,
            TIMER_OPTIONS_2,
            HOTKEY_2,
            self.monitor_index,
            self.selected_map,
            self.role,
            self.show_defense,
            self.show_offense,
        )
        self.window.set_board_visible("defense", self.show_defense)
        self.window.set_board_visible("offense", self.show_offense)
        self.settings.show()
        
        # Process events to ensure window is rendered
        self.app.processEvents()
        print(f"Window should be visible. Label texts: {self.window.label.texts()}")
        sys.exit(self.app.exec())

    def position_window(self):
        screens = QtWidgets.QApplication.screens()
        if not screens:
            return
        if not 0 <= self.monitor_index < len(screens):
            self.monitor_index = 0
        screen = screens[self.monitor_index].availableGeometry()
        w = WINDOW_WIDTH
        h = WINDOW_HEIGHT
        x = int(screen.x() + (screen.width() - w) / 2)
        y = int(screen.y() + screen.height() * 0.05)
        
        # Set window geometry explicitly
        self.window.setGeometry(x, y, w, h)
        self.window.resize(w, h)
        
        print(f"Window positioned at ({x}, {y}) with size {w}x{h}")
        print(f"Screen size: {screen.width()}x{screen.height()}")
        
        # Show window immediately so it's ready - make sure it's visible
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        # Force window to be on top
        self.window.setWindowState(
            self.window.windowState()
            & ~QtCore.Qt.WindowState.WindowMinimized
            | QtCore.Qt.WindowState.WindowActive
        )
        self.window.raise_()
        
        # Ensure label is visible and properly sized
        self.window.label.show()
        self.window.label.setVisible(True)
        self.window.label.resize(TIMER_WIDTH, WINDOW_HEIGHT)


def parse_args():
    p = argparse.ArgumentParser(description="Simple Tribes cap timer overlay")
    p.add_argument("--no-network", action="store_true", help="Disable network sync (local-only)")
    p.add_argument(
        "--server",
        default=DEFAULT_SERVER_URL,
        help="WebSocket server URL (e.g., wss://your-app.railway.app)",
    )
    p.add_argument("--hotkey1", default=HOTKEY_1, help="Capper 1 hotkey (default: v)")
    p.add_argument("--hotkey2", default=HOTKEY_2, help="Capper 2 hotkey (default: b)")
    p.add_argument("--monitor", type=int, default=1, help="Monitor number (1 = primary)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    HOTKEY_1 = args.hotkey1.lower()
    HOTKEY_2 = args.hotkey2.lower()
    server_url = None if args.no_network else args.server
    app = CapTimerApp(
        network=(not args.no_network),
        server_url=server_url
    )
    app.monitor_index = max(0, args.monitor - 1)
    app.settings.load_last_preset()
    app.run()
