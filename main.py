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

from PyQt5 import QtWidgets, QtCore, QtGui

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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cap Timer Overlay")
        # Use simpler window flags first to ensure visibility
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        )
        # Don't use WA_TranslucentBackground initially - it might prevent rendering
        # self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.resize(600, 200)
        # Set a background color so we can see the window
        self.setStyleSheet("background-color: rgba(0, 0, 0, 200);")

        # central label
        self.label = QtWidgets.QLabel("", self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        font = QtGui.QFont("Segoe UI", 72, QtGui.QFont.Bold)
        self.label.setFont(font)
        # Make text highly visible with dark background and bright text
        self.label.setStyleSheet("""
            QLabel {
                color: #00FF00;
                background-color: rgba(0, 0, 0, 200);
                border: 3px solid rgba(255, 255, 255, 255);
                border-radius: 15px;
                padding: 20px;
            }
        """)
        # Ensure label fills the window
        self.label.setMinimumSize(600, 200)
        self.setCentralWidget(self.label)
        # Set initial text to test visibility - make it visible immediately
        self.label.setText("READY")
        self.label.show()
        print("Overlay window created, label initialized with 'READY' text")

        # background opacity widget to improve visibility
        self.bg = None
        self._apply_click_through_later()

        # timer
        self._remaining = 0.0
        self._qtimer = QtCore.QTimer()
        self._qtimer.setInterval(50)  # 20 Hz
        self._qtimer.timeout.connect(self._tick)

    def _apply_click_through_later(self):
        # Apply click-through after window is created on Windows
        if sys.platform.startswith("win") and win32gui:
            QtCore.QTimer.singleShot(200, self._make_click_through)

    def _make_click_through(self):
        # TEMPORARILY DISABLED - Test visibility first
        # The WS_EX_TRANSPARENT flag might be preventing rendering
        # Let's get the window visible first, then we can add click-through back
        hwnd = int(self.winId())
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        # Only use WS_EX_LAYERED for transparency, NOT WS_EX_TRANSPARENT
        # WS_EX_TRANSPARENT makes the window completely click-through but can prevent rendering
        ex_style |= win32con.WS_EX_LAYERED
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        # Make window visible (fully opaque)
        win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)
        print("Window configured (click-through DISABLED for testing - window should be visible)")
        print("NOTE: Window will NOT be click-through - you can click on it to test visibility")

    def start(self, seconds: float):
        self._remaining = float(seconds)
        print(f"Timer start called: {seconds}s, remaining={self._remaining}")
        # Ensure window is shown and visible first
        self.show()
        self.raise_()
        self.activateWindow()
        # Force window to front
        self.setWindowState(self.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive)
        self.raise_()
        # Ensure label is visible
        self.label.show()
        self.label.setVisible(True)
        # Update label after window is shown
        self._update_label()
        print(f"Label text after update: '{self.label.text()}'")
        # Process events to ensure update happens immediately
        QtWidgets.QApplication.processEvents()
        print(f"Timer started: {seconds}s - Window should be visible, label='{self.label.text()}'")
        self._qtimer.start()

    def stop(self):
        self._qtimer.stop()
        self.label.setText("")
        self._remaining = 0.0

    def _tick(self):
        self._remaining -= 0.05
        if self._remaining <= 0:
            self.stop()
            return
        self._update_label()

    def _update_label(self):
        sec = int(self._remaining + 0.999)  # ceil-ish display
        text = f"{sec:02d}s"
        print(f"DEBUG: Updating label to: '{text}' (remaining={self._remaining:.2f})")
        self.label.setText(text)
        # Ensure label is visible and shown
        self.label.show()
        self.label.setVisible(True)
        # Force update
        self.label.update()
        self.label.repaint()
        self.update()
        self.repaint()
        # Process events to ensure update happens
        QtWidgets.QApplication.processEvents()
        print(f"DEBUG: Label text is now: '{self.label.text()}'")


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
        print(f"Hotkey '{HOTKEY}' pressed!")
        with self.lock:
            self.cycle_index = (self.cycle_index + 1) % len(TIMER_OPTIONS)
            sec = TIMER_OPTIONS[self.cycle_index]
            print(f"Starting timer: {sec} seconds")
            # start locally (must run in Qt main thread)
            # Use QTimer.singleShot to execute in Qt event loop
            # Capture sec in lambda to avoid closure issues
            def start_timer(s):
                try:
                    print(f"About to call window.start({s})")
                    self.window.start(s)
                    print(f"window.start({s}) completed")
                except Exception as e:
                    print(f"ERROR calling window.start: {e}")
                    import traceback
                    traceback.print_exc()
            QtCore.QTimer.singleShot(0, lambda s=sec: start_timer(s))

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
        w = self.window.width()
        h = self.window.height()
        self.window.move(int((screen.width() - w) / 2), int(screen.height() * 0.05))
        # Show window immediately so it's ready - make sure it's visible
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        # Force window to be on top
        self.window.setWindowState(self.window.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive)
        self.window.raise_()
        # Process events to ensure window is rendered
        self.app.processEvents()
        print("Overlay window initialized and should be visible at top center of screen")
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

