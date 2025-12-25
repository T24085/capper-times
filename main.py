#!/usr/bin/env python3
# main.py
# Simple overlay countdown timer with optional LAN sync (UDP broadcast)
# Windows-oriented (PyQt5 + keyboard + pywin32 for click-through)
#
# Usage:
#   python main.py        # runs with network sync enabled
#   python main.py --no-network  # run local-only
#
# Requirements (pip):
#   pip install PyQt5 keyboard pywin32

import sys
import time
import json
import socket
import uuid
import threading
import argparse
import os

# Suppress Qt warnings/errors to console
os.environ['QT_LOGGING_RULES'] = '*.debug=false'

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import pyqtSignal

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
HOTKEY = "v"             # key to press
UDP_PORT = 54545         # port for LAN sync
TIMER_OPTIONS = [35, 25, 20]  # cycle order as requested

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
            # Start listening
            asyncio.create_task(self._listen())
            return True
        except Exception as e:
            print(f"Failed to connect to server: {e}")
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
                        sec = float(data["seconds"])
                        # Update timer in Qt thread
                        # Capture sec in lambda to avoid closure issues
                        QtCore.QTimer.singleShot(0, lambda s=sec: self.app.window.start(s))
                except Exception:
                    continue
        except websockets.exceptions.ConnectionClosed:
            self.running = False
            print("Disconnected from server")
        except Exception as e:
            print(f"WebSocket error: {e}")
            self.running = False
    
    async def send_timer(self, seconds, sender_id):
        """Send timer start to server"""
        if self.websocket and self.running:
            try:
                msg = json.dumps({"cmd": "start", "seconds": seconds, "sender": sender_id})
                await self.websocket.send(msg)
            except Exception as e:
                print(f"Failed to send: {e}")
    
    def close(self):
        """Close connection"""
        self.running = False
        if self.websocket and self.loop:
            asyncio.run_coroutine_threadsafe(self.websocket.close(), self.loop)


class OverlayWindow(QtWidgets.QMainWindow):
    # Signal to start timer from any thread
    start_timer_signal = QtCore.pyqtSignal(float)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cap Timer Overlay")
        # Use simpler window flags first to ensure visibility
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        )
        self.resize(600, 200)
        # Set transparent background using stylesheet
        self.setStyleSheet("background-color: transparent;")
        
        # Connect signal to start method
        self.start_timer_signal.connect(self.start)

        # central label
        self.label = QtWidgets.QLabel("", self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        font = QtGui.QFont("Segoe UI", 72, QtGui.QFont.Bold)
        self.label.setFont(font)
        # Make text visible with transparent background - only text shows
        self.label.setStyleSheet("""
            QLabel {
                color: #00FF00;
                background-color: transparent;
                border: none;
                padding: 20px;
            }
        """)
        # Ensure label fills the window and is properly sized
        self.label.setMinimumSize(600, 200)
        self.label.setMaximumSize(600, 200)
        self.label.resize(600, 200)
        self.setCentralWidget(self.label)
        
        # Ensure window geometry is correct
        self.setGeometry(0, 0, 600, 200)
        # Set initial text
        self.label.setText("READY")
        self.label.show()

        # background opacity widget to improve visibility
        self.bg = None
        self._apply_click_through_later()

        # timer
        self._remaining = 0.0
        self._qtimer = QtCore.QTimer()
        self._qtimer.setInterval(50)  # 20 Hz
        self._qtimer.timeout.connect(self._tick)
        
        # Flash timer for red warning
        self._flash_timer = QtCore.QTimer()
        self._flash_timer.setInterval(250)  # Flash every 250ms
        self._flash_state = False
        self._flash_timer.timeout.connect(self._flash_tick)

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
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            
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

    def start(self, seconds: float):
        print(f"start() called with {seconds} seconds")
        self._remaining = float(seconds)
        # Ensure window is shown and visible first
        self.show()
        self.raise_()
        self.activateWindow()
        # Force window to front
        self.setWindowState(self.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive)
        self.raise_()
        # Update label
        print(f"Updating label, remaining={self._remaining}")
        self._update_label()
        print(f"Starting timer, label text='{self.label.text()}'")
        self._qtimer.start()
        print(f"Timer started successfully")

    def stop(self):
        self._qtimer.stop()
        self._flash_timer.stop()
        self.label.setText("")
        self._remaining = 0.0
        self._flash_state = False

    def _tick(self):
        self._remaining -= 0.05
        if self._remaining <= 0:
            self.stop()
            return
        self._update_label()
    
    def _flash_tick(self):
        """Flash the label when <= 10 seconds"""
        if self._remaining <= 10:
            self._flash_state = not self._flash_state
            # Alternate between bright red and slightly dimmed red for flash effect
            if self._flash_state:
                # Bright red
                self.label.setStyleSheet("""
                    QLabel {
                        color: #FF0000;
                        background-color: transparent;
                        border: none;
                        padding: 20px;
                    }
                """)
            else:
                # Dimmed red (darker)
                self.label.setStyleSheet("""
                    QLabel {
                        color: #CC0000;
                        background-color: transparent;
                        border: none;
                        padding: 20px;
                    }
                """)
            self.label.update()
            self.update()

    def _update_label(self):
        sec = int(self._remaining + 0.999)  # ceil-ish display
        text = f"{sec:02d}s"
        self.label.setText(text)
        
        # Change to red and start flashing if <= 10 seconds
        if self._remaining <= 10:
            if not self._flash_timer.isActive():
                self._flash_timer.start()
            # Red color for warning - transparent background
            self.label.setStyleSheet("""
                QLabel {
                    color: #FF0000;
                    background-color: transparent;
                    border: none;
                    padding: 20px;
                }
            """)
        else:
            # Stop flashing and use normal green color - transparent background
            if self._flash_timer.isActive():
                self._flash_timer.stop()
                self._flash_state = False
            self.label.setStyleSheet("""
                QLabel {
                    color: #00FF00;
                    background-color: transparent;
                    border: none;
                    padding: 20px;
                }
            """)
            self.label.setVisible(True)  # Ensure visible when not flashing
        
        # Force update - schedule in Qt event loop to avoid layered window errors
        QtCore.QTimer.singleShot(0, lambda: self.label.update())
        QtCore.QTimer.singleShot(0, lambda: self.update())


class CapTimerApp:
    def __init__(self, network=True, server_url=None):
        self.network_enabled = network
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = OverlayWindow()
        self.cycle_index = -1
        self.lock = threading.Lock()
        
        # WebSocket support
        self.ws_client = None
        self.ws_loop = None
        if server_url and websockets:
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
        keyboard_thread = threading.Thread(target=self._setup_hotkey, daemon=True)
        keyboard_thread.start()
    
    def _run_ws_loop(self):
        """Run asyncio event loop in separate thread"""
        asyncio.set_event_loop(self.ws_loop)
        self.ws_loop.run_forever()

    def _setup_hotkey(self):
        try:
            print(f"Setting up hotkey: '{HOTKEY}' (press this key to start timer)")
            # keyboard.on_press_key runs callbacks in background threads already
            keyboard.on_press_key(HOTKEY, lambda e: self._on_hotkey())
            print(f"Hotkey '{HOTKEY}' registered successfully!")
        except Exception as e:
            print(f"ERROR: Failed to register hotkey '{HOTKEY}': {e}")
            print("On Windows, you may need to run as Administrator for global hotkeys to work.")
            print("Try right-clicking PowerShell/Terminal and selecting 'Run as Administrator'")
        # Keep the thread alive by waiting
        while True:
            time.sleep(1)

    def _on_hotkey(self):
        # cycle index => start chosen timer and broadcast if enabled
        print(f"Hotkey pressed!")
        with self.lock:
            self.cycle_index = (self.cycle_index + 1) % len(TIMER_OPTIONS)
            sec = TIMER_OPTIONS[self.cycle_index]
            print(f"Emitting signal with {sec} seconds")
            # Use Qt signal to safely call start() from background thread
            self.window.start_timer_signal.emit(float(sec))
            print(f"Signal emitted")

            # Send via WebSocket if connected
            if self.ws_client and self.ws_client.running:
                asyncio.run_coroutine_threadsafe(
                    self.ws_client.send_timer(sec, MY_ID), self.ws_loop
                )
            # Fallback to UDP if WebSocket not available
            elif self.network_enabled and self.sock_tx:
                msg = {"cmd": "start", "seconds": sec, "sender": MY_ID}
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
                    sec = float(msg["seconds"])
                    # run in Qt thread
                    # Capture sec in lambda to avoid closure issues
                    QtCore.QTimer.singleShot(0, lambda s=sec: self.window.start(s))
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
        self.window.setWindowState(self.window.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive)
        self.window.raise_()
        
        # Ensure label is visible and properly sized
        self.window.label.show()
        self.window.label.setVisible(True)
        self.window.label.resize(w, h)
        
        # Process events to ensure window is rendered
        self.app.processEvents()
        print(f"Window should be visible. Label text: '{self.window.label.text()}'")
        sys.exit(self.app.exec_())


def parse_args():
    p = argparse.ArgumentParser(description="Simple Tribes cap timer overlay")
    p.add_argument("--no-network", action="store_true", help="Disable network sync (local-only)")
    p.add_argument("--server", help="WebSocket server URL (e.g., wss://your-app.railway.app)")
    p.add_argument("--hotkey", default=HOTKEY, help="Hotkey to trigger the timer (default: v)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    HOTKEY = args.hotkey.lower()
    app = CapTimerApp(
        network=(not args.no_network),
        server_url=args.server
    )
    app.run()

