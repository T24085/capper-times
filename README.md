# Tribes Rivals Cap Timer Overlay

Simple overlay countdown timer to sync capper routes across players.

## Features

- **Global Hotkey**: Press `V` (configurable) to cycle through timer presets (35s → 25s → 20s)
- **Cross-Network Sync**: Connect to a WebSocket server (Railway) for team-wide timer sync across the internet
- **LAN Sync**: Fallback UDP broadcast for local network synchronization
- **Always-on-top Overlay**: Transparent, click-through window that won't block gameplay
- **Large Display**: Easy-to-read countdown timer

## Installation

1. Install Python 3.7+ if you don't have it
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

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

- Windows (for click-through functionality)
- Python 3.7+
- See `requirements.txt` for Python dependencies

