# Tribes Rivals Cap Timer Overlay

Simple overlay countdown timer to sync capper routes across players.

## Features

- **Global Hotkey**: Press `V` (configurable) to cycle through timer presets (35s → 25s → 20s)
- **Cross-Network Sync**: Connect to a WebSocket server (Railway) for team-wide timer sync across the internet
- **LAN Sync**: Fallback UDP broadcast for local network synchronization
- **Always-on-top Overlay**: Transparent, click-through window that won't block gameplay
- **Large Display**: Easy-to-read countdown timer

## Installation

### Client Installation

1. Install Python 3.7+ if you don't have it
2. Install client dependencies:
   ```bash
   pip install -r requirements-client.txt
   ```

### Server Installation (for local testing)

The server only needs `websockets`:
```bash
pip install websockets
```

Note: `requirements.txt` is used by Railway and only contains server dependencies (no Windows-specific packages).

## Usage

### Client (Overlay Timer)

Connect to Railway server (recommended for cross-network teams):
```bash
python main.py --server wss://your-app.railway.app
```

Run with LAN sync (UDP broadcast, local network only):
```bash
python main.py
```

Run local-only (no network sync):
```bash
python main.py --no-network
```

Custom hotkey:
```bash
python main.py --server wss://your-app.railway.app --hotkey f
```

### Server (Railway Deployment)

1. **Deploy to Railway:**
   - Sign up at https://railway.app
   - Create a new project → "Deploy from GitHub repo" (or upload files)
   - Railway will auto-detect Python and deploy
   - Note your app URL (e.g., `your-app.railway.app`)

2. **Use WebSocket URL:**
   - Railway uses HTTPS, so use `wss://` (secure WebSocket)
   - Example: `wss://your-app.railway.app`

3. **Optional: Password Protection**
   - In Railway dashboard, add environment variable: `PASSWORD=your-secret-password`
   - Clients will need to authenticate (not yet implemented in client, but server supports it)

4. **Test locally:**
   ```bash
   python server.py
   # Then connect clients with: python main.py --server ws://localhost:8765
   ```

## How it Works

- The overlay is a frameless, translucent PyQt5 window with a large QLabel that displays the remaining seconds
- Global hotkey handling is done by the `keyboard` package; when pressed it starts/restarts the timer
- **WebSocket Mode**: When connected to a server, timer starts are broadcast to all connected clients via WebSocket
- **UDP Mode**: Falls back to UDP broadcast on local network (port 54545) if no server specified
- Windows-specific click-through functionality uses `pywin32` to make the overlay non-interactive

## Requirements

- **Client**: Windows (for click-through functionality), Python 3.7+
- **Server**: Any platform, Python 3.7+
- See `requirements-client.txt` for client dependencies
- See `requirements.txt` for server dependencies (Railway)

## Team Setup

### For Your Team Members:

1. **Install Python** (if not already installed)
   - Download from https://www.python.org/downloads/
   - Make sure to check "Add Python to PATH" during installation

2. **Get the Files:**
   - Download/clone this repository
   - Or just get these files:
     - `main.py`
     - `start-timer.bat`
     - `requirements-client.txt`

3. **Install Dependencies:**
   ```bash
   pip install -r requirements-client.txt
   ```

4. **Run the Timer:**
   - Double-click `start-timer.bat`
   - Or edit `start-timer.bat` and change the server URL if needed
   - The overlay will appear with "READY" text
   - Press `V` to start timers (35s → 25s → 20s)

5. **Server URL:**
   - Your Railway server: `wss://web-production-03594.up.railway.app`
   - This is already configured in `start-timer.bat`

## Customization

### Change Timer Presets

Edit `main.py` and find:
```python
TIMER_OPTIONS = [35, 25, 20]  # Change these values
```

### Change Hotkey

Edit `start-timer.bat` and change:
```bash
python main.py --server wss://web-production-03594.up.railway.app --hotkey f
```
(Change `f` to any key you want)

### Change Colors

In `main.py`, find the `setStyleSheet` calls:
- Green color (normal): `color: #00FF00;`
- Red color (warning): `color: #FF0000;`
- Background: `background-color: rgba(0, 0, 0, 200);`

### Change Position

In `main.py`, find `run()` method and change:
```python
self.window.move(int((screen.width() - w) / 2), int(screen.height() * 0.05))
```
- First number: horizontal position (0 = left, screen.width = right)
- Second number: vertical position (0 = top, screen.height = bottom)

### Change Size

In `main.py`, find:
```python
self.resize(600, 200)  # width, height in pixels
self.label.setMinimumSize(600, 200)
```

### Change Font Size

In `main.py`, find:
```python
font = QtGui.QFont("Segoe UI", 72, QtGui.QFont.Bold)  # Change 72 to desired size
```

