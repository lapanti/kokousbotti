"""Turn a kuntapaatokset brief into one Discord forum post.

The whole brief fits in the starter message. Discord caps it at 2000 characters of `content`
and 6000 characters across all embeds (4096 per embed description), so `fit` sheds detail
deterministically: the lowest-signal items lose detail first, then drop out into a
"+N pykälää esityslistalla" line. Items are always shown in agenda order.

Wording follows kuntapaatokset's honesty rules: raw JAA/EI/TYHJÄ totals, never "for"/"against";
the contested flag is labelled a heuristic; † marks small n and ⚠ a parser-flagged vote.
"""

from __future__ import annotations

import re

CONTENT_MAX = 2000
DESCRIPTION_MAX = 4096
EMBEDS_TOTAL_MAX = 6000
SAFETY = 50  # headroom for Discord's own counting of multi-byte characters
TITLE_MAX = 90
SHORT_TOP = 5
BIG_AMOUNT = 100_000

WEEKDAYS = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]

FOOTER = (
    "Automaattinen koonti kuntapaatokset-datasta. Äänestysluvut ovat raakoja: JAA:n merkitys "
    "riippuu äänestysjärjestyksestä. kiistanalainen? on heuristiikka · † pieni n · "
    "⚠ jäsentäjän epäilyttäväksi merkitsemä äänestys"
)


def fi_date(iso: str | None) -> str:
    if not iso:
        return "?"
    y, m, d = iso.split("-")
    return f"{int(d)}.{int(m)}.{y}"


def weekday(iso: str) -> str:
    import datetime

    return WEEKDAYS[datetime.date.fromisoformat(iso).weekday()]


def eur(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " M€"
    return f"{int(round(value)):,}".replace(",", " ") + " €"


def _clean(title: str | None, limit: int = TITLE_MAX) -> str:
    text = (title or "").replace("[", "(").replace("]", ")").replace("*", "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _link(text: str, url: str | None, *, no_preview: bool = False) -> str:
    if not url:
        return text
    return f"[{text}](<{url}>)" if no_preview else f"[{text}]({url})"


# Genitive of a body name's head word, for "<Toimielimen> kokous d.m.yyyy" — the naming the
# group already uses for hand-made threads. Only the last word inflects:
# "Rakennus- ja ympäristölautakunta" -> "Rakennus- ja ympäristölautakunnan".
_GENITIVE = [
    ("kunta", "kunnan"),        # lautakunta, johtokunta, toimikunta
    ("hallitus", "hallituksen"),
    ("valtuusto", "valtuuston"),
    ("jaosto", "jaoston"),
    ("neuvosto", "neuvoston"),
]


def genitive(body: str) -> str | None:
    """The body name in the genitive, or None when the head word is not a known form
    (Swedish names, for instance) — those need `thread_name` in the config."""
    for nominative, gen in _GENITIVE:
        if body.lower().endswith(nominative):
            return body[: -len(nominative)] + gen
    return None


def thread_name(brief: dict, prefix: str | None = None) -> str:
    """`<prefix> d.m.yyyy`; the prefix defaults to "<genitive> kokous"."""
    m = brief["meeting"]
    if not prefix:
        gen = genitive(m["body"] or "")
        prefix = f"{gen} kokous" if gen else f"{m['body']} – kokous"
    return f"{prefix} {fi_date(m['date'])}"[:100]


_DATE_IN_NAME = re.compile(r"\b0?(\d{1,2})\.0?(\d{1,2})\.(?:\d{4})?")


def dedupe_key(name: str | None) -> str:
    """A thread name compared loosely: case, spacing, the year and leading zeros ignored,
    so a hand-made "Kunnanhallituksen kokous 6.10." matches the bot's "… 6.10.2026"."""
    text = " ".join((name or "").lower().split())
    return _DATE_IN_NAME.sub(lambda m: f"{int(m.group(1))}.{int(m.group(2))}", text).rstrip(". ")


def priority(item: dict) -> int:
    return (
        4 * bool(item.get("contested_heuristic"))
        + 2 * bool((item.get("max_amount") or 0) >= BIG_AMOUNT)
        + bool(item.get("history"))
    )


def _totals(vote: dict) -> str:
    t = vote.get("totals") or {}
    flag = " ⚠" if vote.get("suspect") else ""
    return f"JAA {t.get('jaa', 0)} / EI {t.get('ei', 0)} / TYHJÄ {t.get('tyhja', 0)}{flag}"


def _history_parts(item: dict, level: int) -> list[str]:
    history = item.get("history") or []
    if not history:
        return []
    latest = history[0]
    # A diaarinumero can cover a whole class of matters (every resignation, every etuosto
    # decision of a year), so the line says what matched rather than claiming the same matter.
    label = " (otsikko-osuma)" if latest["match"] == "title" else " samalla diaarinumerolla"
    head = f"{len(history)} aiempaa käsittelyä{label}"
    if level < 3:
        return [head]
    head += f": {latest['body']} {fi_date(latest['date'])}"
    if latest.get("outcome") and latest["outcome"] != "tuntematon":
        head += f" – {latest['outcome']}"
    parts = [head]
    voted = next((h for h in history if h.get("votes")), None)
    if voted:
        parts.append(f"äänestys {fi_date(voted['date'])}: {_totals(voted['votes'][0])}")
    for d in (item.get("dissents") or [])[:1]:
        who = ", ".join(d.get("parties") or []) or "puolue ei tiedossa"
        parts.append(f"eriävä mielipide {fi_date(d['date'])} ({who})")
    return parts


def item_line(item: dict, level: int) -> str:
    """One agenda item at a detail level: 3 full, 2 amount + history count, 1 flag, 0 bare."""
    line = f"**§{item['ordinal']}** " + _link(_clean(item.get("title")), item.get("url"))
    parts: list[str] = []
    if level >= 2 and item.get("max_amount"):
        parts.append(f"suurin mainittu summa {eur(item['max_amount'])}")
    if level >= 2:
        parts.extend(_history_parts(item, level))
    if level >= 3 and item.get("theme_voted") and item.get("theme") != "luokittelematon":
        parts.append(f"{item['theme']}: {item['theme_voted']}/{item['theme_n']} äänestetty tällä kaudella")
    if level >= 1 and item.get("contested_heuristic"):
        parts.append("kiistanalainen?")
    return line + ("\n  " + " · ".join(parts) if parts else "")


def _routine_line(items: list[dict]) -> str | None:
    routine = [i for i in items if i.get("routine")]
    if not routine:
        return None
    return "Rutiiniasiat: " + ", ".join(
        _link(f"§{i['ordinal']}", i.get("url")) for i in routine
    )


def _background(brief: dict) -> str:
    lines = []
    a = brief.get("alignment") or {}
    rows = a.get("rows") or []
    if rows and not all(r["low_n"] for r in rows):
        shares = ", ".join(
            f"{r['party']} {round(100 * r['rate'])} % ({r['same']}/{r['total']}){'†' if r['low_n'] else ''}"
            for r in rows
        )
        line = (f"**Samalla puolella kuin {a['focus']}** tässä toimielimessä tällä kaudella "
                f"({a['roll_calls']} äänestystä): {shares}")
        if a.get("suspect_excluded"):
            line += f" · {a['suspect_excluded']} ⚠-äänestystä jätetty pois"
        lines.append(line)
    inits = brief.get("initiatives") or []
    same_case = [i for i in inits if i["match"] == "case_no"][:5]
    same_theme = [i for i in inits if i["match"] == "theme"][:3]
    for label, group in (("Aloitteet samasta asiasta", same_case),
                         ("Aloitteita samasta teemasta tällä kaudella", same_theme)):
        if group:
            lines.append(f"**{label}:**")
            for i in group:
                party = i.get("party") or "puolue ei tiedossa"
                lines.append(f"• {_link(_clean(i.get('title'), 80), i.get('url'))} – {party}, {fi_date(i['date'])}")
    return "\n".join(lines)


def fit(items: list[dict], budget: int) -> str:
    """Agenda-order item lines within `budget` characters, shedding detail lowest-signal first."""
    body = [i for i in items if not i.get("routine")]
    level = {i["id"]: 3 for i in body}
    dropped: set[str] = set()
    # Shed order: lowest priority first, later items before earlier ones on a tie.
    order = sorted(body, key=lambda i: (priority(i), -int(i["ordinal"] or 0)))

    def render() -> str:
        lines = [item_line(i, level[i["id"]]) for i in body if i["id"] not in dropped]
        routine = _routine_line(items)
        if routine:
            lines.append(routine)
        if dropped:
            lines.append(f"+{len(dropped)} pykälää esityslistalla")
        return "\n".join(lines)

    text = render()
    while len(text) > budget:
        victim = next((i for i in order if i["id"] not in dropped and level[i["id"]] > 0), None)
        if victim:
            level[victim["id"]] -= 1
        else:
            victim = next((i for i in order if i["id"] not in dropped), None)
            if victim is None:
                return text[: budget - 1] + "…"
            dropped.add(victim["id"])
        text = render()
    return text


def full_message(brief: dict, members: tuple[str, ...]) -> dict:
    m = brief["meeting"]
    n = len(brief.get("items") or [])
    content = (
        f"**{m['body']}** – {weekday(m['date'])} {fi_date(m['date'])}\n"
        f"{_link('Esityslista', m.get('url'), no_preview=True)} · {n} pykälää"
    )
    if members:
        content += "\n" + " ".join(f"<@{u}>" for u in members)
    content = content[:CONTENT_MAX]

    background = _background(brief)[:1800]
    title = "Pykälät"
    # Everything counts toward the 6000 total: both titles, both descriptions, the footer.
    budget = min(
        DESCRIPTION_MAX,
        EMBEDS_TOTAL_MAX - SAFETY - len(title) - len("Tausta") - len(background) - len(FOOTER),
    )
    embeds = [{"title": title, "description": fit(brief.get("items") or [], budget)}]
    if background:
        embeds.append({"title": "Tausta", "description": background, "footer": {"text": FOOTER}})
    else:
        embeds[0]["footer"] = {"text": FOOTER}
    return {
        "content": content,
        "embeds": embeds,
        "allowed_mentions": {"parse": [], "users": list(members)},
    }


def short_message(brief: dict) -> dict:
    """A few lines for a body where the focus party has no member: no mentions, no embeds."""
    m = brief["meeting"]
    items = [i for i in brief.get("items") or [] if not i.get("routine")]
    top = sorted(items, key=lambda i: (-priority(i), int(i["ordinal"] or 0)))[:SHORT_TOP]
    shown = [i for i in items if i in top]  # agenda order
    head = (f"**{m['body']}** – {weekday(m['date'])} {fi_date(m['date'])} · "
            f"{_link('Esityslista', m.get('url'), no_preview=True)}")
    lines = [head]
    for i in shown:
        parts = []
        if i.get("max_amount"):
            parts.append(eur(i["max_amount"]))
        if i.get("contested_heuristic"):
            parts.append("kiistanalainen?")
        lines.append(f"§{i['ordinal']} " + _link(_clean(i.get("title"), 80), i.get("url"), no_preview=True)
                     + (" · " + " · ".join(parts) if parts else ""))
    rest = len(brief.get("items") or []) - len(shown)
    if rest:
        lines.append(f"+{rest} muuta pykälää")
    content = "\n".join(lines)
    while len(content) > CONTENT_MAX and len(lines) > 2:
        lines.pop(-2)
        content = "\n".join(lines)
    return {"content": content[:CONTENT_MAX], "allowed_mentions": {"parse": []}}


def message(brief: dict, mode: str, members: tuple[str, ...]) -> dict:
    return full_message(brief, members) if mode == "full" else short_message(brief)


def embed_chars(msg: dict) -> int:
    total = 0
    for e in msg.get("embeds") or []:
        total += len(e.get("title", "")) + len(e.get("description", ""))
        total += len((e.get("footer") or {}).get("text", ""))
    return total
