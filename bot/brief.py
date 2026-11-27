"""Deterministic pre-meeting brief for one agenda. Offline.

Feeds the Discord meeting-thread bot (`python -m bot run --dry-run --meeting <id>` prints
it). Everything here is computed, not judged:

  - history: earlier handlings (`related.related_items`) with the outcome read from the
    decision text and raw vote totals.
  - theme: `issues.classify_theme`, a machine suggestion, plus how often this body's items on
    that theme went to a roll call in the current term (x/n).
  - contested?: a heuristic flag — an earlier handling had a roll call, or the theme is
    often voted on in this body. Named as a heuristic wherever it is shown.
  - amounts: the largest euro figure the proposal or its explanation mentions. Figures are
    not summed: a text repeats and splits the same money.
  - alignment: how often each party landed on the same side as `focus` in this body's roll
    calls this term. Same-side is framing-independent: it needs no reading of what JAA meant.
  - initiatives and dissents on the same case, or on the same theme this term.

Honesty constraint: no "for"/"against" wording anywhere; small-n shares carry
`low_n` (rendered †), parser-flagged votes carry `suspect` (rendered ⚠) and are left out of
the alignment shares; every row carries its permalink.
"""

from __future__ import annotations

import re
from collections import defaultdict

from kuntapaatokset.analysis import MIN_AGREE_N, _side
from kuntapaatokset.config import Municipality
from kuntapaatokset.issues import classify_theme, norm_party
from kuntapaatokset.parse import parse_dissents
from .caseno import case_no_of
from .related import Index, build_index, is_routine, related_items

# A theme counts as often voted in a body when at least this many of its items this term
# went to a roll call, out of at least THEME_MIN_N items.
THEME_VOTED_SHARE = 0.15
THEME_MIN_N = 5

_EURO_RE = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[  .]\d{3})+|\d+)(?:,(\d+))?\s*"
    r"(milj(?:\.|oonaa)?|M|t|tuhatta)?\s*(?:€|euroa|euro\b|eur\b)",
    re.I,
)
_BODY_WORD_RE = re.compile(r"\w*(?:lautakun|valtuust|hallitu|jaost|neuvost|nämnd)\w*", re.I)
_SCALE = {"milj": 1e6, "milj.": 1e6, "miljoonaa": 1e6, "m": 1e6, "t": 1e3, "tuhatta": 1e3}


def euro_amounts(text: str | None) -> list[float]:
    """Every euro figure in `text`, as numbers."""
    out = []
    for m in _EURO_RE.finditer(text or ""):
        whole = re.sub(r"[  .]", "", m.group(1))
        value = float(f"{whole}.{m.group(2)}" if m.group(2) else whole)
        out.append(value * _SCALE.get((m.group(3) or "").lower(), 1))
    return out


def _item_text(item: dict) -> str:
    secs = item.get("sections") or {}
    return " ".join(str(secs.get(k) or "") for k in ("paatosehdotus", "selostus", "paatos"))


def _theme(title: str | None, text: str | None, body: str | None) -> str:
    """`classify_theme` with the body's own name removed from the text. Decisions open with
    the body's name, and "Sivistys- ja hyvinvointilautakunta päätti ..." would otherwise tag
    every item of that body as sote/hyvinvointi."""
    if body and text:
        text = text.replace(body, " ")
    # Inflected body names too: "hyvinvointilautakunnan", "nuorisovaltuuston".
    strip = lambda s: _BODY_WORD_RE.sub(" ", s or "")  # noqa: E731
    return classify_theme(strip(title), strip(text))


def _vote_sides(vote: dict) -> dict[str, str]:
    """{party: JAA|EI} from the stored per-party breakdown; tied or blank-only parties omitted."""
    by_party: dict[str, dict[str, int]] = defaultdict(lambda: {"jaa": 0, "ei": 0, "tyhja": 0})
    for outcome in ("jaa", "ei", "tyhja"):
        for party, n in ((vote.get("parties") or {}).get(outcome) or {}).items():
            by_party[norm_party(party)][outcome] += n
    sides = {}
    for party, counts in by_party.items():
        side = _side(counts)
        if side and party != "tuntematon":
            sides[party] = side
    return sides


def _term_entries(muni: Municipality, idx: Index, body_slug: str, date: str) -> list[dict]:
    """This body's minutes items in the council term of `date`, before `date`."""
    term = muni.term_for_date(date)
    if not term:
        return []
    return [
        e for e in idx.entries
        if e["body_slug"] == body_slug and e["doctype"] == "Pöytäkirja"
        and e["date"] < date and muni.term_for_date(e["date"]) == term
    ]


def _alignment(entries: list[dict], focus: str) -> dict:
    same: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    n_votes = n_suspect = 0
    for e in entries:
        for vote in e["item"].get("votes") or []:
            n_votes += 1
            if vote.get("suspect"):
                n_suspect += 1
                continue
            sides = _vote_sides(vote)
            if focus not in sides:
                continue
            for party, side in sides.items():
                if party == focus:
                    continue
                total[party] += 1
                same[party] += side == sides[focus]
    rows = [
        {"party": p, "same": same[p], "total": total[p], "rate": round(same[p] / total[p], 3),
         "low_n": total[p] < MIN_AGREE_N}
        for p in sorted(total, key=lambda p: (-same[p] / total[p], p))
    ]
    return {"focus": focus, "roll_calls": n_votes, "suspect_excluded": n_suspect, "rows": rows}


def _theme_stats(entries: list[dict]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "voted": 0})
    for e in entries:
        item = e["item"]
        if is_routine(item.get("title")):
            continue
        theme = _theme(item.get("title"), item.get("decision"), e["body"])
        stats[theme]["n"] += 1
        stats[theme]["voted"] += bool(item.get("votes"))
    return stats


def _initiative_row(e: dict) -> dict:
    ini = e["item"]["initiative"]
    return {
        "ref": ini.get("ref"), "party": norm_party(ini["party"]) if ini.get("party") else None,
        "date": e["date"], "title": e["item"].get("title"), "url": e["item"].get("url"),
    }


def analyze_meeting(
    muni: Municipality,
    meeting: dict,
    body_slug: str,
    *,
    idx: Index | None = None,
    focus: str,
) -> dict:
    """The brief for one meeting (normally an Esityslista). `body_slug` is the directory the
    meeting lives in under data/<muni>/meetings/."""
    idx = idx or build_index(muni)
    date = meeting.get("date") or ""
    term_entries = _term_entries(muni, idx, body_slug, date)
    theme_stats = _theme_stats(term_entries)

    items = []
    item_themes = set()
    for item in meeting.get("items", []):
        case = case_no_of(item)
        if case:
            item["case_no"] = case
        routine = is_routine(item.get("title"))
        secs = item.get("sections") or {}
        theme = _theme(item.get("title"), secs.get("paatosehdotus") or item.get("decision"),
                       meeting.get("body"))
        stats = theme_stats.get(theme, {"n": 0, "voted": 0})
        history = related_items(idx, item, meeting)
        amounts = euro_amounts(_item_text(item))
        dissents = []
        for h in history:
            if h["match"] != "case_no":
                continue
            for e in idx.by_case.get(item.get("case_no") or "", []):
                if e["item"].get("url") == h["url"]:
                    text = " ".join(str(v) for v in (e["item"].get("sections") or {}).values())
                    for d in parse_dissents(text):
                        dissents.append({"date": h["date"], "body": h["body"], "url": h["url"],
                                         "parties": d["parties"]})
        often_voted = stats["n"] >= THEME_MIN_N and stats["voted"] / stats["n"] >= THEME_VOTED_SHARE
        contested = not routine and (any(h["votes"] for h in history) or bool(dissents) or often_voted)
        if not routine:
            item_themes.add(theme)
        items.append({
            "id": item.get("id"),
            "ordinal": item.get("ordinal"),
            "title": item.get("title"),
            "url": item.get("url"),
            "routine": routine,
            "case_no": item.get("case_no"),
            "valmistelija": secs.get("valmistelija"),
            "attachments": len(item.get("attachments") or []),
            "theme": theme,
            "theme_suggested": True,
            "theme_voted": stats["voted"],
            "theme_n": stats["n"],
            "contested_heuristic": contested,
            "max_amount": max(amounts) if amounts else None,
            "history": history,
            "dissents": dissents,
        })

    # Initiatives: same case as an agenda item (any time), or same theme this term.
    cases = {i["case_no"] for i in items if i["case_no"]}
    term = muni.term_for_date(date)
    initiatives, seen = [], set()
    for e in idx.entries:
        ini = e["item"].get("initiative")
        if not ini or e["date"] >= date:
            continue
        same_case = e["item"].get("case_no") in cases
        same_theme = (
            term is not None and muni.term_for_date(e["date"]) == term
            and classify_theme(e["item"].get("title"), None) in item_themes
        )
        key = ini.get("ref") or e["item"].get("url")
        if (same_case or same_theme) and key not in seen:
            seen.add(key)
            initiatives.append({**_initiative_row(e), "match": "case_no" if same_case else "theme"})
    # Same-case first, newest first within each group.
    initiatives.sort(key=lambda r: r["date"], reverse=True)
    initiatives.sort(key=lambda r: r["match"] != "case_no")

    return {
        "meeting": {
            "id": meeting.get("id"), "body": meeting.get("body"), "body_slug": body_slug,
            "date": date, "doctype": meeting.get("doctype"), "url": meeting.get("url"),
        },
        "term": term.election if term else None,
        "items": items,
        "alignment": _alignment(term_entries, focus),
        "initiatives": initiatives,
    }
