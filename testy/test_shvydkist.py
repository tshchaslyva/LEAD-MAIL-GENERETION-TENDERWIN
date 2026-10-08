# -*- coding: utf-8 -*-
"""
Прогін має тривати СТАЛИЙ час, а не рости з кожним робочим днем.

Дефект (звіт 26): тека справи лежала всередині теки прогону, разом із
маніфестом «цей документ уже завантажено». Новий прогін = нова тека =
порожній маніфест = повторне завантаження ВСІЄЇ історії.

Запуск:  python3 -m unittest test_shvydkist
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tenderwin_lead_engine as E   # noqa: E402

E.ALLOW_COLAB_SECRETS = False

import test_lead_engine as T        # noqa: E402

DOCS = [{"назва": f"Документ {i}.pdf", "url": f"https://p/d{i}", "тип": ""}
        for i in range(1, 8)]


class Merezha:
    """Мережа без мережі: рахує КОЖНЕ звернення."""

    def __init__(self):
        self.calls: list[str] = []

    def make_session(self):
        return object()

    def get_bytes(self, session, url, tries=3):
        self.calls.append(url)
        return b"%PDF-1.4 " + b"x" * 512


class Baza(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "leads.db")
        self.root = os.path.join(self.tmp, "drive")
        cfg = E.Config(db_path=self.db, out_dir=os.path.join(self.tmp, "o"),
                       mode="TEST", min_delay=0, max_delay=0, verbose=False)
        self.eng = E.LeadEngine(cfg, E.FixtureSource([]),
                                E.DryRunTransport(os.path.join(self.tmp, "eml")),
                                None)
        self.eng.repo.mark_history_migrated("тест")

    def tearDown(self):
        try:
            self.eng.close()
        except Exception:                                           # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sprava(self, n: int):
        """Додає одну справу і повертає її rejection_id."""
        f = T.facts(edrpou=f"1000000{n}", ua=f"UA-2026-09-0{n}-00000{n}-a",
                    email=f"k{n}@e.ua", company=f"ТОВ «Фірма {n}»",
                    bid=f"bid-{n}", award=f"award-{n}")
        f.protocol_url = f"https://p/proto{n}"
        f.protocol_title = "Протокол.pdf"
        f.td_documents = DOCS
        f.bid_documents = []
        f.award_documents = []
        f.qa_documents = []
        before = list(self.eng.touched_rejections)
        self.eng.ingest([f])
        new = [x for x in self.eng.touched_rejections if x not in before]
        return new[0] if new else ""


class TestPovtornyjProhin(Baza):
    """Головна властивість: другий прогін не качає те саме вдруге."""

    def test_second_run_downloads_nothing_again(self):
        self.sprava(1)
        net = Merezha()
        st1 = E.download_case_documents(self.db, self.root, legacy=net,
                                        case_dir=E.run_folder(self.root, "r1"),
                                        make_pdf_copies=False)
        self.assertGreater(st1["завантажено"], 0)

        net2 = Merezha()
        st2 = E.download_case_documents(self.db, self.root, legacy=net2,
                                        case_dir=E.run_folder(self.root, "r2"),
                                        make_pdf_copies=False)
        self.assertEqual(net2.calls, [], "другий прогін качав ті самі файли")
        self.assertEqual(st2["завантажено"], 0)
        self.assertEqual(st2["вже_були"], st1["завантажено"])

    def test_work_does_not_grow_with_history(self):
        """Десять днів роботи — стільки ж роботи на прогін, скільки в перший."""
        obsyahy = []
        for den in range(1, 6):
            self.sprava(den)
            net = Merezha()
            E.download_case_documents(
                self.db, self.root, legacy=net,
                case_dir=E.run_folder(self.root, f"r{den}"),
                rejection_ids=self.eng.touched_rejections[-1:],
                make_pdf_copies=False)
            obsyahy.append(len(net.calls))
        self.assertEqual(len(set(obsyahy)), 1,
                         f"обсяг роботи росте: {obsyahy}")


class TestFiltrProhonu(Baza):
    """rejection_ids: None, список і ПОРОЖНІЙ список — три різні речі."""

    def test_only_this_run_cases_are_processed(self):
        self.sprava(1)
        rid2 = self.sprava(2)
        net = Merezha()
        st = E.download_case_documents(self.db, self.root, legacy=net,
                                       rejection_ids=[rid2],
                                       make_pdf_copies=False)
        self.assertEqual(st["справ"], 1)
        self.assertTrue(os.path.isdir(
            E.case_archive(self.root, "UA-2026-09-02-000002-a")))
        self.assertFalse(os.path.isdir(
            E.case_archive(self.root, "UA-2026-09-01-000001-a")),
            "опрацьовано справу, якої цей прогін не торкався")

    def test_empty_list_means_nothing_not_everything(self):
        """День без відхилень не має означати «перекачай усю історію»."""
        self.sprava(1)
        self.sprava(2)
        net = Merezha()
        st = E.download_case_documents(self.db, self.root, legacy=net,
                                       rejection_ids=[],
                                       make_pdf_copies=False)
        self.assertEqual(net.calls, [])
        self.assertEqual(st["справ"], 0)

    def test_none_means_all_cases(self):
        """Ручна перебудова має і далі проходити по всій базі."""
        self.sprava(1)
        self.sprava(2)
        net = Merezha()
        st = E.download_case_documents(self.db, self.root, legacy=net,
                                       rejection_ids=None,
                                       make_pdf_copies=False)
        self.assertEqual(st["справ"], 2)

    def test_engine_remembers_only_this_runs_cases(self):
        rid1 = self.sprava(1)
        self.assertEqual(self.eng.touched_rejections, [rid1])
        rid2 = self.sprava(2)
        self.assertEqual(self.eng.touched_rejections, [rid1, rid2])


class TestArhivIPokazhchyk(Baza):
    """Картка лежить ПОРУЧ із документами; тека прогону тримає покажчик."""

    def setUp(self):
        super().setUp()
        self.sprava(1)
        self.eng.build_queue()
        self.eng.send()
        self.ua = "UA-2026-09-01-000001-a"
        self.arhiv = E.case_archive(self.root, self.ua)

    def test_card_sits_next_to_the_documents(self):
        E.download_case_documents(self.db, self.root, legacy=Merezha(),
                                  make_pdf_copies=False)
        run = E.run_folder(self.root, "r1")
        E.build_case_cards(self.db, self.root, legacy=None, case_dir=run)
        files = os.listdir(self.arhiv)
        card = [f for f in files if f.startswith(E.CARD_PREFIX)]
        self.assertEqual(len(card), 1, files)
        self.assertTrue([f for f in files if f.startswith("03_TD_")], files)

    def test_run_folder_holds_the_index_only(self):
        E.download_case_documents(self.db, self.root, legacy=Merezha(),
                                  make_pdf_copies=False)
        run = E.run_folder(self.root, "r1")
        E.build_case_cards(self.db, self.root, legacy=None, case_dir=run)
        self.assertEqual(sorted(os.listdir(run)), [E.RUN_INDEX_NAME])

    def test_the_index_links_to_the_card(self):
        import docx
        run = E.run_folder(self.root, "r1")
        E.build_case_cards(self.db, self.root, legacy=None, case_dir=run)
        d = docx.Document(os.path.join(run, E.RUN_INDEX_NAME))
        targets = [r.target_ref for r in d.part.rels.values() if r.is_external]
        self.assertTrue(any(E.CARD_PREFIX in t for t in targets), targets)
        text = "\n".join(c.text for t in d.tables for r in t.rows
                         for c in r.cells)
        self.assertIn(self.ua, text)

    def test_human_notes_survive_the_next_run(self):
        """Нотатки людини в картці не переписує жоден наступний прогін."""
        import docx
        run1 = E.run_folder(self.root, "r1")
        E.build_case_cards(self.db, self.root, legacy=None, case_dir=run1)
        card = os.path.join(self.arhiv,
                            f"{E.CARD_PREFIX}10000001.docx")
        d = docx.Document(card)
        d.add_paragraph("МІЙ ВИСНОВОК: оскарження перспективне")
        d.save(card)

        run2 = E.run_folder(self.root, "r2")
        E.build_case_cards(self.db, self.root, legacy=None, case_dir=run2)
        text = "\n".join(p.text for p in docx.Document(card).paragraphs)
        self.assertIn("МІЙ ВИСНОВОК", text)


class TestPdfKopii(unittest.TestCase):
    """Імʼя PDF-копії і пакетна конвертація."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_copy_name_keeps_the_original_extension(self):
        """«Кошторис.xlsx» і «Кошторис.docx» — дві РІЗНІ копії."""
        a = E._pdf_copy_path(os.path.join(self.tmp, "Кошторис.xlsx"))
        b = E._pdf_copy_path(os.path.join(self.tmp, "Кошторис.docx"))
        self.assertNotEqual(a, b)
        self.assertIn(".xlsx", os.path.basename(a))
        self.assertIn(".docx", os.path.basename(b))

    def test_two_documents_one_stem_do_not_share_a_copy(self):
        import openpyxl
        # Обидва формати NotebookLM НЕ читає: .rtf і .xlsx. (.docx він тепер
        # читає сам — для перевірки колізії імен не годиться.)
        with open(os.path.join(self.tmp, "Зведений кошторис.rtf"), "w",
                  encoding="ascii") as fh:
            fh.write(r"{\rtf1\ansi word document}")
        wb = openpyxl.Workbook()
        wb.active.append(["це книга Excel", 125.40])
        wb.save(os.path.join(self.tmp, "Зведений кошторис.xlsx"))
        paths = [os.path.join(self.tmp, n) for n in
                 ("Зведений кошторис.rtf", "Зведений кошторис.xlsx")]
        res = E.convert_many_to_pdf(paths)
        if all(st == "НЕМАЄ_LIBREOFFICE" for _, st in res.values()):
            self.skipTest("немає LibreOffice")
        made = {p for p, _ in res.values() if p}
        self.assertEqual(len(made), 2, res)

    def test_every_input_gets_a_status(self):
        """Жоден файл не має зникнути з відповіді мовчки."""
        paths = [os.path.join(self.tmp, n) for n in
                 ("a.pdf", "b.txt", "c.exe")]
        for p in paths:
            with open(p, "wb") as fh:
                fh.write(b"x")
        res = E.convert_many_to_pdf(paths)
        self.assertEqual(set(res), set(paths))
        self.assertEqual(res[paths[0]][1], "НЕ_ПОТРІБНО")
        self.assertEqual(res[paths[2]][1], "ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ")


class TestMihraciyaOdynRaz(unittest.TestCase):
    """Обхід усіх тек Диска — один раз, за маркером."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "spravy", "UA-STARA"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marker_stops_the_second_walk(self):
        first = E.migrate_case_layout(self.tmp, verbose=False)
        self.assertNotIn("вже_перенесено", first)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "spravy", E.MIGRATION_MARKER)))
        second = E.migrate_case_layout(self.tmp, verbose=False)
        self.assertTrue(second.get("вже_перенесено"))

    def test_removing_the_marker_lets_it_run_again(self):
        E.migrate_case_layout(self.tmp, verbose=False)
        os.remove(os.path.join(self.tmp, "spravy", E.MIGRATION_MARKER))
        again = E.migrate_case_layout(self.tmp, verbose=False)
        self.assertNotIn("вже_перенесено", again)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestZvedennyaVArhiv(unittest.TestCase):
    """Уже завантажені документи з тек прогонів не пропадають."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.path.join(self.tmp, "spravy", "02.09.2026",
                                "prohin_ab12cd", "UA-2026-09-02-000001-a")
        os.makedirs(self.old, exist_ok=True)
        for name in ("03_TD_01_Tenderna_dokumentaciya.pdf",
                     "00_KARTKA_12345678.docx", E.MANIFEST_NAME):
            with open(os.path.join(self.old, name), "wb") as fh:
                fh.write("мої нотатки".encode("utf-8"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_run_folders_are_folded_into_the_archive(self):
        E.migrate_case_layout(self.tmp, verbose=False)
        arhiv = E.case_archive(self.tmp, "UA-2026-09-02-000001-a")
        self.assertEqual(
            sorted(os.listdir(arhiv)),
            sorted(["03_TD_01_Tenderna_dokumentaciya.pdf",
                    "00_KARTKA_12345678.docx", E.MANIFEST_NAME]))
        with open(os.path.join(arhiv, "00_KARTKA_12345678.docx"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "мої нотатки")

    def test_a_file_already_in_the_archive_is_not_overwritten(self):
        arhiv = E.case_archive(self.tmp, "UA-2026-09-02-000001-a")
        os.makedirs(arhiv, exist_ok=True)
        with open(os.path.join(arhiv, "00_KARTKA_12345678.docx"),
                  "w", encoding="utf-8") as fh:
            fh.write("новіша картка з висновком")
        stats = E.migrate_case_layout(self.tmp, verbose=False)
        with open(os.path.join(arhiv, "00_KARTKA_12345678.docx"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "новіша картка з висновком")
        self.assertGreaterEqual(stats["конфліктів"], 1)
        self.assertTrue(os.path.isfile(
            os.path.join(self.old, "00_KARTKA_12345678.docx")),
            "старий файл зник, хоча його не перенесли")


class TestSamolikuvannya(Baza):
    """Неповна справа не має лишитись неповною назавжди — але й не має
    перетворити прогін на перебір усієї історії."""

    def test_a_complete_case_is_not_touched_again(self):
        self.sprava(1)
        ids = list(self.eng.touched_rejections)
        E.download_case_documents(self.db, self.root, legacy=Merezha(),
                                  rejection_ids=ids, make_pdf_copies=False)
        E.build_case_cards(self.db, self.root, legacy=None,
                           case_dir=E.run_folder(self.root, "r1"),
                           rejection_ids=ids)
        potribno, lyshylos = E.cases_needing_attention(self.db, self.root, [])
        self.assertEqual(potribno, [], "повну справу взяли в роботу ще раз")
        self.assertEqual(lyshylos, 0)

    def test_a_case_without_documents_is_picked_up_later(self):
        """Упала мережа — наступний прогін доліковує, а не забуває."""
        rid = self.sprava(1)
        potribno, _ = E.cases_needing_attention(self.db, self.root, [])
        self.assertEqual(potribno, [rid])

    def test_this_runs_cases_come_first_and_are_never_lost(self):
        rid1 = self.sprava(1)
        rid2 = self.sprava(2)
        potribno, _ = E.cases_needing_attention(self.db, self.root, [rid2])
        self.assertEqual(potribno[0], rid2, "справа прогону має йти першою")
        self.assertIn(rid1, potribno)

    def test_the_amount_of_healing_is_capped(self):
        for n in range(1, 13):
            self.sprava(n)
        potribno, lyshylos = E.cases_needing_attention(
            self.db, self.root, [], limit=5)
        self.assertEqual(len(potribno), 5, "межа доліковування не тримається")
        self.assertEqual(lyshylos, 7)

    def test_a_suppressed_case_is_not_healed(self):
        """Компанію в стоп-листі не обслуговуємо взагалі."""
        self.sprava(1)
        self.eng.db.conn.execute("UPDATE leads SET status='SUPPRESSED'")
        self.eng.db.conn.commit()
        potribno, _ = E.cases_needing_attention(self.db, self.root, [])
        self.assertEqual(potribno, [])


class TestParalelnyjRozbir(unittest.TestCase):
    """Розбір відхилень у потоках: швидше, але порядок і повнота ті самі."""

    def dzherelo(self, tenders, pause=0.0, breaks=()):
        """LegacyLeadSource із підробленим модулем мережі."""
        import types as _t
        import time as _time

        calls = []

        def download_protocol(session, award, folder):
            calls.append(award.get("id"))
            if pause:
                _time.sleep(pause)
            if award.get("id") in breaks:
                raise RuntimeError("мережа впала")
            return f"Протокол по {award.get('id')}: учасником не надано довідку."

        fake = _t.SimpleNamespace(
            make_session=lambda: object(),
            scan_day=lambda s, a, b, c: tenders,
            all_rejections=lambda t: [("awards", a) for a in t["awards"]],
            participant_contacts=lambda t, a: {
                "ЄДРПОУ": a["edrpou"], "компанія": a["firma"],
                "email": a["email"], "телефон": "", "контактна_особа": "",
                "адреса": "", "ПІБ": {}},
            download_protocol=download_protocol,
            rejection_time=lambda t, a: "2026-09-11T10:00:00+03:00",
            lot_amount=lambda t, a: 1000.0,
        )
        src = E.LegacyLeadSource.__new__(E.LegacyLeadSource)
        src.m = fake
        src.case_dir = tempfile.mkdtemp()
        src.download_protocols = True
        src.verbose = False
        src.skipped = []
        return src, calls

    @staticmethod
    def tender(uid, awards):
        return {uid: {"id": uid, "tenderID": uid, "title": "Ремонт",
                      "procuringEntity": {"name": "Замовник"},
                      "value": {"amount": 1000.0}, "awards": awards,
                      "items": []}}

    @staticmethod
    def award(n, edrpou=None):
        return {"id": f"award-{n}", "status": "unsuccessful",
                "edrpou": f"{10000000 + n}" if edrpou is None else edrpou,
                "firma": f"ТОВ «Фірма {n}»", "email": f"k{n}@e.ua",
                "title": "Відхилено", "description": "не надано довідку",
                "documents": []}

    def test_order_is_preserved(self):
        awards = [self.award(n) for n in range(12)]
        src, _ = self.dzherelo(self.tender("UA-1", awards))
        facts = src.rejections_for_day("2026-09-11")
        self.assertEqual([f.edrpou for f in facts],
                         [a["edrpou"] for a in awards],
                         "паралельний розбір переплутав порядок")

    def test_every_rejection_is_parsed(self):
        awards = [self.award(n) for n in range(25)]
        src, calls = self.dzherelo(self.tender("UA-1", awards))
        facts = src.rejections_for_day("2026-09-11")
        self.assertEqual(len(facts), 25)
        self.assertEqual(sorted(calls), sorted(a["id"] for a in awards))

    def test_one_broken_rejection_does_not_lose_the_rest(self):
        awards = [self.award(n) for n in range(6)]
        src, _ = self.dzherelo(self.tender("UA-1", awards),
                               breaks={"award-2"})
        facts = src.rejections_for_day("2026-09-11")
        # Протокол не завантажився, але title+description лишились —
        # підстава є, справа не губиться.
        self.assertEqual(len(facts), 6)

    def test_a_rejection_without_a_code_is_reported_not_swallowed(self):
        awards = [self.award(0), self.award(1, edrpou=""), self.award(2)]
        src, _ = self.dzherelo(self.tender("UA-1", awards))
        facts = src.rejections_for_day("2026-09-11")
        self.assertEqual(len(facts), 2)
        self.assertEqual(len(src.skipped), 1)
        self.assertIn("ЄДРПОУ", src.skipped[0][1])

    def test_parallel_is_actually_faster(self):
        """Реальна картина дня: багато закупівель по одному відхиленню."""
        tenders = {}
        for n in range(16):
            tenders.update(self.tender(f"UA-{n}", [self.award(n)]))
        src, _ = self.dzherelo(tenders, pause=0.05)
        t0 = time.time()
        facts = src.rejections_for_day("2026-09-11")
        paralelno = time.time() - t0
        poslidovno = 16 * 0.05
        self.assertEqual(len(facts), 16)
        self.assertLess(paralelno, poslidovno * 0.6,
                        f"паралельно {paralelno:.2f} с проти {poslidovno:.2f} с")

    def test_rejections_of_one_tender_never_run_at_the_same_time(self):
        """Два рішення однієї закупівлі пишуть протоколи в одну теку."""
        import threading
        active, peak, lock = [0], [0], threading.Lock()
        awards = [self.award(n) for n in range(6)]
        src, _ = self.dzherelo(self.tender("UA-1", awards))
        real = src.m.download_protocol

        def watched(session, award, folder):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            try:
                return real(session, award, folder)
            finally:
                with lock:
                    active[0] -= 1

        src.m.download_protocol = watched
        src.rejections_for_day("2026-09-11")
        self.assertEqual(peak[0], 1, "відхилення однієї закупівлі йшли паралельно")
