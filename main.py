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
DEFAULT_SERVER_URL = os.environ.get(
    "CAPTIMER_SERVER",
    "wss://web-production-03594.up.railway.app",
)

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
                    if data.get("cmd") == "start" and "seconds" in data:
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


class OverlayWindow(QtWidgets.QMainWindow):
    # Signal to start timer from any thread
    start_timer_signal = QtCore.pyqtSignal(int, float)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cap Timer Overlay")
        # Use simpler window flags first to ensure visibility
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.resize(600, 200)
        # Ensure transparent window background (required in PyQt6)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background-color: transparent;")
        
        # Connect signal to start method
        self.start_timer_signal.connect(self.start_timer)

        # central display widget
        self.label = OverlayLabel(lines=2, parent=self)
        self.label.setMinimumSize(600, 200)
        self.label.setMaximumSize(600, 200)
        self.label.resize(600, 200)
        self.setCentralWidget(self.label)
        
        # Ensure window geometry is correct
        self.setGeometry(0, 0, 600, 200)
        # Set initial text
        self._ready_texts = ["CAP 1 READY", "CAP 2 READY"]
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
        self.setFixedSize(360, 300)

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

        apply_btn = QtWidgets.QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)

        exit_btn = QtWidgets.QPushButton("Exit")
        exit_btn.clicked.connect(QtWidgets.QApplication.quit)

        self.status_label = QtWidgets.QLabel("WebSocket: idle")

        layout.addLayout(form)
        layout.addWidget(apply_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(exit_btn)

        self.setLayout(layout)

    def load_current(self, times_1, hotkey_1, times_2, hotkey_2):
        self.times_input_1.setText(",".join(str(t) for t in times_1))
        self.hotkey_input_1.setText(hotkey_1)
        self.times_input_2.setText(",".join(str(t) for t in times_2))
        self.hotkey_input_2.setText(hotkey_2)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def _on_apply(self):
        times_text_1 = self.times_input_1.text().strip()
        hotkey_text_1 = self.hotkey_input_1.text().strip().lower()
        times_text_2 = self.times_input_2.text().strip()
        hotkey_text_2 = self.hotkey_input_2.text().strip().lower()
        self.app.update_settings(times_text_1, hotkey_text_1, times_text_2, hotkey_text_2)


class CapTimerApp:
    def __init__(self, network=True, server_url=None):
        self.network_enabled = network
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = OverlayWindow()
        self.settings = SettingsWindow(self)
        self.cycle_index = [-1, -1]
        self.lock = threading.Lock()
        self.hotkey_handlers = [None, None]
        
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

    def update_status(self, text: str):
        self.settings.set_status(text)

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
            except Exception:
                continue

    def run(self):
        # center on primary screen
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        w = 600
        h = 200
        x = int((screen.width() - w) / 2)
        y = int(screen.height() * 0.05)
        
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
        self.window.label.resize(w, h)

        # Show settings window
        self.settings.load_current(TIMER_OPTIONS_1, HOTKEY_1, TIMER_OPTIONS_2, HOTKEY_2)
        self.settings.show()
        
        # Process events to ensure window is rendered
        self.app.processEvents()
        print(f"Window should be visible. Label texts: {self.window.label.texts()}")
        sys.exit(self.app.exec())


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
    app.run()
