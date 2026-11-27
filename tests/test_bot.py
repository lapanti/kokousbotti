"""Bot tests: Discord limits, modes, dedupe, missing tags, and that the public log leaks nothing.

Run: PYTHONPATH=../kuntapaatokset python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import types
import unittest
from unittest import mock

from bot import __main__ as botmain
from bot import render
from bot.config import ConfigError, parse

MEMBER = "123456789012345678"
TAG = "223456789012345678"
SECRET_TITLE = "Salainen testiotsikko Masalan hoivakoti"

CFG = json.dumps({
    "municipality": "kirkkonummi", "focus": "vihr",
    "guild_id": "900000000000000001", "forum_id": "900000000000000002",
    "bodies": {
        "kunnanhallitus": {"tag": TAG, "mode": "full", "members": [MEMBER]},
        "vammaisneuvosto": {"mode": "short"},
    },
})


def _item(n: int, **kw) -> dict:
    base = {
        "id": f"1-{n}", "ordinal": str(n), "title": f"{SECRET_TITLE} {n} " + "pitkä otsikko " * 5,
        "url": f"https://example.invalid/item/{n}", "routine": False, "case_no": None,
        "theme": "sote/hyvinvointi", "theme_voted": 3, "theme_n": 20,
        "contested_heuristic": n % 3 == 0, "max_amount": 1_250_000.0 if n % 2 else None,
        "history": [{
            "date": "2026-05-01", "body": "Kunnanvaltuusto", "body_slug": "kunnanvaltuusto",
            "ordinal": "4", "title": "x", "url": "https://example.invalid/h", "doctype": "Pöytäkirja",
            "outcome": "palautettu", "match": "case_no",
            "votes": [{"totals": {"jaa": 30, "ei": 20, "tyhja": 1}, "suspect": n == 4}],
        }] * 3,
        "dissents": [{"date": "2026-05-01", "body": "Kunnanvaltuusto", "url": "u", "parties": ["vihr"]}],
    }
    base.update(kw)
    return base


def _brief(n_items: int) -> dict:
    items = [_item(1, routine=True, title="Kokouksen laillisuus")] + [_item(i) for i in range(2, n_items + 1)]
    return {
        "meeting": {"id": "1", "body": "Kunnanhallitus", "body_slug": "kunnanhallitus",
                    "date": "2026-10-05", "doctype": "Esityslista", "url": "https://example.invalid/m"},
        "items": items,
        "alignment": {"focus": "vihr", "roll_calls": 29, "suspect_excluded": 1, "rows": [
            {"party": "vas", "same": 10, "total": 11, "rate": 0.909, "low_n": False},
            {"party": "kd", "same": 1, "total": 2, "rate": 0.5, "low_n": True},
        ]},
        "initiatives": [{"ref": "1/2026", "party": "vihr", "date": "2026-01-01", "title": "Aloite",
                         "url": "https://example.invalid/a", "match": "case_no"}],
    }


class RenderTest(unittest.TestCase):
    def test_full_fits_discord_limits(self):
        for n in (3, 12, 30, 60):
            msg = render.full_message(_brief(n), (MEMBER,))
            self.assertLessEqual(len(msg["content"]), render.CONTENT_MAX)
            self.assertLessEqual(render.embed_chars(msg), render.EMBEDS_TOTAL_MAX)
            for e in msg["embeds"]:
                self.assertLessEqual(len(e["description"]), render.DESCRIPTION_MAX)

    def test_overflow_sheds_detail_then_items(self):
        msg = render.full_message(_brief(60), (MEMBER,))
        self.assertIn("pykälää esityslistalla", msg["embeds"][0]["description"])

    def test_agenda_order_kept(self):
        desc = render.full_message(_brief(8), ())["embeds"][0]["description"]
        ordinals = [int(x) for x in re.findall(r"\*\*§(\d+)\*\*", desc)]
        self.assertEqual(ordinals, sorted(ordinals))

    def test_mentions_only_members(self):
        msg = render.full_message(_brief(3), (MEMBER,))
        self.assertIn(f"<@{MEMBER}>", msg["content"])
        self.assertEqual(msg["allowed_mentions"], {"parse": [], "users": [MEMBER]})

    def test_honest_wording(self):
        text = json.dumps(render.full_message(_brief(12), ()), ensure_ascii=False).lower()
        for banned in ("puolesta", "vastaan", "kannatti", "vastusti"):
            self.assertNotIn(banned, text)
        self.assertIn("heuristiikka", text)
        self.assertIn("jaa 30 / ei 20 / tyhjä 1", text)

    def test_short_has_no_mentions_or_embeds(self):
        msg = render.short_message(_brief(20))
        self.assertNotIn("embeds", msg)
        self.assertNotIn("<@", msg["content"])
        self.assertEqual(msg["allowed_mentions"], {"parse": []})
        self.assertLessEqual(len(re.findall(r"^§", msg["content"], re.M)), render.SHORT_TOP)

    def test_thread_name(self):
        self.assertEqual(render.thread_name(_brief(2)), "Kunnanhallituksen kokous 5.10.2026")
        self.assertEqual(render.thread_name(_brief(2), "Kuntakehityslautakunnan kokous"),
                         "Kuntakehityslautakunnan kokous 5.10.2026")

    def test_genitive(self):
        cases = {
            "Kunnanvaltuusto": "Kunnanvaltuuston",
            "Rakennus- ja ympäristölautakunta": "Rakennus- ja ympäristölautakunnan",
            "Kirkkonummen Vesi -liikelaitoksen johtokunta": "Kirkkonummen Vesi -liikelaitoksen johtokunnan",
            "Henkilöstöjaosto": "Henkilöstöjaoston",
            "Vammaisneuvosto": "Vammaisneuvoston",
            "Svenska nämnden för fostran och utbildning": None,
        }
        for body, want in cases.items():
            self.assertEqual(render.genitive(body), want, body)

    def test_dedupe_key_is_loose(self):
        key = render.dedupe_key("Kuntakehityslautakunnan kokous 6.10.2026")
        for variant in ("kuntakehityslautakunnan kokous 6.10.", "Kuntakehityslautakunnan  kokous 06.10.2026",
                        "Kuntakehityslautakunnan kokous 6.10"):
            self.assertEqual(render.dedupe_key(variant), key, variant)
        self.assertNotEqual(render.dedupe_key("Kuntakehityslautakunnan kokous 7.10.2026"), key)


class ConfigTest(unittest.TestCase):
    def test_parse(self):
        cfg = parse(CFG)
        self.assertEqual(cfg.bodies["kunnanhallitus"].members, (MEMBER,))
        self.assertIsNone(cfg.bodies["vammaisneuvosto"].tag)

    def test_rejects_bad(self):
        for raw in ("{", "{}", json.dumps({"guild_id": 1, "forum_id": 2,
                                          "bodies": {"x": {"tag": 1, "mode": "loud"}}})):
            with self.assertRaises(ConfigError):
                parse(raw)


class FakeDiscord:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.posts = []

    def thread_names(self, guild_id, forum_id):
        return set(self.existing)

    def create_forum_post(self, forum_id, name, message, tag):
        self.posts.append((name, message, tag))
        return "1"


def _found(slug, body, date, mid):
    items = [{"id": f"{mid}-1", "ordinal": "1", "title": SECRET_TITLE, "url": "https://example.invalid/i",
              "sections": {"paatosehdotus": "Lautakunta päättää 2 000 000 euroa."}, "votes": []}]
    return {"body_slug": slug, "path": "x",
            "meeting": {"id": mid, "body": body, "doctype": "Esityslista", "date": date,
                        "url": "https://example.invalid/m", "items": items}}


class RunTest(unittest.TestCase):
    def _run(self, discord, found):
        args = types.SimpleNamespace(dry_run=False, config=None, meeting=None, since="2026-10-01")
        out, err = io.StringIO(), io.StringIO()
        env = {"BOT_CONFIG": CFG, "DISCORD_BOT_TOKEN": "tok-secret-value", "CI": "true"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(botmain, "upcoming_meetings", return_value=found), \
             mock.patch.object(botmain, "followed_body_ids", return_value=["2"]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = botmain.run(args, discord=discord, fetcher=object())
        return code, out.getvalue() + err.getvalue()

    def test_posts_dedupes_and_flags_missing_tag(self):
        found = [
            _found("kunnanhallitus", "Kunnanhallitus", "2026-10-05", "501"),
            _found("kunnanhallitus", "Kunnanhallitus", "2026-10-12", "502"),
            _found("vammaisneuvosto", "Vammaisneuvosto", "2026-10-06", "503"),
        ]
        discord = FakeDiscord(existing={"kunnanhallituksen kokous 12.10."})  # hand-made, no year
        code, log = self._run(discord, found)
        self.assertEqual([p[0] for p in discord.posts], ["Kunnanhallituksen kokous 5.10.2026"])
        self.assertEqual(discord.posts[0][2], TAG)
        self.assertIn("postattu=1", log)
        self.assertIn("jo_olemassa=1", log)
        self.assertIn("tagi_puuttuu=1", log)
        self.assertEqual(code, 1)  # a missing tag fails the run so it shows in Actions

    def test_public_log_leaks_nothing(self):
        found = [_found("kunnanhallitus", "Kunnanhallitus", "2026-10-05", "501")]

        class Boom(FakeDiscord):
            def create_forum_post(self, *a):
                raise RuntimeError(f"failed for {MEMBER} {SECRET_TITLE} tok-secret-value")

        for discord in (FakeDiscord(), Boom()):
            _, log = self._run(discord, found)
            for secret in (MEMBER, TAG, SECRET_TITLE, "tok-secret-value", "example.invalid",
                           "Kunnanhallitus"):
                self.assertNotIn(secret, log)
            self.assertIsNone(re.search(r"\d{17,20}", log))

    def test_network_error_traceback_does_not_reach_log(self):
        import requests

        class Down(FakeDiscord):
            def thread_names(self, guild_id, forum_id):
                raise requests.ConnectionError(f"https://discord.com/api/v10/guilds/{guild_id}/threads/active")

        out = io.StringIO()
        env = {"BOT_CONFIG": CFG, "DISCORD_BOT_TOKEN": "tok-secret-value", "CI": "true"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(botmain, "Discord", lambda token: Down()), \
             mock.patch.object(botmain, "upcoming_meetings", return_value=[]), \
             mock.patch.object(botmain, "Fetcher", lambda **kw: object()), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = botmain.main(["run"])
        self.assertEqual(code, 1)
        self.assertIn("virhe=ConnectionError", out.getvalue())
        self.assertNotIn("900000000000000001", out.getvalue())

    def test_unfollowed_and_unknown_slug_are_counted(self):
        cfg = json.loads(CFG)
        cfg["bodies"]["kunnanhallitu"] = {"tag": TAG, "mode": "short"}  # typo
        # --meeting for a body the config does not follow: counted, not silently dropped.
        from pathlib import Path

        meeting = _found("perusturvalautakunta", "Perusturvalautakunta", "2026-10-05", "601")["meeting"]
        hit = (Path("data/kirkkonummi/meetings/perusturvalautakunta/601.json"), meeting)
        args = types.SimpleNamespace(dry_run=False, config=None, meeting="601", since=None)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"BOT_CONFIG": json.dumps(cfg), "DISCORD_BOT_TOKEN": "t"}), \
             mock.patch.object(botmain, "find_meeting", return_value=hit), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = botmain.run(args, discord=FakeDiscord(), fetcher=object())
        self.assertIn("ei_seurattu=1", out.getvalue())
        self.assertIn("tuntematon_slug=1", out.getvalue())
        self.assertEqual(code, 1)

    def test_dry_run_refused_in_ci(self):
        args = types.SimpleNamespace(dry_run=True, config=None, meeting=None, since=None)
        with mock.patch.dict("os.environ", {"CI": "true", "BOT_CONFIG": CFG}), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(botmain.run(args), 2)


if __name__ == "__main__":
    unittest.main()
