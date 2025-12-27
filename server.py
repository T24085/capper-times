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
LOCKED_ROLES = ["Capper 1", "Capper 2"]
role_claims = {}


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
        await websocket.send(json.dumps({"cmd": "role_status", "roles": _roles_payload()}))
        
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
                        "sender": data.get("sender"),
                        "capper": data.get("capper", 1),
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
                    logger.info(
                        f"Broadcasted timer start ({data.get('seconds')}s, capper {data.get('capper', 1)}) "
                        f"to {len(clients) - 1} clients"
                    )
                elif cmd == "board_update":
                    broadcast_msg = json.dumps({
                        "cmd": "board_update",
                        "board": data.get("board"),
                        "index": data.get("index"),
                        "state": data.get("state"),
                        "sender": data.get("sender"),
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
                    logger.info(
                        f"Broadcasted board update ({data.get('board')}, {data.get('index')}, {data.get('state')}) "
                        f"to {len(clients) - 1} clients"
                    )
                elif cmd == "role_claim":
                    role = data.get("role")
                    sender = data.get("sender")
                    if role in LOCKED_ROLES:
                        owner = role_claims.get(role)
                        if owner is None or owner.get("ws") == websocket:
                            role_claims[role] = {"id": sender, "ws": websocket}
                            await websocket.send(
                                json.dumps({"cmd": "role_result", "role": role, "ok": True})
                            )
                            await _broadcast_role_status()
                        else:
                            await websocket.send(
                                json.dumps({"cmd": "role_result", "role": role, "ok": False})
                            )
                elif cmd == "role_release":
                    role = data.get("role")
                    if role in LOCKED_ROLES and role_claims.get(role, {}).get("ws") == websocket:
                        role_claims.pop(role, None)
                        await _broadcast_role_status()
                
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
        _release_roles_for_client(websocket)
        logger.info(f"Active clients: {len(clients)}")


def _roles_payload():
    payload = {}
    for role in LOCKED_ROLES:
        owner = role_claims.get(role)
        payload[role] = owner.get("id") if owner else None
    return payload


async def _broadcast_role_status():
    msg = json.dumps({"cmd": "role_status", "roles": _roles_payload()})
    disconnected = set()
    for client in clients:
        try:
            await client.send(msg)
        except websockets.exceptions.ConnectionClosed:
            disconnected.add(client)
    clients.difference_update(disconnected)


def _release_roles_for_client(websocket):
    released = False
    for role in list(role_claims.keys()):
        if role_claims.get(role, {}).get("ws") == websocket:
            role_claims.pop(role, None)
            released = True
    if released:
        try:
            asyncio.create_task(_broadcast_role_status())
        except RuntimeError:
            pass


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

