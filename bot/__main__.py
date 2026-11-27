"""Open a Discord forum post for every new agenda of a followed body.

    python -m bot run                      # CI: needs BOT_CONFIG and DISCORD_BOT_TOKEN
    python -m bot run --dry-run --config local.json [--meeting 20262404]

Needs kuntapaatokset importable (PYTHONPATH pointing at a checkout; its data/ is the history).
Dedupe is against Discord itself: a meeting whose thread name already exists in the forum is
skipped, so the bot keeps no state file and a re-run never double-posts.

All output goes through bot.log — counters only — because the Actions log of this public
repository is public. --dry-run prints full payloads and is refused under CI for that reason.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile

from kuntapaatokset import config as kconfig
from kuntapaatokset.crawl import DATA_ROOT, _slug
from kuntapaatokset.http import Fetcher
from kuntapaatokset.show import find_meeting

from . import log, render
from .brief import analyze_meeting
from .config import ConfigError, from_env
from .discord import Discord, DiscordError
from .related import build_index
from .upcoming import upcoming_meetings


def _bodies(muni) -> list[dict]:
    return json.loads((DATA_ROOT / muni.slug / "bodies.json").read_text(encoding="utf-8"))


def followed_body_ids(muni, cfg) -> list[str]:
    return [b["id"] for b in _bodies(muni) if _slug(b["name"]) in cfg.bodies]


def unknown_slugs(muni, cfg) -> int:
    """Configured slugs that match no body: a typo would otherwise never be polled, silently."""
    known = {_slug(b["name"]) for b in _bodies(muni)}
    return sum(1 for slug in cfg.bodies if slug not in known)


def run(args, *, discord: Discord | None = None, fetcher: Fetcher | None = None) -> int:
    if args.dry_run and os.environ.get("CI"):
        log.error("DryRunInCI")
        return 2
    try:
        cfg = from_env(args.config)
    except ConfigError:
        log.error("ConfigError")
        return 2
    muni = kconfig.load(cfg.municipality)

    if args.meeting:
        hit = find_meeting(muni, args.meeting)
        if not hit:
            log.error("MeetingNotOnDisk")
            return 1
        path, meeting = hit
        found = [{"body_slug": path.parent.name, "meeting": meeting}]
    else:
        since = args.since or datetime.date.today().isoformat()
        fetcher = fetcher or Fetcher(cache_dir=tempfile.mkdtemp(prefix="kokousbotti-"))
        found = upcoming_meetings(muni, fetcher, since, body_ids=followed_body_ids(muni, cfg))
        found = [f for f in found if f["meeting"].get("doctype") == "Esityslista"
                 and f["body_slug"] in cfg.bodies]

    stats = {"uusia": len(found), "postattu": 0, "jo_olemassa": 0, "tagi_puuttuu": 0,
             "ei_seurattu": 0, "tuntematon_slug": unknown_slugs(muni, cfg), "virheita": 0}
    idx = build_index(muni)

    # A dry run reads the forum too when a token is at hand, so it shows what a real run skips.
    token = os.environ.get("DISCORD_BOT_TOKEN")
    existing: set[str] | None = None
    if not args.dry_run or token or discord is not None:
        if not token and discord is None:
            log.error("TokenMissing")
            return 2
        discord = discord or Discord(token)
        try:
            # Compared loosely (render.dedupe_key) so hand-made threads count too.
            existing = {render.dedupe_key(n) for n in discord.thread_names(cfg.guild_id, cfg.forum_id)}
        except DiscordError as e:
            log.error("DiscordError", e.status)
            return 1

    for f in found:
        body = cfg.bodies.get(f["body_slug"])
        if body is None:
            stats["ei_seurattu"] += 1
            continue
        if not body.tag:
            stats["tagi_puuttuu"] += 1
            continue
        try:
            brief = analyze_meeting(muni, f["meeting"], f["body_slug"], idx=idx, focus=cfg.focus)
            name = render.thread_name(brief, body.thread_name)
            key = render.dedupe_key(name)
            msg = render.message(brief, body.mode, body.members)
            if args.dry_run:
                print(json.dumps({"name": name, "tag": body.tag, "message": msg,
                                  "embed_chars": render.embed_chars(msg),
                                  "thread_exists": None if existing is None else key in existing},
                                 ensure_ascii=False, indent=2))
                continue
            if key in existing:
                stats["jo_olemassa"] += 1
                continue
            discord.create_forum_post(cfg.forum_id, name, msg, body.tag)
            existing.add(key)
            stats["postattu"] += 1
        except DiscordError as e:
            stats["virheita"] += 1
            log.error("DiscordError", e.status)
        except Exception as e:  # noqa: BLE001 — logged by type only, the log is public
            stats["virheita"] += 1
            log.error(type(e).__name__)

    log.counts(**stats)
    failed = stats["virheita"] or stats["tagi_puuttuu"] or stats["tuntematon_slug"]
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="post new agendas")
    p_run.add_argument("--dry-run", action="store_true", help="print payloads, post nothing (local only)")
    p_run.add_argument("--config", default=None, help="local config JSON instead of BOT_CONFIG")
    p_run.add_argument("--meeting", default=None,
                       help="post (or with --dry-run, print) this on-disk meeting; for testing")
    p_run.add_argument("--since", default=None, help="ISO date (default: today)")
    p_tags = sub.add_parser("tags", help="list the forum's tag names and ids (local only)")
    p_tags.add_argument("--config", default=None, help="local config JSON instead of BOT_CONFIG")
    args = parser.parse_args(argv)
    # Last line of defence for the public log: a traceback would print exception messages,
    # and a requests error's message carries the URL with the guild or forum id in it.
    try:
        return tags(args) if args.command == "tags" else run(args)
    except Exception as e:  # noqa: BLE001
        log.error(type(e).__name__)
        return 1


def tags(args, *, discord: Discord | None = None) -> int:
    """Print the forum's tags so their ids can go into the config. Refused under CI: the
    output is configuration, and the Actions log is public."""
    if os.environ.get("CI"):
        log.error("TagsInCI")
        return 2
    try:
        cfg = from_env(args.config)
    except ConfigError:
        log.error("ConfigError")
        return 2
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token and discord is None:
        log.error("TokenMissing")
        return 2
    discord = discord or Discord(token)
    try:
        for t in discord.forum_tags(cfg.forum_id):
            print(f"{t['id']}  {t['name']}")
    except DiscordError as e:
        log.error("DiscordError", e.status)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
