"""Tests for the agenda-brief pipeline: case numbers, history, brief metrics, upcoming crawl.

Run: python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuntapaatokset import config, crawl
from bot.brief import _alignment, _theme, euro_amounts
from bot import upcoming
from bot.caseno import case_no_of, extract_case_no
from bot.related import Index, classify_outcome, is_routine, related_items


class CaseNoTest(unittest.TestCase):
    def test_header_number(self):
        text = "Kunnanhallitus 04.12.2023 § 413 \xa0 807/10.00.02/2023 \xa0 Valmistelija"
        self.assertEqual(extract_case_no(text), "807/10.00.02/2023")

    def test_first_number_wins(self):
        text = "KH 1.1.2024 § 1 12/00.00.00/2024 ... viittaa asiaan 99/02.02.02/2020"
        self.assertEqual(extract_case_no(text), "12/00.00.00/2024")

    def test_kirdno(self):
        self.assertEqual(extract_case_no("Dnro KIRDno-2024-123 x"), "KIRDno-2024-123")

    def test_none(self):
        self.assertIsNone(extract_case_no("Kokouksen laillisuus ja päätösvaltaisuus"))
        self.assertIsNone(extract_case_no(None))


class OutcomeTest(unittest.TestCase):
    def test_outcomes(self):
        cases = {
            "Kunnanhallitus päätti yksimielisesti ehdotuksen mukaisesti": "hyväksytty ehdotuksen mukaan",
            "Kunnanvaltuusto päätti yksimielisesti palauttaa asian uudelleen valmisteltavaksi": "palautettu",
            "Kunnanhallitus päätti yksimielisesti jättää asian pöydälle": "pöydälle",
            "Lautakunta päätti päätösehdotuksen mukaisesti seuraavin muutoksin: Lisäys": "muutettuna",
            "Kunnanvaltuusto toimittaa valtuustoaloitteen kunnanhallitukselle": "tuntematon",
            None: "tuntematon",
        }
        for text, want in cases.items():
            self.assertEqual(classify_outcome(text), want, text)

    def test_routine(self):
        self.assertTrue(is_routine("Kokouksen laillisuus ja päätösvaltaisuus"))
        self.assertTrue(is_routine("Esittelijän ajankohtaiset asiat"))
        self.assertTrue(is_routine("Ajankohtaista toimialoilta"))
        self.assertFalse(is_routine("Oikaisuvaatimus kunnossapitopäällikön viranhaltijapäätöksestä"))
        self.assertFalse(is_routine("Masalan uusi hoivakoti"))


class EuroTest(unittest.TestCase):
    def test_formats(self):
        text = "1,2 milj. euroa, 350 000 euroa, 20 €/h, 3 500 000 € ja 12 t€"
        self.assertEqual(euro_amounts(text), [1_200_000, 350_000, 20, 3_500_000, 12_000])

    def test_none(self):
        self.assertEqual(euro_amounts("ei rahaa"), [])
        self.assertEqual(euro_amounts(None), [])


class ThemeTest(unittest.TestCase):
    def test_body_name_does_not_tag(self):
        body = "Sivistys- ja hyvinvointilautakunta"
        text = "Sivistys- ja hyvinvointilautakunta päättää hyväksyä hinnaston. Hyvinvointilautakunnan"
        self.assertEqual(_theme("Kulttuurisalien hinnasto", text, body), "kulttuuri/vapaa-aika")


def _entry(mid, date, item, doctype="Pöytäkirja", body="Kunnanhallitus", slug="kunnanhallitus"):
    from bot.related import _title_tokens

    return {"meeting_id": mid, "body": body, "body_slug": slug, "date": date, "doctype": doctype,
            "item": item, "tokens": _title_tokens(item.get("title"))}


def _vote(jaa, ei, suspect=False):
    return {"totals": {"jaa": 1, "ei": 1, "tyhja": 0}, "parties": {"jaa": jaa, "ei": ei, "tyhja": {}},
            "suspect": suspect}


class RelatedTest(unittest.TestCase):
    def setUp(self):
        self.idx = Index()
        old = {"id": "1-5", "title": "Koulukuljetusten periaatteet", "case_no": "5/12.00.01/2025",
               "url": "u1", "decision": "Kunnanhallitus päätti yksimielisesti palauttaa asian",
               "votes": [_vote({"vihr": 2}, {"kok": 3})]}
        similar = {"id": "2-3", "title": "Nuorisopalvelut kohdeavustukset 2025", "url": "u2",
                   "decision": "päätti päätösehdotuksen mukaisesti", "votes": []}
        later = {"id": "9-1", "title": "Koulukuljetusten periaatteet", "case_no": "5/12.00.01/2025",
                 "url": "u9", "votes": []}
        for e in (_entry("1", "2025-05-01", old), _entry("2", "2025-06-01", similar),
                  _entry("9", "2027-01-01", later)):
            self.idx.entries.append(e)
            if e["item"].get("case_no"):
                self.idx.by_case.setdefault(e["item"]["case_no"], []).append(e)

    def test_case_match_earlier_only(self):
        item = {"title": "Koulukuljetusten periaatteet", "case_no": "5/12.00.01/2025"}
        rows = related_items(self.idx, item, {"id": "5", "date": "2026-01-01"})
        self.assertEqual([r["url"] for r in rows], ["u1"])
        self.assertEqual(rows[0]["match"], "case_no")
        self.assertEqual(rows[0]["outcome"], "palautettu")
        self.assertEqual(rows[0]["votes"][0]["totals"], {"jaa": 1, "ei": 1, "tyhja": 0})

    def test_title_fallback(self):
        item = {"title": "Nuorisopalvelut kohdeavustukset 2026"}
        rows = related_items(self.idx, item, {"id": "5", "date": "2026-01-01"})
        self.assertEqual([(r["url"], r["match"]) for r in rows], [("u2", "title")])

    def test_routine_has_no_history(self):
        item = {"title": "Kokouksen laillisuus ja päätösvaltaisuus"}
        self.assertEqual(related_items(self.idx, item, {"id": "5", "date": "2026-01-01"}), [])


class AlignmentTest(unittest.TestCase):
    def test_same_side_and_suspect(self):
        entries = [
            _entry("1", "2025-09-01", {"votes": [_vote({"vihr": 2, "sdp": 3}, {"kok": 4})]}),
            _entry("2", "2025-10-01", {"votes": [_vote({"vihr": 2}, {"sdp": 3, "kok": 4})]}),
            _entry("3", "2025-11-01", {"votes": [_vote({"vihr": 2}, {"sdp": 3}, suspect=True)]}),
        ]
        out = _alignment(entries, "vihr")
        self.assertEqual(out["roll_calls"], 3)
        self.assertEqual(out["suspect_excluded"], 1)
        rows = {r["party"]: r for r in out["rows"]}
        self.assertEqual((rows["sdp"]["same"], rows["sdp"]["total"]), (1, 2))
        self.assertEqual((rows["kok"]["same"], rows["kok"]["total"]), (0, 2))
        self.assertTrue(rows["sdp"]["low_n"])


class FakeFetcher:
    """Serves canned pages by URL substring; records every request."""

    def __init__(self, pages: dict[str, str], cache_dir: str = "/nonexistent"):
        self.pages = pages
        self.cache_dir = Path(cache_dir)
        self.calls: list[tuple[str, bool]] = []

    def get_bytes(self, url: str, *, force: bool = False) -> bytes:
        return self.get(url, force=force).encode("utf-8")

    def get(self, url: str, *, force: bool = False) -> str:
        self.calls.append((url, force))
        for key, page in self.pages.items():
            if url.endswith(key):
                return page
        raise AssertionError(f"unexpected fetch {url}")


def _meeting_page(doctype: str, date: str, mid: str) -> str:
    return (f"<html><head><title>Testilautakunta {doctype} {date} klo 17:00 | Dynasty</title>"
            f"</head><body><a href='DREQUEST.PHP?page=meetingitem&id={mid}-1'>Asia</a></body></html>")


class UpcomingTest(unittest.TestCase):
    def test_crawls_only_new_upcoming(self):
        muni = config.load("kirkkonummi")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / muni.slug / "meetings" / "testilautakunta").mkdir(parents=True)
            (root / muni.slug / "bodies.json").write_text(json.dumps([{"id": "77", "name": "Testilautakunta"}]))
            # 300 is on disk and upcoming; 200 is past and not on disk (must not be crawled).
            on_disk = {"id": "300", "body": "Testilautakunta", "doctype": "Esityslista",
                       "date": "2026-10-01", "items": []}
            (root / muni.slug / "meetings" / "testilautakunta" / "300.json").write_text(json.dumps(on_disk))
            fetcher = FakeFetcher({
                "page=meetings&id=77": ("<title>Testilautakunta | x</title>"
                                        "<a href='DREQUEST.PHP?page=meeting&id=400'>"
                                        "<a href='DREQUEST.PHP?page=meeting&id=300'>"
                                        "<a href='DREQUEST.PHP?page=meeting&id=200'>"),
                "page=meeting&id=400": _meeting_page("Esityslista", "08.10.2026", "400"),
                "page=meetingitem&id=400-1": ("<html><title>Asia</title><body>Testilautakunta 08.10.2026 § 1 "
                                              "604/12.05.02/2026 Päätösehdotus x</body></html>"),
                "page=meeting&id=200": _meeting_page("Pöytäkirja", "01.09.2026", "200"),
            })
            fetcher.cache_dir = root / "cache"
            with mock.patch.object(crawl, "DATA_ROOT", root), mock.patch.object(upcoming, "DATA_ROOT", root):
                found = upcoming.upcoming_meetings(muni, fetcher, "2026-09-29")
            self.assertEqual([f["meeting"]["id"] for f in found], ["400", "300"])
            self.assertTrue((root / muni.slug / "meetings" / "testilautakunta" / "400.json").exists())
            fetched = [u for u, _ in fetcher.calls]
            # 200 costs one meeting-page fetch to learn it is past; its items are not crawled.
            self.assertFalse(any("meetingitem&id=200" in u for u in fetched))
            self.assertFalse((root / muni.slug / "meetings" / "testilautakunta" / "200.json").exists())
            new = next(f["meeting"] for f in found if f["meeting"]["id"] == "400")
            self.assertEqual(new["items"][0].get("case_no"), "604/12.05.02/2026")

    def test_case_no_from_sections_when_not_stored(self):
        item = {"title": "Asia", "sections": {"selostus": "Dnro 12/00.00.00/2024 ja muuta"}}
        self.assertEqual(case_no_of(item), "12/00.00.00/2024")
        self.assertEqual(case_no_of({**item, "case_no": "1/01.01.01/2020"}), "1/01.01.01/2020")
        self.assertIsNone(case_no_of({"title": "Kokouksen laillisuus"}))


if __name__ == "__main__":
    unittest.main()
