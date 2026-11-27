"""Minimal Discord REST client: list a forum's threads, create a forum post.

REST only, no gateway: the bot runs for a minute on a schedule and never listens for events.
Errors carry the HTTP status and nothing else — a Discord error body can echo the message
content, and the Actions log is public.
"""

from __future__ import annotations

import time

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/lapanti/kokousbotti, 1.0)"
MAX_ATTEMPTS = 4


class DiscordError(Exception):
    def __init__(self, status: int):
        super().__init__(f"Discord HTTP {status}")
        self.status = status


class Discord:
    def __init__(self, token: str, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        })

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        for _ in range(MAX_ATTEMPTS):
            resp = self._session.request(method, API + path, timeout=30, **kwargs)
            if resp.status_code == 429:
                try:
                    wait = float(resp.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                time.sleep(min(wait, 30))
                continue
            if resp.status_code >= 400:
                raise DiscordError(resp.status_code)
            return resp.json() if resp.content else {}
        raise DiscordError(429)

    def thread_names(self, guild_id: str, forum_id: str) -> set[str]:
        """Names of the forum's active threads and its most recent archived ones.

        Posts are made for meetings dated today or later, so a thread that already covers one
        is at most a few weeks old: the first archived page (100 threads) is enough."""
        names = set()
        active = self._request("GET", f"/guilds/{guild_id}/threads/active")
        for t in active.get("threads", []):
            if t.get("parent_id") == forum_id:
                names.add(t.get("name"))
        archived = self._request("GET", f"/channels/{forum_id}/threads/archived/public",
                                 params={"limit": 100})
        for t in archived.get("threads", []):
            names.add(t.get("name"))
        return names

    def forum_tags(self, forum_id: str) -> list[dict]:
        """The forum's available tags as `{id, name}` — for filling in the config locally."""
        channel = self._request("GET", f"/channels/{forum_id}")
        return [{"id": t["id"], "name": t["name"]} for t in channel.get("available_tags", [])]

    def create_forum_post(self, forum_id: str, name: str, message: dict, tag: str) -> str:
        """Create a forum post (a thread with a starter message); returns the thread id."""
        body = {"name": name, "message": message, "applied_tags": [tag]}
        out = self._request("POST", f"/channels/{forum_id}/threads", json=body)
        return str(out.get("id"))
