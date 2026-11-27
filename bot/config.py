"""Bot configuration, read from the BOT_CONFIG environment variable (a GitHub secret).

Nothing deployment-specific lives in this repository: which municipality, which party, which
Discord server and which bodies all come from configuration. BOT_CONFIG is one line of JSON —
GitHub masks a multi-line secret line by line, which is unreliable — shaped like:

    {"municipality": "<kuntapaatokset config slug>", "guild_id": "…", "forum_id": "…",
     "focus": "<party code>",
     "bodies": {
       "<body-slug>": {"tag": "<forum tag id>", "mode": "full", "members": ["<user id>", …]},
       "<body-slug>": {"tag": "<forum tag id>", "mode": "short"},
       "<body-slug>": {"tag": "…", "mode": "full", "members": […], "thread_name": "<prefix>"}
     }}

Thread names are "<thread_name> d.m.yyyy"; without `thread_name` the prefix is the body name
in the genitive + " kokous" ("Kunnanhallituksen kokous").

`municipality` is a kuntapaatokset `config/municipalities/<slug>.yaml` slug. `focus` is the
party code (as kuntapaatokset normalises it) whose same-side shares the brief shows. Keys of
`bodies` are body slugs, the directory names under kuntapaatokset's data/<muni>/meetings/.
Only listed bodies are followed. `mode` is `full` (the focus party sits on the body: mentions +
the full brief) or `short` (no representation: a few lines).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

MODES = ("full", "short")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class BodyConfig:
    tag: str | None
    mode: str
    members: tuple[str, ...] = ()
    thread_name: str | None = None  # e.g. "Kuntakehityslautakunnan kokous"; date is appended


@dataclass(frozen=True)
class Config:
    municipality: str
    guild_id: str
    forum_id: str
    focus: str
    bodies: dict[str, BodyConfig] = field(default_factory=dict)


def parse(raw: str) -> Config:
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConfigError("BOT_CONFIG is not valid JSON") from None
    try:
        bodies = {}
        for slug, b in data["bodies"].items():
            mode = b.get("mode", "full")
            if mode not in MODES:
                raise ConfigError(f"mode must be one of {MODES}")
            members = tuple(str(m) for m in b.get("members", []))
            if not all(m.isdigit() for m in members):
                raise ConfigError("members must be numeric Discord user ids")
            bodies[slug] = BodyConfig(tag=str(b["tag"]) if b.get("tag") else None, mode=mode,
                                      members=members, thread_name=b.get("thread_name"))
        return Config(municipality=str(data["municipality"]), guild_id=str(data["guild_id"]),
                      forum_id=str(data["forum_id"]), focus=str(data["focus"]), bodies=bodies)
    except (KeyError, TypeError, AttributeError):
        raise ConfigError(
            "BOT_CONFIG is missing municipality, guild_id, forum_id, focus or bodies"
        ) from None


def from_env(path: str | None = None) -> Config:
    """From a local file when `path` is given (local dry runs), else from BOT_CONFIG."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return parse(fh.read())
    raw = os.environ.get("BOT_CONFIG")
    if not raw:
        raise ConfigError("BOT_CONFIG is not set")
    return parse(raw)
