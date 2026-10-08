# -*- coding: utf-8 -*-
"""
Тести бойового режиму (Lead & Mail 1.5.0, рішення власника 08.10.2026).

  * бойовий лист іде лише на адресу самої компанії з Prozorro;
  * усі попередні листи тестові — бойового першого листа вони не займають;
  * обмеження діє лише після бойового листа (у черзі, надісланого,
    з невідомим станом); скасований і невдалий компанію не блокують;
  * подія, яку бачив лише тестовий прогін, у бойовому знову йде в розсилку;
  * відправка клієнтам — одразу, без запитання (рішення власника 08.10.2026);
  * лист V10 — V9 з телефоном «0 800 357 135».

Мережі й Gmail немає. Запуск:  python3 test_live.py  (поруч із test_lead_mail.py)
"""
from __future__ import annotations

import builtins
import contextlib
import io
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tenderwin_lead_mail as M              # noqa: E402
import test_lead_mail as T                   # noqa: E402  фікстури і Base

CLIENT = "office@bud.example"


def t_one(uid="t1", ua="UA-2026-09-01-000001-a", hour=11, **kw):
    """Закупівля з одним відхиленням компанії 12345678 (або іншої з kw)."""
    return T.tender(uid=uid, ua=ua, bids=[T.bid("b1", **kw)],
                    awards=[T.award("a1", "b1", date=T.iso(22, hour),
                                    cp_start=T.iso(22, hour), cp_end=T.iso(27, 0), **kw)])


class LiveBase(T.Base):
    def setUp(self):
        super().setUp()
        M.MODE = "LIVE"

    def letters(self):
        eng = self.engine()
        rows = [dict(r) for r in eng.db.q("SELECT * FROM outreach ORDER BY generated_at")]
        eng.close()
        return rows

    def build(self, tenders):
        eng, _ = self.run_scan(tenders)
        stats = eng.build_letters()
        eng.close()
        return stats

    def mark(self, status):
        eng = self.engine()
        oid = eng.db.one("SELECT outreach_id FROM outreach ORDER BY generated_at DESC"
                         " LIMIT 1")["outreach_id"]
        eng.repo.set_outreach(oid, status)
        eng.close()


class TestLiveDelivery(LiveBase):
    def test_live_letter_goes_to_the_company_address(self):
        self.build([t_one()])
        [row] = self.letters()
        self.assertEqual(row["mode"], "LIVE")
        self.assertEqual(row["delivery_email_actual"], CLIENT)
        self.assertEqual(row["contact_email_original"], CLIENT)
        self.assertEqual(row["template_version"], "V10_OWNER")

    def test_message_is_addressed_to_the_client_without_test_header(self):
        self.build([t_one()])
        eng = self.engine()
        row = eng.db.one("SELECT * FROM outreach")
        raw = M.build_letter_bytes(row).decode("utf-8", "replace")
        eng.close()
        self.assertIn(f"To: {CLIENT}", raw)
        self.assertNotIn("X-TenderWin-Intended-To", raw)

    def test_test_mode_still_goes_to_test_boxes(self):
        M.MODE = "TEST"
        self.build([t_one()])
        [row] = self.letters()
        self.assertEqual(row["mode"], "TEST")
        self.assertIn(row["delivery_email_actual"], M.TEST_RECIPIENTS)
        self.assertEqual(row["contact_email_original"], CLIENT)

    def test_database_refuses_live_letter_to_another_address(self):
        self.build([t_one()])
        eng = self.engine()
        good = dict(eng.db.one("SELECT * FROM outreach"))
        with self.assertRaises(sqlite3.IntegrityError) as спроба:
            eng.db.x(
                "INSERT INTO outreach(outreach_id, company_id, outreach_type, mode,"
                " status, template_version, subject_rendered, body_rendered,"
                " contact_email_original, delivery_email_actual, message_id_header,"
                " event_ids, generated_at) VALUES ('x', ?, 'FIRST_TOUCH', 'LIVE',"
                " 'CANCELLED', 'V10_OWNER', 's', 'b', ?, 'stranger@evil.example',"
                " '<x@y>', '[]', '2026-09-22')", (good["company_id"], CLIENT))
        self.assertIn("LIVE_DELIVERY_NOT_CONTACT", str(спроба.exception))
        eng.close()

    def test_test_letter_cannot_be_turned_into_a_live_one(self):
        M.MODE = "TEST"
        self.build([t_one()])
        eng = self.engine()
        with self.assertRaises(sqlite3.IntegrityError) as спроба:
            eng.db.x("UPDATE outreach SET mode = 'LIVE'")
        self.assertIn("LIVE_DELIVERY_NOT_CONTACT", str(спроба.exception))
        eng.close()

    def test_old_database_gets_the_live_guard(self):
        """База попередньої збірки дістає новий тригер під час відкриття."""
        eng = self.engine()
        eng.db.x("DROP TRIGGER trg_live_delivery_is_contact")
        eng.db.x("DROP TRIGGER trg_live_delivery_is_contact_update")
        eng.close()
        eng = self.engine()
        names = {r["name"] for r in eng.db.q(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        eng.close()
        self.assertIn("trg_live_delivery_is_contact", names)
        self.assertIn("trg_live_delivery_is_contact_update", names)


class TestTestHistoryDoesNotCount(LiveBase):
    def test_company_with_test_letters_gets_a_live_letter(self):
        M.MODE = "TEST"
        self.build([t_one()])
        self.mark("SENT_CONFIRMED")                 # тестовий лист «пішов»
        M.MODE = "LIVE"
        self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15)])
        rows = self.letters()
        self.assertEqual([r["mode"] for r in rows], ["TEST", "LIVE"])
        self.assertEqual(rows[1]["delivery_email_actual"], CLIENT)

    def test_legacy_history_of_the_old_engine_does_not_block(self):
        """Історія старого рушія — лише тестові листи (mode TEST)."""
        eng = self.engine()
        cid = eng.repo.company("12345678", "ТОВ «БУДІНВЕСТ»")
        eng.db.x("INSERT INTO outreach(outreach_id, company_id, outreach_type, mode,"
                 " status, origin, template_version, subject_rendered, body_rendered,"
                 " delivery_email_actual, message_id_header, event_ids, generated_at)"
                 " VALUES ('l1', ?, 'FIRST_TOUCH', 'TEST', 'LEGACY_SENT', 'IMPORTED',"
                 " 'LEGACY', 's', 'b', 'ppvetik1@gmail.com', '<l1@x>', '[]',"
                 " '2026-09-10T10:00:00+03:00')", (cid,))
        eng.close()
        self.build([t_one()])
        self.assertEqual([r["mode"] for r in self.letters()], ["TEST", "LIVE"])

    def test_event_seen_only_by_a_test_run_is_sent_live(self):
        """Сьогодні вже був тестовий прогін — бойовий не пропускає його подій."""
        M.MODE = "TEST"
        self.build([t_one()])
        self.mark("SENT_CONFIRMED")
        M.MODE = "LIVE"
        stats = self.build([t_one()])               # та сама подія, бойовий прогін
        self.assertEqual(stats["листів"], 1)
        live = [r for r in self.letters() if r["mode"] == "LIVE"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["delivery_email_actual"], CLIENT)


class TestRearm(LiveBase):
    def test_rearm_counts_and_builds_the_letter(self):
        M.MODE = "TEST"
        self.build([t_one()])
        M.MODE = "LIVE"
        eng = self.engine()
        win = M.Window(*M.day_bounds(T.TODAY), mode="TODAY_ONLY")
        win = M.Window(win.start, min(win.end, T.TODAY), win.mode, win.tail_start)
        result = T.FakeApi([t_one()]).scan(win)
        eng.repo.start_run(eng.run_id, win)
        stats = eng.ingest(result)
        built = eng.build_letters()
        eng.close()
        self.assertEqual(stats.get("повернуто_з_тесту"), 1)
        self.assertEqual(stats["нових_подій"], 1)
        self.assertEqual(built["листів"], 1)
        live = [r for r in self.letters() if r["mode"] == "LIVE"]
        self.assertEqual(len(live), 1)

    def test_event_of_a_live_run_is_not_rearmed(self):
        self.build([t_one()])
        self.mark("CANCELLED")                    # бойовий лист скасовано
        eng, _ = self.run_scan([t_one()])
        built = eng.build_letters()
        eng.close()
        self.assertEqual(built["листів"], 0)      # подія не стала «новою» вдруге
        self.assertEqual(len(self.letters()), 1)


class TestLiveFirstTouch(LiveBase):
    def second(self):
        return self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15)])

    def test_live_letter_blocks_the_next_live_letter(self):
        for status in ("QUEUED", "SENT", "SENT_CONFIRMED", "DELIVERY_UNKNOWN"):
            with self.subTest(status=status):
                self.tearDown(); self.setUp()
                self.build([t_one()])
                if status != "QUEUED":
                    self.mark(status)
                stats = self.second()
                self.assertEqual(stats["листів"], 0)
                self.assertEqual(stats["NOT_ELIGIBLE"], 1)
                self.assertEqual(len(self.letters()), 1)

    def test_failed_or_cancelled_live_letter_does_not_block(self):
        for status in ("SEND_FAILED", "CANCELLED"):
            with self.subTest(status=status):
                self.tearDown(); self.setUp()
                self.build([t_one()])
                self.mark(status)
                self.assertEqual(self.second()["листів"], 1)

    def test_suppressed_company_gets_no_live_letter(self):
        eng = self.engine()
        cid = eng.repo.company("12345678", "ТОВ «БУДІНВЕСТ»")
        eng.repo.suppress(state="OPTED_OUT", reason="просили не писати",
                          source="test", company_id=cid)
        eng.close()
        stats = self.build([t_one()])
        self.assertEqual(stats["SUPPRESSED"], 1)
        self.assertEqual(self.letters(), [])


class TestEmailCheck(LiveBase):
    def test_strict_email_rule(self):
        for good in ("office@bud.example", "i.petrenko@firm.com.ua", "a+b@x-y.ua"):
            self.assertTrue(M.valid_email(good), good)
        for bad in (None, "", "a..b@x.ua", "a@x", "a@-x.ua", "a b@x.ua",
                    "a@x.ua, b@y.ua", ".a@x.ua", "a.@x.ua"):
            self.assertFalse(M.valid_email(bad), bad)

    def test_broken_email_goes_to_review_not_to_the_client(self):
        stats = self.build([t_one(email="ivan..petrenko@bud.example")])
        self.assertEqual(stats["REVIEW"], 1)
        self.assertEqual(self.letters(), [])


class TestLiveSend(LiveBase):
    def setUp(self):
        super().setUp()
        self.fake = T.FakeTransport()
        self._real_transport = M.make_transport
        M.make_transport = lambda dry_run, out_dir: self.fake

    def tearDown(self):
        M.make_transport = self._real_transport
        super().tearDown()

    def send(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = M.send(paced=False, **kw)
        return out, buf.getvalue()

    def test_live_send_goes_to_the_client_without_asking(self):
        """Ні запитання, ні очікування клавіатури: «Виконати всі» теж надсилає."""
        self.build([t_one()])
        real_input = builtins.input

        def no_questions(prompt=""):
            raise AssertionError("відправка не має нічого питати")
        builtins.input = no_questions
        try:
            out, text = self.send()
        finally:
            builtins.input = real_input
        self.assertEqual(len(self.fake.sent), 1)
        import email                                              # noqa: PLC0415
        from email import policy                                  # noqa: PLC0415
        msg = email.message_from_bytes(self.fake.sent[0][1], policy=policy.default)
        self.assertEqual(msg["To"], CLIENT)
        self.assertIsNone(msg["X-TenderWin-Intended-To"])
        self.assertIn("0 800 357 135", msg.get_body(("plain",)).get_content())
        self.assertIn("0 800 357 135", msg.get_body(("html",)).get_content())
        self.assertEqual(self.letters()[0]["status"], "SENT_CONFIRMED")
        self.assertIn(f"→ {CLIENT}", text)              # у звіті видно, кому пішов

    def test_rest_of_the_queue_goes_with_the_next_run(self):
        self.build([t_one(), t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                                   code="87654321", name="ТОВ «ІНША»",
                                   email="info@insha.example")])
        _, text = self.send(max_sends=1)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertIn("ще 1", text)                      # решта — наступним запуском
        self.send(max_sends=1)
        self.assertEqual(len(self.fake.sent), 2)
        sent_to = sorted(r["delivery_email_actual"] for r in self.letters())
        self.assertEqual(sent_to, sorted([CLIENT, "info@insha.example"]))
        self.assertTrue(all(r["status"] == "SENT_CONFIRMED" for r in self.letters()))

    def test_setup_works_without_test_boxes(self):
        M.TEST_RECIPIENTS = []
        dysk = os.path.join(self.tmp, "MyDrive", "1 Lead&Mail generator")
        saved = (M.CARDS_DIR, M.WORK_DIR, M.E.TOKEN_DIR)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                M.setup(dysk)
        finally:
            M.CARDS_DIR, M.WORK_DIR, M.E.TOKEN_DIR = saved
        self.assertIn("БОЙОВИЙ РЕЖИМ", buf.getvalue())


class TestReviewFixes(LiveBase):
    """Виправлення за незалежною перевіркою 08.10.2026."""

    def test_letter_goes_to_the_address_of_todays_bid(self):
        """Стара адреса компанії з минулого (тестового) прогону — не адресат."""
        M.MODE = "TEST"
        self.build([t_one(email="old.agent@agency.example")])
        M.MODE = "LIVE"
        self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                          email="director@bud.example")])
        live = [r for r in self.letters() if r["mode"] == "LIVE"]
        self.assertEqual([r["delivery_email_actual"] for r in live],
                         ["director@bud.example"])

    def test_address_of_the_rejected_bid_beats_a_newer_company_address(self):
        """Компанія мала дві адреси; лист — на ту, що в пропозиції, про яку він."""
        M.MODE = "TEST"
        self.build([t_one(email="tender@bud.example")])           # закупівля A
        self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                          email="newer@bud.example")])            # закупівля B
        M.MODE = "LIVE"
        self.build([t_one(email="tender@bud.example")])           # бойовий: лише A
        live = [r for r in self.letters() if r["mode"] == "LIVE"]
        self.assertEqual([r["delivery_email_actual"] for r in live],
                         ["tender@bud.example"])

    def test_bad_address_today_falls_back_to_a_good_one(self):
        self.build([t_one(email="good@bud.example")])
        self.mark("CANCELLED")
        self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                          email="bad..address@bud.example")])
        live = [r for r in self.letters() if r["status"] == "QUEUED"]
        self.assertEqual([r["delivery_email_actual"] for r in live], ["good@bud.example"])

    def test_second_dry_run_the_same_day_rebuilds_unsent_letters(self):
        """Клітинка 6 удруге: ненадіслані листи першої партії складаються заново."""
        self.build([t_one(), t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                                   code="87654321", name="ТОВ «ІНША»",
                                   email="info@insha.example")])
        self.mark("SENT_CONFIRMED")                      # один лист уже пішов
        eng, _ = self.run_scan([t_one(), t_one(uid="t2", ua="UA-2026-09-02-000002-a",
                                               hour=15, code="87654321",
                                               name="ТОВ «ІНША»",
                                               email="info@insha.example")])
        скасовано = eng.cancel_stale()
        built = eng.build_letters()
        eng.close()
        self.assertEqual(скасовано, 1)
        self.assertEqual(built["листів"], 1)
        statuses = sorted(r["status"] for r in self.letters())
        self.assertEqual(statuses, ["CANCELLED", "QUEUED", "SENT_CONFIRMED"])
        sent = [r["company_id"] for r in self.letters() if r["status"] == "SENT_CONFIRMED"]
        queued = [r["company_id"] for r in self.letters() if r["status"] == "QUEUED"]
        self.assertNotEqual(sent, queued)                 # дубля немає

    def test_cancelled_letter_of_another_day_is_not_rebuilt(self):
        self.build([t_one()])
        eng = self.engine()                               # прогін без цієї події
        скасовано = eng.cancel_stale()
        built = eng.build_letters()
        eng.close()
        self.assertEqual((скасовано, built["листів"]), (1, 0))

    def test_review_events_are_not_rearmed(self):
        eng = self.engine()
        facts_events_before = eng.repo.rearmed
        eng.close()
        M.MODE = "TEST"
        self.build([t_one()])
        M.MODE = "LIVE"
        eng = self.engine()
        win = M.Window(*M.day_bounds(T.TODAY), mode="TODAY_ONLY")
        win = M.Window(win.start, min(win.end, T.TODAY), win.mode, win.tail_start)
        result = T.FakeApi([t_one()]).scan(win)
        result.review = [(f, "на перевірку") for f in result.events]
        result.events = []
        eng.repo.start_run(eng.run_id, win)
        stats = eng.ingest(result)
        eng.close()
        self.assertEqual(facts_events_before, 0)
        self.assertNotIn("повернуто_з_тесту", stats)

    def test_live_working_card_is_a_separate_file(self):
        M.MODE = "TEST"
        eng, _ = self.run_scan([t_one()])
        eng.build_letters(); test_cards = eng.cards(); eng.close()
        M.MODE = "LIVE"
        eng, _ = self.run_scan([t_one()])
        eng.build_letters(); live_cards = eng.cards(); folder = live_cards["тека"]
        eng.close()
        self.assertEqual((test_cards["карток"], live_cards["карток"]), (1, 1))
        files = sorted(f for _, _, fs in os.walk(folder) for f in fs)
        self.assertEqual(len(files), 2)
        self.assertTrue(any(f.endswith("__LIVE.docx") for f in files))

    def test_state_counts_live_and_test_separately(self):
        M.MODE = "TEST"
        self.build([t_one()])
        M.MODE = "LIVE"
        self.build([t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15)])
        with contextlib.redirect_stdout(io.StringIO()):
            out = M.state()
        self.assertEqual(out["бойові листи (LIVE)"], {"QUEUED": 1})
        self.assertEqual(out["тестові листи (TEST)"], {"QUEUED": 1})


class TestSendFailures(LiveBase):
    def setUp(self):
        super().setUp()
        self.build([t_one(), t_one(uid="t2", ua="UA-2026-09-02-000002-a", hour=15,
                                   code="87654321", name="ТОВ «ІНША»",
                                   email="info@insha.example"),
                    t_one(uid="t3", ua="UA-2026-09-03-000003-a", hour=16,
                          code="11223344", name="ТОВ «ТРЕТЯ»", email="info@tretia.example")])

    def eng_send(self, transport):
        eng = self.engine()
        eng.batch_id = eng.db.one("SELECT batch_id FROM outreach LIMIT 1")["batch_id"]
        eng.verbose = False
        stats = eng.send(transport=transport, paced=False)
        eng.close()
        return stats

    def test_gmail_refusal_stops_the_batch_and_keeps_letters_queued(self):
        """Прострочений токен / ліміт: зупинка після 2 відмов, ніхто не втрачений."""
        refusal = T.FakeTransport(T.FakeResult(ok=False, error="invalid_grant"))
        stats = self.eng_send(refusal)
        self.assertEqual(len(refusal.sent), M.STOP_AFTER_FAILURES_IN_ROW)
        self.assertIn("ЗУПИНЕНО", stats)
        self.assertEqual(sorted(r["status"] for r in self.letters()), ["QUEUED"] * 3)
        ok = T.FakeTransport()
        self.eng_send(ok)                                  # пошта ожила — усі пішли
        self.assertEqual(len(ok.sent), 3)
        self.assertEqual(sorted(r["status"] for r in self.letters()),
                         ["SENT_CONFIRMED"] * 3)

    def test_letter_taken_by_another_send_is_skipped(self):
        """Друга відправка забирає лист уже ПІСЛЯ того, як перша прочитала чергу."""
        test = self
        batch = self.letters()[0]["batch_id"]
        rows = self.engine().repo.queued(batch, 10)
        other = self.engine()

        class Racing(T.FakeTransport):
            def send(self, raw, message_id_header):
                if not self.sent:                          # під час першого листа
                    test.assertTrue(other.repo.claim(rows[1]["outreach_id"]))
                return super().send(raw, message_id_header)
        fake = Racing()
        self.eng_send(fake)
        other.close()
        sent_ids = [m for m, _ in fake.sent]
        self.assertNotIn(rows[1]["message_id_header"], sent_ids)
        self.assertEqual(len(fake.sent), 2)                # взятий лист не дублюється

    def test_summary_does_not_claim_letters_went_when_none_did(self):
        refusal = T.FakeTransport(T.FakeResult(ok=False, error="403 quota"))
        real = M.make_transport
        M.make_transport = lambda dry_run, out_dir: refusal
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                M.send(paced=False)
        finally:
            M.make_transport = real
        text = buf.getvalue()
        self.assertIn("Нічого не надіслано", text)
        self.assertNotIn("Бойових листів надіслано", text)
        self.assertIn("лишились у черзі", text)


class TestAmbiguousGmailErrors(LiveBase):
    """Відмова 4xx — лист точно не пішов; 5xx/обрив — міг піти, наосліп не повторюємо."""

    def test_classification(self):
        for refused in ("400", "403 quota", "invalid_grant: Token has been expired",
                        "<HttpError 429 when requesting ... returned \"User-rate limit exceeded\">",
                        "RefreshError('invalid_grant')"):
            self.assertTrue(M.gmail_refused(refused), refused)
        for unknown in ("<HttpError 500 when requesting ... returned \"Backend Error\">",
                        "<HttpError 503 ...>", "IncompleteRead(0 bytes read)", "",
                        "RemoteDisconnected('Remote end closed connection')"):
            self.assertFalse(M.gmail_refused(unknown), unknown)

    def send_once(self, transport):
        eng = self.engine()
        eng.batch_id = eng.db.one("SELECT batch_id FROM outreach LIMIT 1")["batch_id"]
        eng.verbose = False
        stats = eng.send(transport=transport, paced=False)
        eng.close()
        return stats

    def test_server_error_is_unknown_and_never_resent_blindly(self):
        self.build([t_one()])
        stats = self.send_once(T.FakeTransport(T.FakeResult(
            ok=False, error="<HttpError 500 when requesting returned \"Backend Error\">")))
        self.assertEqual(stats["DELIVERY_UNKNOWN"], 1)
        self.assertEqual(self.letters()[0]["status"], "DELIVERY_UNKNOWN")
        again = T.FakeTransport()
        self.send_once(again)
        self.assertEqual(again.sent, [])                 # повтору наосліп немає

    def test_retry_first_checks_the_sent_folder(self):
        """Лист повернули в чергу, але Gmail його все ж має — повтору немає."""
        self.build([t_one()])
        self.send_once(T.FakeTransport(T.FakeResult(ok=False, error="403 quota")))
        self.assertEqual(self.letters()[0]["status"], "QUEUED")
        row = self.letters()[0]

        class AlreadyThere(T.FakeTransport):
            def find_by_message_id(self, mid):
                return {"id": "g-1", "threadId": "t-1"} if mid == row["message_id_header"] else None
        transport = AlreadyThere()
        stats = self.send_once(transport)
        self.assertEqual(transport.sent, [])
        self.assertEqual(stats["SENT_CONFIRMED"], 1)
        self.assertEqual(self.letters()[0]["status"], "SENT_CONFIRMED")

    def test_summary_counts_letters_found_by_reconcile(self):
        self.build([t_one()])

        class TimeoutButSent(T.FakeTransport):
            def send(self, raw, mid):
                self.sent.append((mid, raw))
                return T.FakeResult(ok=False, unknown=True, error="timeout")
        transport = TimeoutButSent()
        real = M.make_transport
        M.make_transport = lambda dry_run, out_dir: transport
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                M.send(paced=False)
        finally:
            M.make_transport = real
        text = buf.getvalue()
        self.assertNotIn("Нічого не надіслано", text)
        self.assertIn("Бойових листів надіслано: 1", text)
        self.assertEqual(self.letters()[0]["status"], "SENT_CONFIRMED")


class TestServiceCardLabel(LiveBase):
    def test_service_card_rows_carry_the_mode(self):
        self.build([t_one()])
        eng = self.engine()
        rows = eng.service_card_rows(day="2026-09-22")
        eng.close()
        self.assertEqual(rows[0]["letter_mode"], "LIVE")
        import tenderwin_service_cards as SC                 # noqa: PLC0415
        view = SC._event_view(rows[0], M.now())
        self.assertEqual(view["letter_mode"], "LIVE")


class TestLetterV10(T.Base):
    """Чинний лист V10: V9 слово в слово, лише телефон «0 800 357 135»."""

    def лист(self):
        eng, _ = self.run_scan([t_one()])
        eng.build_letters()
        row = eng.db.one("SELECT * FROM outreach")
        eng.close()
        return row

    def test_v10_is_the_active_letter(self):
        self.assertEqual(M.LETTER_VERSION, "V10_OWNER")
        self.assertEqual(self.лист()["template_version"], "V10_OWNER")

    def test_only_the_phone_differs_from_v9(self):
        v9, v10 = M.ALL_TEMPLATES["V9_OWNER"], M.ALL_TEMPLATES["V10_OWNER"]
        self.assertEqual(v10.subject, v9.subject)
        self.assertEqual(v10.body.replace("0 800 357 135", "+380 800 357 135"), v9.body)
        self.assertEqual(v10.html.replace("0 800 357 135", "+380 800 357 135"), v9.html)
        self.assertIn("+380 800 357 135", v9.body)     # V9 не змінено

    def test_signature_in_text_and_html(self):
        row = self.лист()
        self.assertTrue(row["body_rendered"].endswith(
            "З повагою,\nВіталій Щасливий\nрадник з публічних закупівель\n"
            "0 800 357 135\n"))
        self.assertNotIn("+380", row["body_rendered"])
        self.assertIn("радник з публічних закупівель<br>0 800 357 135</p>",
                      row["body_html"])
        self.assertNotIn("+380", row["body_html"])
        self.assertNotIn("tel:", row["body_html"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
