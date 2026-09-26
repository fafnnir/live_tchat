# livetchat/server/channel_queue.py
# Une file par salon : les médias passent un par un, chacun pendant display_time secondes
# (ou moins si l'expéditeur le passe pour tout le monde).
import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from livetchat.server import settings
from livetchat.server.security import media_signature


@dataclass(eq=False)
class MediaItem:
    media_id: str
    kind: str
    path: str
    content_type: str
    display_time: float
    display_text: str
    username: str
    sender_id: str
    channel: str
    link_exp: int = 0
    skip_event: asyncio.Event = field(default_factory=asyncio.Event)


# Médias servables : media_id -> MediaItem (retirés quand le fichier est effacé)
MEDIA: dict[str, MediaItem] = {}


def delete_media(item: MediaItem):
    MEDIA.pop(item.media_id, None)
    try:
        os.remove(item.path)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[MEDIA] suppression impossible {item.path}: {e}")


class ChannelQueue:

    def __init__(self, channel: str, manager, on_change: Callable[[], Awaitable[None]]):
        self.channel = channel
        self.manager = manager
        self.on_change = on_change
        self.queue: deque[MediaItem] = deque()
        self.current: MediaItem | None = None
        self._task: asyncio.Task | None = None

    def is_full(self) -> bool:
        return len(self.queue) + (1 if self.current else 0) >= settings.MAX_QUEUE_PER_CHANNEL

    def push(self, item: MediaItem) -> int:
        self.queue.append(item)
        MEDIA[item.media_id] = item
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run())
        return len(self.queue) + (1 if self.current else 0)

    def skip(self, media_id: str, client_id: str) -> bool:
        """Seul l'expéditeur peut passer son média pour tout le monde."""
        cur = self.current
        if cur and cur.media_id == media_id and cur.sender_id == client_id:
            cur.skip_event.set()
            return True
        return False

    def queue_size(self) -> int:
        return len(self.queue)

    async def _run(self):
        loop = asyncio.get_running_loop()
        while self.queue:
            item = self.queue.popleft()
            self.current = item
            skipped = False
            try:
                item.link_exp = int(time.time() + item.display_time + settings.MEDIA_LINK_GRACE_S)
                sig = media_signature(item.media_id, item.link_exp)
                await self.manager.broadcast_to_channel(self.channel, {
                    "type": "display_start",
                    "media_id": item.media_id,
                    "kind": item.kind,
                    "content_type": item.content_type,
                    "url": f"/media/{item.media_id}/{item.link_exp}/{sig}",
                    "display_time": item.display_time,
                    "display_text": item.display_text,
                    "username": item.username,
                    "sender_id": item.sender_id,
                    "channel": self.channel,
                    "queue_remaining": len(self.queue),
                })
                await self.on_change()
                try:
                    await asyncio.wait_for(item.skip_event.wait(), item.display_time)
                    skipped = True
                except asyncio.TimeoutError:
                    pass
                await self.manager.broadcast_to_channel(self.channel, {
                    "type": "display_end",
                    "media_id": item.media_id,
                    "channel": self.channel,
                    "skipped": skipped,
                })
            except Exception as e:
                print(f"[QUEUE] #{self.channel} erreur: {e}")
            finally:
                self.current = None
                # Laisse le temps aux clients lents de finir le téléchargement, puis efface
                loop.call_later(settings.MEDIA_DELETE_AFTER_S, delete_media, item)
            await self.on_change()
