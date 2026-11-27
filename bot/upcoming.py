"""New meetings of the followed bodies, crawled through the public kuntapaatokset package.

Built for a fresh CI runner: `run` gives the Fetcher an empty temporary cache, so every page
read here is a live fetch, and a portal update since an earlier crawl is always seen. History is
not crawled. A meeting id not on disk costs one meeting-page fetch to learn its date. Only a
meeting dated `since` or later has its items crawled. Meetings already on disk are read from disk.
"""

from __future__ import annotations

import json

from kuntapaatokset.config import Municipality
from kuntapaatokset.crawl import DATA_ROOT, _slug, crawl_meeting, engine_for
from kuntapaatokset.http import Fetcher

from .caseno import extract_case_no


def _set_case_nos(muni: Municipality, fetcher: Fetcher, meeting: dict) -> None:
    """Read each item's diaarinumero from its full page, already in the fetcher's cache."""
    engine = engine_for(muni)
    for item in meeting.get("items", []):
        try:
            sources = engine.item_sources(muni, item, fetcher.get, fetcher.get_bytes)
            text = engine.parse_item(muni, sources, meeting).get("text") if sources else None
        except Exception:  # noqa: BLE001 — a missing number only weakens history matching
            continue
        case = extract_case_no(text)
        if case:
            item["case_no"] = case


def upcoming_meetings(
    muni: Municipality,
    fetcher: Fetcher,
    since: str,
    *,
    body_ids: list[str] | None = None,
) -> list[dict]:
    """Every meeting dated `since` (ISO) or later, as `{body_slug, path, meeting}`,
    newest-first per body."""
    bodies = json.loads((DATA_ROOT / muni.slug / "bodies.json").read_text(encoding="utf-8"))
    if body_ids is not None:
        bodies = [b for b in bodies if b["id"] in body_ids]
    engine = engine_for(muni)
    root = DATA_ROOT / muni.slug / "meetings"
    on_disk = {p.stem: p for p in root.glob("*/*.json")}
    out: list[dict] = []
    for body in bodies:
        info = engine.body_page(muni, fetcher, body["id"])
        for mid in info["meeting_ids"]:
            path = on_disk.get(mid)
            if path:
                meeting = json.loads(path.read_text(encoding="utf-8"))
            else:
                head = engine.meeting(muni, fetcher, mid)
                if not head.get("date") or head["date"] < since:
                    # The list is newest-first: the first past meeting ends this body's new
                    # ones, and everything after it is history the dataset owns.
                    break
                meeting = crawl_meeting(muni, fetcher, mid, body=info["body"])
                _set_case_nos(muni, fetcher, meeting)
                path = root / _slug(info["body"] or meeting.get("body") or "unknown") / f"{mid}.json"
            if (meeting.get("date") or "") < since:
                break
            out.append({"body_slug": path.parent.name, "path": str(path), "meeting": meeting})
    return out
