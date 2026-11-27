"""Case numbers (diaarinumero): the portal's own identity for a case across its handlings.

The item header names the case as "<Body> DD.MM.YYYY § N 604/12.05.02/2026" (number /
tehtäväluokka / year). The same number recurs every time the case is handled, in any body, so
it links a new agenda item to its earlier handlings exactly. Routine items (laillisuus,
tarkastajat) and most permits carry none.

kuntapaatokset does not store the number. The bot reads it from the full item page when it
crawls a new meeting (`upcoming`), and otherwise from the item's title and sections. The
sections omit the page header, so they find the number for about half the items the full page
would (Kirkkonummi, 4 429 items: 1 133 from sections vs 2 306 from the page).
"""

from __future__ import annotations

import json
import re

_CASE_NO = re.compile(
    r"(?<![\d/])(\d{1,5}/\d{2}\.\d{2}\.\d{2}/\d{4})(?![\d/])|\b(KIRDno-\d{4}-\d+)\b"
)


def extract_case_no(text: str | None) -> str | None:
    """The first diaarinumero in `text`, or None. The first match is the item's own case: the
    rare second number is a referenced case further down the text."""
    m = _CASE_NO.search(text or "")
    return (m.group(1) or m.group(2)) if m else None


def case_no_of(item: dict) -> str | None:
    """`item["case_no"]` when set (a page-level read), else the first number in the title and
    sections."""
    if item.get("case_no"):
        return item["case_no"]
    sections = item.get("sections") or {}
    return extract_case_no(" ".join([item.get("title") or "", json.dumps(sections, ensure_ascii=False)]))
