"""Earlier handlings of an agenda item, from the crawled record. Offline.

Two match kinds, kept apart so a reader can weigh them:

  - `case_no`: the same diaarinumero. Exact — the portal's own case identity.
  - `title`: a normalized-title near match, used only when the item has no case number or the
    number has no earlier handling. Machine-matched, labelled as such.

Honesty constraint from CLAUDE.md: a history row carries the outcome read from the decision
text and raw JAA/EI/TYHJÄ totals, never a "for"/"against" label, and every row carries its
permalink.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kuntapaatokset.config import Municipality
from kuntapaatokset.crawl import DATA_ROOT

from .caseno import case_no_of

# Items every meeting has. They share generic titles and, for "ajankohtaiset asiat", a
# recurring case number, so linking them would fill the history with noise.
ROUTINE_RE = re.compile(
    r"laillisuu|päätösvaltaisuu|pöytäkirjan tarkast|tarkastajien valin|ilmoitusasi|"
    r"ajankohtai|tiedoksi saatet|viranhaltijapäätösten|viranhaltijoiden päätö|"
    r"otto-oike|muut asiat|kokouksen avaa|kokouksen päättä|työjärjestyks|"
    r"laglighet|beslutför|protokolljuster|anmälningsärenden|aktuella ärenden",
    re.I,
)

# Outcome of one handling, read from the body's own decision text — not from a vote, so it
# needs no äänestysjärjestys. Order matters: a returned or tabled item also "päätti".
_OUTCOMES: list[tuple[str, re.Pattern]] = [
    ("palautettu", re.compile(r"palautt\w* asian|palautt\w* valmisteluun|återremitt", re.I)),
    ("pöydälle", re.compile(r"pöydälle|bordlä", re.I)),
    ("muutettuna", re.compile(r"muutoksin|muutettuna|seuraavin lisäyksin|lisäyksin|med ändring", re.I)),
    ("hyväksytty ehdotuksen mukaan", re.compile(
        r"(?:päätös)?ehdotuksen mukaisesti|hyväksy\w* (?:päätös)?ehdotuksen|enligt förslaget|"
        r"godkän\w* förslaget|päätti yksimielisesti ehdotuksen|merkitsi \w* ?tiedoksi|antecknade",
        re.I)),
]
UNKNOWN_OUTCOME = "tuntematon"

TITLE_JACCARD_MIN = 0.6


def classify_outcome(decision: str | None) -> str:
    """How the body disposed of the item, from its decision text. `tuntematon` when the text
    matches no known formula (including agendas, which have no decision)."""
    head = (decision or "")[:400]
    for name, rx in _OUTCOMES:
        if rx.search(head):
            return name
    return UNKNOWN_OUTCOME


def is_routine(title: str | None) -> bool:
    return bool(ROUTINE_RE.search(title or ""))


def _title_tokens(title: str | None) -> frozenset[str]:
    words = re.findall(r"[a-zåäö]+", (title or "").lower())
    return frozenset(w for w in words if len(w) > 2)


@dataclass
class Index:
    """Every crawled item, keyed for history lookups. Build once per run."""

    entries: list[dict] = field(default_factory=list)
    by_case: dict[str, list[dict]] = field(default_factory=dict)


def build_index(muni: Municipality) -> Index:
    idx = Index()
    for path in sorted((DATA_ROOT / muni.slug / "meetings").glob("*/*.json")):
        meeting = json.loads(path.read_text(encoding="utf-8"))
        for item in meeting.get("items", []):
            entry = {
                "meeting_id": meeting.get("id") or path.stem,
                "body": meeting.get("body"),
                "body_slug": path.parent.name,
                "date": meeting.get("date") or "",
                "doctype": meeting.get("doctype"),
                "item": item,
                "tokens": _title_tokens(item.get("title")),
            }
            idx.entries.append(entry)
            case = case_no_of(item)
            if case:
                item["case_no"] = case  # in memory only; kuntapaatokset does not store it
                idx.by_case.setdefault(case, []).append(entry)
    return idx


def history_row(entry: dict, match: str) -> dict:
    item = entry["item"]
    votes = [
        {
            "totals": v.get("totals") or {o: len(v.get(o) or []) for o in ("jaa", "ei", "tyhja")},
            "suspect": bool(v.get("suspect")),
        }
        for v in item.get("votes") or []
    ]
    return {
        "date": entry["date"],
        "body": entry["body"],
        "body_slug": entry["body_slug"],
        "ordinal": item.get("ordinal"),
        "title": item.get("title"),
        "url": item.get("url"),
        "doctype": entry["doctype"],
        "outcome": classify_outcome(item.get("decision")) if entry["doctype"] == "Pöytäkirja" else None,
        "votes": votes,
        "match": match,
    }


def related_items(idx: Index, item: dict, meeting: dict, limit: int = 5) -> list[dict]:
    """Earlier handlings of `item`, newest first. Empty for routine items."""
    if is_routine(item.get("title")):
        return []
    date = meeting.get("date") or ""
    mid = meeting.get("id")

    def earlier(e: dict) -> bool:
        return e["meeting_id"] != mid and e["date"] < date and e["doctype"] == "Pöytäkirja"

    rows: list[dict] = []
    case = case_no_of(item)
    if case:
        rows = [history_row(e, "case_no") for e in idx.by_case.get(case, []) if earlier(e)]
    if not rows:
        tokens = _title_tokens(item.get("title"))
        if len(tokens) >= 2:
            for e in idx.entries:
                if not earlier(e) or not e["tokens"] or is_routine(e["item"].get("title")):
                    continue
                jaccard = len(tokens & e["tokens"]) / len(tokens | e["tokens"])
                if jaccard >= TITLE_JACCARD_MIN:
                    rows.append(history_row(e, "title"))
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]
