# livetchat/server/ws_manager.py
# Connexions WebSocket authentifiées : qui est connecté, dans quel salon, avec quel pseudo.
import asyncio
import json
from dataclasses import dataclass, field

from fastapi import WebSocket

from livetchat.server.security import RateLimiter

SEND_TIMEOUT_S = 5


@dataclass(eq=False)
class Client:
    ws: WebSocket
    client_id: str
    username: str
    channel: str
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(20, 10))


class ConnectionManager:

    def __init__(self):
        self.clients: set[Client] = set()

    def add(self, client: Client):
        self.clients.add(client)
        print(f"[WS] +1 '{client.username}' #{client.channel} — {len(self.clients)} connecté(s)")

    def remove(self, client: Client):
        if client in self.clients:
            self.clients.discard(client)
            print(f"[WS] -1 '{client.username}' #{client.channel} — {len(self.clients)} connecté(s)")

    def members(self, channel: str) -> list[str]:
        return sorted((c.username for c in self.clients if c.channel == channel), key=str.lower)

    async def _send_many(self, targets: list[Client], message: dict):
        if not targets:
            return
        payload = json.dumps(message)
        results = await asyncio.gather(
            *(asyncio.wait_for(c.ws.send_text(payload), SEND_TIMEOUT_S) for c in targets),
            return_exceptions=True,
        )
        for client, err in zip(targets, results):
            if isinstance(err, Exception):
                print(f"[WS] envoi échoué vers '{client.username}' → déconnexion")
                self.remove(client)
                try:
                    await client.ws.close()
                except Exception:
                    pass

    async def send(self, client: Client, message: dict):
        await self._send_many([client], message)

    async def broadcast_to_channel(self, channel: str, message: dict):
        await self._send_many([c for c in self.clients if c.channel == channel], message)

    async def broadcast_all(self, message: dict):
        await self._send_many(list(self.clients), message)
