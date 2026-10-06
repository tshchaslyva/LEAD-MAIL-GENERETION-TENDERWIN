# -*- coding: utf-8 -*-
"""
Тести службової картки: дві таблиці, посилання на документи, межі
завантаження. Мережа не потрібна — завантажувач підмінюється.

Запуск:  python3 -m unittest test_kartka
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tenderwin_lead_engine as E   # noqa: E402

E.ALLOW_COLAB_SECRETS = False

import test_lead_engine as T        # noqa: E402

PROTO = (
    "Тендерна пропозиція учасника не відповідає вимогам п. 1 Розділу III "
    "тендерної документації, оскільки надана довідка про наявність "
    "працівників не містить інформації про інженерно-технічний персонал. "
    "Крім того, учасник протягом 24 годин не усунув невідповідності, а саме "
    "не надав акти виконаних робіт за аналогічним договором, Додатком 3. "
    "Також строк дії банківської гарантії менший ніж вимагав п. 7 Додатку 4.")

TD_DOCS = [
    {"назва": "Тендерна документація.pdf", "url": "https://p/td1", "тип": "biddingDocuments"},
    {"назва": "Додаток 2 Кваліфікаційні критерії.docx", "url": "https://p/td2", "тип": ""},
]
BID_DOCS = [
    {"назва": "Довідка про наявність працівників.pdf", "url": "https://p/b1", "тип": ""},
    {"назва": "Банківська гарантія.pdf", "url": "https://p/b2", "тип": ""},
    {"назва": "Статут.pdf", "url": "https://p/b3", "тип": ""},
]


class FakeNet:
    """Замість мережі. Рахує виклики, щоб перевірити повторний запуск."""

    def __init__(self, payload=b"%PDF-1.4 fake", fail=(), huge=()):
        self.payload, self.fail, self.huge = payload, set(fail), set(huge)
        self.calls = []

    def make_session(self):
        return object()

    def get_bytes(self, session, url, tries=3):
        self.calls.append(url)
        if url in self.fail:
            return None
        if url in self.huge:
            return b"x" * (E.MAX_DOC_BYTES + 1)
        return self.payload


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "leads.db")
        self.root = os.path.join(self.tmp, "drive")
        cfg = E.Config(db_path=self.db_path, out_dir=os.path.join(self.tmp, "o"),
                       mode="TEST", min_delay=0, max_delay=0, verbose=False)
        eng = E.LeadEngine(cfg, E.FixtureSource([]),
                           E.DryRunTransport(os.path.join(self.tmp, "eml")), None)
        eng.repo.mark_history_migrated("тест")
        eng.ingest([T.facts(reason=PROTO)])
        eng.build_queue()
        eng.send()
        eng.db.conn.execute(
            "UPDATE rejections SET cpv='45000000-7', lot_amount=4045480.0,"
            " bid_amount=2251738.0, winner_amount=2609900.0,"
            " winner_name='ТОВ «Переможець»',"
            " protocol_url='https://p/proto', protocol_title='Протокол.pdf',"
            " notice_24h='YES', notice_24h_url='https://p/n24',"
            " notice_24h_title='Вимога про усунення невідповідностей.pdf',"
            " td_documents=?, bid_documents=?",
            (json.dumps(TD_DOCS, ensure_ascii=False),
             json.dumps(BID_DOCS, ensure_ascii=False)))
        eng.db.conn.commit()
        eng.close()
        self.folder = os.path.join(self.root, "spravy", "UA-2026-08-28-000001-a")
        self.card = os.path.join(self.folder, "00_KARTKA_12345678.docx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, net=None, **kw):
        if net is not None:
            E.download_case_documents(self.db_path, self.root, legacy=net)
        return E.build_case_cards(self.db_path, self.root, legacy=None, **kw)

    def doc(self):
        import docx
        return docx.Document(self.card)

    def text(self):
        d = self.doc()
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)

    def links(self):
        return [r.target_ref for r in self.doc().part.rels.values() if r.is_external]


class TestTwoTables(Base):
    """
    Рішення власника продукту 2026-09-02: ключові слова Clarity з картки
    прибрати. У картці лишається ОДНА таблиця.
    """

    def test_the_card_has_exactly_one_table(self):
        self.build()
        self.assertEqual(len(self.doc().tables), 1,
                         "у картці має лишитись лише основна таблиця")

    def test_no_clarity_keywords_anywhere_in_the_card(self):
        self.build()
        text = self.text()
        self.assertNotIn("КЛЮЧОВІ СЛОВА", text.upper())
        self.assertNotIn("Clarity", text)

    def test_no_separate_queries_file(self):
        self.build()
        left = [f for f in os.listdir(self.folder) if "ZAPYTY" in f.upper()]
        self.assertEqual(left, [], f"файл запитів лишився: {left}")

    def test_the_keyword_table_can_be_switched_back_on(self):
        """Генератор не видалено — його можна повернути одним значенням."""
        E.CLARITY_KEYWORDS_IN_CARD = True
        try:
            self.build()
            self.assertEqual(len(self.doc().tables), 2)
        finally:
            E.CLARITY_KEYWORDS_IN_CARD = False

    def test_first_table_holds_the_grounds(self):
        self.build()
        rows = [" | ".join(c.text for c in r.cells)
                for r in self.doc().tables[0].rows]
        joined = "\n".join(rows)
        for cid in ("C01", "C02", "C03"):
            self.assertIn(cid, joined, f"підстави {cid} немає в основній таблиці")
        self.assertIn("ЩО ПЕРЕВІРИТИ ПЕРЕД РОЗМОВОЮ", joined)

    def test_keywords_are_not_in_the_main_table(self):
        self.build()
        rows = "\n".join(" | ".join(c.text for c in r.cells)
                         for r in self.doc().tables[0].rows)
        self.assertNotIn("Ключові слова для Clarity", rows)


class TestDocumentLinks(Base):
    """Посилання на протокол, вимогу 24 год, документи ТД і пропозиції."""

    def test_all_four_link_rows_exist(self):
        self.build()
        text = self.text()
        for row in ("ПРОТОКОЛ / рішення", "ВИМОГА про усунення за 24 год",
                    "ДОКУМЕНТИ УЧАСНИКА, названі в рішенні",
                    "ДОКУМЕНТИ ТД — де шукати вимогу"):
            self.assertIn(row, text, f"немає рядка «{row}»")

    def test_links_point_to_prozorro_when_nothing_downloaded(self):
        self.build()
        targets = self.links()
        for url in ("https://p/proto", "https://p/n24", "https://p/td1",
                    "https://p/b1"):
            self.assertIn(url, targets, f"немає посилання на {url}")

    def test_named_documents_are_separated_from_the_rest(self):
        self.build()
        rows = {r.cells[0].text: r.cells[1].text
                for r in self.doc().tables[0].rows}
        named = rows.get("ДОКУМЕНТИ УЧАСНИКА, названі в рішенні", "")
        self.assertIn("Довідка про наявність працівників", named)
        self.assertIn("Банківська гарантія", named)
        self.assertNotIn("Статут", named, "Статут у рішенні не згадувався")

    def test_missing_24h_requirement_is_stated_in_the_main_table(self):
        """«Якщо її немає, то зазначити в основній табличці.»"""
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE rejections SET notice_24h='NOT_ESTABLISHED',"
                    " notice_24h_url='', notice_24h_title=''")
        con.commit()
        con.close()
        self.build()
        rows = {r.cells[0].text: r.cells[1].text
                for r in self.doc().tables[0].rows}
        cell = rows.get("ВИМОГА про усунення за 24 год", "")
        self.assertIn(E.NOT_ESTABLISHED, cell)
        self.assertNotIn("https", cell)

    def test_missing_td_documents_say_so(self):
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE rejections SET td_documents='[]'")
        con.commit()
        con.close()
        self.build()
        rows = {r.cells[0].text: r.cells[1].text
                for r in self.doc().tables[0].rows}
        self.assertIn(E.NOT_ESTABLISHED,
                      rows.get("ДОКУМЕНТИ ТД — де шукати вимогу", ""))


class TestLocalCopies(Base):
    """Файли лежать поруч із карткою — зберігати нічого не треба."""

    def test_documents_land_next_to_the_card(self):
        net = FakeNet()
        self.build(net=net)
        folder = self.folder
        files = [f for f in os.listdir(folder)
                 if not f.startswith(("00_", "_"))]
        # протокол + вимога 24 год + 2 документи ТД + 3 документи пропозиції
        self.assertEqual(len(files), 7, f"очікували 7 файлів, маємо {files}")
        self.assertTrue(any(f.startswith("01_rishennia") for f in files))
        self.assertTrue(any(f.startswith("02_vymoga_24h") for f in files))
        self.assertTrue(any(f.startswith("03_TD") for f in files))
        self.assertTrue(any(f.startswith("04_propozycia") for f in files))

    def test_links_become_relative_to_the_card(self):
        """Відносний шлях: тека переїде — посилання не зламається."""
        net = FakeNet()
        self.build(net=net)
        targets = self.links()
        local = [t for t in targets if not t.startswith("http")]
        self.assertTrue(local, f"немає локальних посилань: {targets}")
        for t in local:
            self.assertFalse(t.startswith("/"), "шлях має бути відносним")
            self.assertTrue(os.path.exists(os.path.join(self.folder, t)),
                            f"посилання веде в нікуди: {t}")

    def test_second_run_does_not_download_again(self):
        """Правило 44: повторний запуск не качає вдруге."""
        net = FakeNet()
        E.download_case_documents(self.db_path, self.root, legacy=net)
        first = len(net.calls)
        stats = E.download_case_documents(self.db_path, self.root, legacy=net)
        self.assertEqual(len(net.calls), first, "качало вдруге")
        self.assertEqual(stats["завантажено"], 0)
        self.assertEqual(stats["вже_були"], first)

    def test_manifest_records_every_document(self):
        """Правило 17: невдалий документ не зникає до аналізу."""
        net = FakeNet(fail={"https://p/b3"}, huge={"https://p/td2"})
        stats = E.download_case_documents(self.db_path, self.root, legacy=net)
        with open(os.path.join(self.folder, E.MANIFEST_NAME),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertEqual(len(man), 7, "у маніфесті мають бути ВСІ документи")
        self.assertEqual(man["https://p/b3"]["статус"], "НЕ_ЗАВАНТАЖЕНО")
        self.assertEqual(man["https://p/td2"]["статус"], "ЗАВЕЛИКИЙ")
        self.assertEqual(stats["помилок"], 1)
        self.assertEqual(stats["завеликі"], 1)

    def test_failed_document_still_gets_a_prozorro_link(self):
        """Не завантажилось — людина має відкрити його в Prozorro."""
        net = FakeNet(fail={"https://p/b1"})
        self.build(net=net)
        self.assertIn("https://p/b1", self.links())

    def test_downloaded_file_keeps_its_hash(self):
        net = FakeNet()
        E.download_case_documents(self.db_path, self.root, legacy=net)
        with open(os.path.join(self.folder, E.MANIFEST_NAME),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        import hashlib
        expected = hashlib.sha256(net.payload).hexdigest()
        self.assertEqual(man["https://p/proto"]["sha256"], expected)

    def test_no_network_module_is_not_a_crash(self):
        stats = E.download_case_documents(self.db_path, self.root, legacy=object())
        self.assertEqual(stats["завантажено"], 0)
        self.assertIn("причина", stats)

    def test_signatures_are_not_offered_to_the_human(self):
        """Файли .p7s — це підписи, людині вони не потрібні."""
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE rejections SET bid_documents=?",
                    (json.dumps(BID_DOCS + [{"назва": "Довідка.pdf.p7s",
                                             "url": "https://p/sig"}],
                                ensure_ascii=False),))
        con.commit()
        con.close()
        self.build()
        self.assertNotIn("https://p/sig", self.links())


class TestHumanNotesSurvive(Base):
    """Правило 36: нотатки людини не перезаписуються."""

    def test_existing_card_is_not_rebuilt(self):
        self.build()
        import docx
        d = docx.Document(self.card)
        d.add_paragraph("ВИСНОВОК ЮРИСТА: оскаржувати недоцільно.")
        d.save(self.card)
        stats = self.build()
        self.assertEqual(stats["створено"], 0)
        self.assertIn("ВИСНОВОК ЮРИСТА", self.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTdClauseRow(Base):
    """«Де шукати вимогу в ТД» — тільки структура ТД, не норми закону."""

    def test_law_and_pkmu_are_not_offered_as_td_clauses(self):
        self.build()
        rows = {r.cells[0].text: r.cells[1].text
                for r in self.doc().tables[0].rows}
        cell = rows.get("C01 · де шукати вимогу в ТД", "")
        self.assertIn("п. 1", cell)
        self.assertIn("Розділ III", cell)
        for absent in ("ст. 31", "ПКМУ", "Закону України"):
            self.assertNotIn(absent, cell,
                             f"«{absent}» у тендерній документації не міститься")

    def test_law_only_claim_says_no_td_clause(self):
        c = E.analyze_claim(
            "Пропозицію відхилено на підставі ч. 1 ст. 31 Закону України "
            "«Про публічні закупівлі».", "C01")
        self.assertTrue(c.citations)
        self.assertFalse(any(x["вид"] in E.TD_CITATION_KINDS
                             for x in c.citations))


class TestGreetingWhenFieldHoldsACompany(unittest.TestCase):
    """
    Prozorro часто кладе в «уповноважену особу» саму фірму. Відмінювати її
    як ім'я не можна: у листі виходить «Шановний Ірбудтрансе!».
    """

    def setUp(self):
        import lead_machine_v1 as L
        self.p = E.Personalizer(L)

    COMPANIES = [
        ("Тов Ірбудтранс", "ТЗОВ «ІРБУДТРАНС»"),
        ("ІРБУДТРАНС", "ТЗОВ «ІРБУДТРАНС»"),
        ("ТОВ «БУДІНВЕСТ»", "ТОВ «БУДІНВЕСТ»"),
        ("Будівельна компанія Гранд", "ТОВ «БК ГРАНД»"),
        ("КНП «Тячівська РЛ»", "КНП «ТЯЧІВСЬКА РЛ»"),
        ("Приватне підприємство Дах", "ПП «ДАХ»"),
        ("Науково-виробниче об'єднання Темп", "НВО «ТЕМП»"),
    ]
    PEOPLE = [
        ("Іван Петрович Коваленко", "ТОВ «БУДІНВЕСТ»", "Іване Петровичу"),
        ("Коваленко Іван Петрович", "ТОВ «БУДІНВЕСТ»", "Іване Петровичу"),
        ("ФОП Коваленко Іван Петрович", "ФОП Коваленко І.П.", "Іване Петровичу"),
        ("Петренко Олена Іванівна", "ТОВ «АЛЬФА»", "Олено Іванівно"),
    ]

    def test_company_name_gets_a_neutral_greeting(self):
        for raw, company in self.COMPANIES:
            with self.subTest(raw=raw):
                text, conf, src = self.p.greeting(raw, company)
                self.assertEqual(text, E.SAFE_GREETING,
                                 f"«{raw}» відмінюється як ім'я")
                self.assertEqual(src, "COMPANY_NOT_PERSON")
                self.assertEqual(conf, "LOW")

    def test_real_person_still_gets_their_name(self):
        for raw, company, expected in self.PEOPLE:
            with self.subTest(raw=raw):
                text, conf, _ = self.p.greeting(raw, company)
                self.assertIn(expected, text, f"втратили ім'я в «{raw}»")
                self.assertEqual(conf, "HIGH")

    def test_fop_with_a_name_is_a_person_not_a_company(self):
        self.assertFalse(E.looks_like_company("ФОП Коваленко Іван",
                                              "ФОП Коваленко І.П."))

    def test_the_letter_body_starts_with_the_neutral_greeting(self):
        """Перевіряємо ЛИСТ, а не лише функцію: саме його читає клієнт."""
        import lead_machine_v1 as L
        tmp = tempfile.mkdtemp()
        try:
            cfg = E.Config(db_path=os.path.join(tmp, "d.db"),
                           out_dir=os.path.join(tmp, "o"), mode="TEST",
                           min_delay=0, max_delay=0, verbose=False)
            eng = E.LeadEngine(cfg, E.FixtureSource([]),
                               E.DryRunTransport(os.path.join(tmp, "e")), L)
            eng.repo.mark_history_migrated("тест")
            eng.ingest([T.facts(company="ТЗОВ «ІРБУДТРАНС»",
                                name="Тов Ірбудтранс")])
            eng.build_queue()
            body = eng.db.one("SELECT body_rendered FROM outreach_events")
            eng.close()
            first = body["body_rendered"].splitlines()[0]
            self.assertEqual(first, E.SAFE_GREETING)
            self.assertNotIn("Ірбудтранс", first)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEngineVersionGate(unittest.TestCase):
    """Стара версія на Диску має називатись вголос, а не падати пізніше."""

    def test_every_required_name_exists(self):
        missing = [n for n in E.REQUIRED_API if not hasattr(E, n)]
        self.assertEqual(missing, [], "REQUIRED_API називає те, чого немає")

    def test_version_is_set(self):
        self.assertTrue(E.ENGINE_VERSION)
        self.assertTrue(E.ENGINE_BUILD)

    def test_card_functions_are_guarded(self):
        for name in ("build_case_cards", "download_case_documents"):
            self.assertIn(name, E.REQUIRED_API,
                          f"{name} має перевірятись при завантаженні")


class TestTestRecipients(unittest.TestCase):
    def test_the_corrected_address_is_used(self):
        self.assertIn("t.shchaslyva@gmail.com", E.TEST_RECIPIENTS)
        self.assertNotIn("t.shchasliva@gmail.com", E.TEST_RECIPIENTS)

    def test_no_duplicates_in_the_rotation(self):
        self.assertEqual(len(E.TEST_RECIPIENTS), len(set(E.TEST_RECIPIENTS)))


class TestFallbackSplitter(unittest.TestCase):
    """Запасний розділювач не має падати: він працює, коли ядро недоступне."""

    TXT = ("Пропозиція не відповідає п. 1 Розділу III, оскільки надана довідка "
           "про наявність працівників не містить персоналу. "
           "Також строк дії банківської гарантії менший ніж вимагав п. 7.")

    def test_it_runs_without_the_legacy_module(self):
        parts = E.split_protocol(self.TXT, legacy=None)
        self.assertEqual(len(parts), 2)

    def test_abbreviations_do_not_split_sentences(self):
        parts = E.split_protocol("Учасник не надав довідку за п. 5 Додатку 2.",
                                 legacy=None)
        self.assertEqual(len(parts), 1, "розрізало по крапці у «п. 5»")

    def test_a_legacy_that_returns_nothing_falls_back(self):
        class Empty:
            @staticmethod
            def split_claims(text, max_claims=25):
                return []
        self.assertTrue(E.split_protocol(self.TXT, legacy=Empty()))


class TestCaseFolderOnDrive(unittest.TestCase):
    """Справа має лягати саме туди, куди її чекає людина."""

    def test_run_writes_the_case_folder_next_to_the_database(self):
        import lead_machine_v1 as L
        import types as _t
        dysk = tempfile.mkdtemp()
        try:
            vyhid = os.path.join(dysk, "vyhid")
            os.makedirs(vyhid)
            db = os.path.join(dysk, "leads.db")
            E.run_colab(day="2026-08-07", dry_run=True, db_path=db,
                        out_dir=vyhid, leads=[T.facts(reason=PROTO)])
            net = _t.SimpleNamespace(
                make_session=lambda: object(),
                get_bytes=lambda s, u, tries=3: b"%PDF-1.4 x")
            E.build_case_cards(db, dysk, legacy=L)
            folder = os.path.join(dysk, "spravy", "UA-2026-08-28-000001-a")
            self.assertTrue(os.path.isdir(folder),
                            f"теки справи немає; є: {os.listdir(dysk)}")
            self.assertTrue(os.path.isfile(
                os.path.join(folder, "00_KARTKA_12345678.docx")))
            self.assertTrue(os.path.isfile(db), "база поруч зі справами")
        finally:
            shutil.rmtree(dysk, ignore_errors=True)


class TestOneFolderPerCase(Base):
    """
    Вимога власника продукту: картка має лежати ТАМ САМО, де документи.
    Підпапку dokumenty/ прибрано — картку в ній не знаходили.
    """

    def test_card_and_documents_live_in_the_same_folder(self):
        self.build(net=FakeNet())
        names = set(os.listdir(self.folder))
        self.assertIn("00_KARTKA_12345678.docx", names)
        self.assertTrue(any(n.startswith("01_rishennia") for n in names),
                        f"рішення замовника не в теці картки: {sorted(names)}")
        self.assertTrue(any(n.startswith("03_TD") for n in names),
                        "документів ТД немає поруч із карткою")
        self.assertTrue(any(n.startswith("04_propozycia") for n in names),
                        "документів пропозиції немає поруч із карткою")

    def test_no_subfolder_is_created(self):
        self.build(net=FakeNet())
        subdirs = [n for n in os.listdir(self.folder)
                   if os.path.isdir(os.path.join(self.folder, n))]
        self.assertEqual(subdirs, [], f"зайві підпапки: {subdirs}")

    def test_the_card_sorts_first(self):
        self.build(net=FakeNet())
        first = sorted(os.listdir(self.folder))[0]
        self.assertTrue(first.startswith("00_"),
                        f"першим у теці лежить {first}, а не картка")

    def test_links_are_plain_neighbour_filenames(self):
        self.build(net=FakeNet())
        local = [t for t in self.links() if not t.startswith("http")]
        self.assertTrue(local, "немає локальних посилань")
        for t in local:
            self.assertNotIn("/", t, "посилання має бути на сусідній файл")
            self.assertTrue(os.path.isfile(os.path.join(self.folder, t)),
                            f"посилання веде в нікуди: {t}")


class TestLayoutMigration(unittest.TestCase):
    """Стара розкладка на Диску має піднятись, а нотатки — вціліти."""

    NOTE = "ВИСНОВОК ЮРИСТА: оскаржувати недоцільно."

    def setUp(self):
        import docx
        self.dysk = tempfile.mkdtemp()
        self.case = os.path.join(self.dysk, "spravy", "UA-2026-08-07-008904-a")
        self.sub = os.path.join(self.case, E.LEGACY_DOC_SUBDIR)
        os.makedirs(self.sub)
        d = docx.Document()
        d.add_paragraph("СТАРА КАРТКА")
        d.add_paragraph(self.NOTE)
        d.save(os.path.join(self.case, "kartka_37406974.docx"))
        with open(os.path.join(self.case, "zapyty_37406974.md"),
                  "w", encoding="utf-8") as fh:
            fh.write("# запити")
        for f in ("01_rishennia_01_Протокол.pdf", "03_TD_02_ТД.pdf"):
            with open(os.path.join(self.sub, f), "wb") as fh:
                fh.write(b"%PDF-1.4 old")
        with open(os.path.join(self.sub, "manifest.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"https://p/proto": {
                "назва": "Протокол.pdf", "статус": "ЗАВАНТАЖЕНО",
                "файл": "01_rishennia_01_Протокол.pdf", "байтів": 11}},
                fh, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.dysk, ignore_errors=True)

    def test_documents_move_up_next_to_the_card(self):
        E.migrate_case_layout(self.dysk, verbose=False)
        names = set(os.listdir(self.case))
        self.assertIn("01_rishennia_01_Протокол.pdf", names)
        self.assertIn("03_TD_02_ТД.pdf", names)
        self.assertFalse(os.path.isdir(self.sub), "підпапка лишилась")

    def test_human_notes_survive_the_rename(self):
        """Правило 36: картку ПЕРЕЙМЕНОВУЄМО, а не створюємо заново."""
        E.migrate_case_layout(self.dysk, verbose=False)
        import docx
        path = os.path.join(self.case, "00_KARTKA_37406974.docx")
        self.assertTrue(os.path.isfile(path))
        text = "\n".join(p.text for p in docx.Document(path).paragraphs)
        self.assertIn(self.NOTE, text)
        self.assertFalse(os.path.exists(
            os.path.join(self.case, "kartka_37406974.docx")))

    def test_manifest_is_renamed_so_local_links_keep_working(self):
        E.migrate_case_layout(self.dysk, verbose=False)
        self.assertTrue(os.path.isfile(
            os.path.join(self.case, E.MANIFEST_NAME)))
        local = E._local_files(self.case)
        self.assertEqual(local.get("https://p/proto"),
                         ("01_rishennia_01_Протокол.pdf", ""))

    def test_running_it_twice_changes_nothing(self):
        E.migrate_case_layout(self.dysk, verbose=False)
        before = sorted(os.listdir(self.case))
        stats = E.migrate_case_layout(self.dysk, verbose=False)
        self.assertEqual(sorted(os.listdir(self.case)), before)
        self.assertEqual(stats["перенесено_справ"], 0)

    def test_a_name_clash_keeps_both_files(self):
        """Правило 14: нічого не перезаписуємо мовчки."""
        with open(os.path.join(self.case, "00_KARTKA_37406974.docx"),
                  "wb") as fh:
            fh.write("нова картка".encode("utf-8"))
        stats = E.migrate_case_layout(self.dysk, verbose=False)
        self.assertGreaterEqual(stats["конфліктів"], 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.case, "kartka_37406974.docx")),
            "стару картку з нотатками не можна втрачати")

    def test_building_cards_migrates_first(self):
        """Побудова карток не має створювати ДРУГУ картку поруч зі старою."""
        db = os.path.join(self.dysk, "leads.db")
        E.Db(db).close()
        E.build_case_cards(db, self.dysk, legacy=None)
        kartky = [f for f in os.listdir(self.case) if "KARTKA" in f.upper()]
        self.assertEqual(len(kartky), 1, f"карток стало {kartky}")


class TestCaseFoldersReport(Base):
    """«Де картка» має відповідати скрипт, а не людина."""

    def _report(self, root):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            E.case_folders(root)
        return buf.getvalue()

    def test_it_lists_the_card_and_the_documents(self):
        self.build(net=FakeNet())
        out = self._report(self.root)
        self.assertIn("UA-2026-08-28-000001-a", out)
        self.assertIn("КАРТКА: 00_KARTKA_12345678.docx", out)
        self.assertIn("01_rishennia", out)

    def test_it_says_plainly_when_there_is_no_card(self):
        os.makedirs(os.path.join(self.root, "spravy", "UA-X"), exist_ok=True)
        self.assertIn("КАРТКИ НЕМАЄ", self._report(self.root))

    def test_missing_root_is_explained_not_crashed(self):
        self.assertIn("ще немає", self._report(os.path.join(self.tmp, "нема")))
