#!/usr/bin/env python3
# server.py
# WebSocket server for cross-network timer sync
# Deploy to Railway or run locally
#
# Requirements: pip install websockets

import asyncio
import websockets
import json
import argparse
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store connected clients
clients = set()
PASSWORD = None  # Optional password protection


async def handle_client(websocket):
    """Handle a new client connection"""
    client_id = str(websocket.remote_address)
    path = getattr(websocket, 'path', '/')  # Get path from websocket object
    logger.info(f"Client connected: {client_id}, path: {path}")
    clients.add(websocket)
    
    try:
        # Optional: send password prompt
        if PASSWORD:
            await websocket.send(json.dumps({"cmd": "auth_required"}))
            auth_msg = await websocket.recv()
            auth_data = json.loads(auth_msg)
            if auth_data.get("password") != PASSWORD:
                await websocket.send(json.dumps({"cmd": "auth_failed"}))
                return
        
        await websocket.send(json.dumps({"cmd": "connected", "clients": len(clients)}))
        
        # Set ping interval to keep connection alive (Railway closes idle connections)
        websocket.ping_interval = 20  # Send ping every 20 seconds
        
        # Listen for messages from this client
        async for message in websocket:
            try:
                data = json.loads(message)
                cmd = data.get("cmd")
                
                if cmd == "start":
                    # Broadcast timer start to all OTHER clients
                    broadcast_msg = json.dumps({
                        "cmd": "start",
                        "seconds": data.get("seconds"),
                        "sender": data.get("sender")
                    })
                    
                    disconnected = set()
                    for client in clients:
                        if client != websocket:  # Don't send back to sender
                            try:
                                await client.send(broadcast_msg)
                            except websockets.exceptions.ConnectionClosed:
                                disconnected.add(client)
                    
                    # Remove disconnected clients
                    clients.difference_update(disconnected)
                    logger.info(f"Broadcasted timer start ({data.get('seconds')}s) to {len(clients) - 1} clients")
                    
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {client_id}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {client_id}")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        clients.discard(websocket)
        logger.info(f"Active clients: {len(clients)}")


async def main():
    # Railway provides PORT environment variable
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "0.0.0.0")
    password = os.environ.get("PASSWORD", None)
    
    global PASSWORD
    PASSWORD = password
    
    logger.info(f"Starting WebSocket server on {host}:{port}")
    if PASSWORD:
        logger.info("Password protection enabled")
    
    # Configure WebSocket server with ping/pong keepalive
    async with websockets.serve(
        handle_client, 
        host, 
        port,
        ping_interval=20,  # Send ping every 20 seconds
        ping_timeout=10,   # Wait 10 seconds for pong
        close_timeout=10   # Wait 10 seconds when closing
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())


