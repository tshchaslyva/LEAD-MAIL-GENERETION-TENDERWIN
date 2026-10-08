# -*- coding: utf-8 -*-
"""
ДОКУМЕНТИ СПРАВИ: повнота, надійність, копії для NotebookLM.

Власник продукту: «перестав закачувати всі файли пропозиції відхиленого
учасника, всю ТД та рішення замовника». Кожен клас тут — один знайдений
і відтворений дефект.

Запуск:  python3 -m unittest test_dokumenty
"""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tenderwin_lead_engine as E   # noqa: E402

E.ALLOW_COLAB_SECRETS = False

import test_lead_engine as T        # noqa: E402


def doc(i, prefix, ext="pdf", url=True, dtype="", date="2026-08-01"):
    d = {"id": f"{prefix}{i}", "title": f"{prefix} {i}.{ext}",
         "documentType": dtype, "datePublished": f"{date}T10:00:00+03:00",
         "dateModified": f"{date}T10:00:00+03:00"}
    if url:
        d["url"] = f"https://public-docs.prozorro.gov.ua/get/{prefix}{i}"
    return d


def tender_eu():
    """aboveThresholdEU: велика ТД, пропозиція в чотирьох конвертах."""
    mine = {
        "id": "bid-MY",
        "tenderers": [{"name": "ТОВ БУДІНВЕСТ",
                       "identifier": {"id": "12345678",
                                      "legalName": "ТОВ «БУДІНВЕСТ»"},
                       "contactPoint": {"name": "Коваленко Іван Петрович",
                                        "email": "office@budinvest.ua"}}],
        "documents": [doc(i, "Пропозиція") for i in range(1, 31)],
        "qualificationDocuments": [doc(i, "Кваліфікація") for i in range(1, 41)],
        "financialDocuments": [doc(1, "Кошторис", "xlsx", dtype="billOfQuantity"),
                               doc(2, "Цінова", dtype="commercialProposal")],
        "eligibilityDocuments": [doc(i, "Відповідність") for i in range(1, 11)],
        "lotValues": [{"relatedLot": "lot-1", "value": {"amount": 100.0}}],
    }
    winner = {"id": "bid-WIN",
              "tenderers": [{"identifier": {"id": "87654321"}}],
              "documents": [doc(i, "ЧУЖА") for i in range(1, 21)]}
    award = {"id": "award-1", "bid_id": "bid-MY", "status": "unsuccessful",
             "lotID": "lot-1", "title": "Відхилено",
             "description": "Не надано довідку.", "value": {"amount": 100.0},
             "suppliers": mine["tenderers"],
             "documents": [doc(1, "Протокол"), doc(2, "Обгрунтування", "docx")]}
    return {"id": "a" * 32, "tenderID": "UA-2026-09-19-000123-a",
            "title": "Ремонт", "procuringEntity": {"name": "КНП"},
            "value": {"amount": 1000.0},
            "lots": [{"id": "lot-1", "value": {"amount": 1000.0}}],
            "documents": [doc(i, "ТД") for i in range(1, 131)],
            "bids": [winner, mine], "awards": [award]}


class TestPropozyciyaUsiKonverty(unittest.TestCase):
    """Дефект 1: бралась лише частина пропозиції — без кошторису."""

    def test_all_four_envelopes_are_collected(self):
        t = tender_eu()
        docs, problem = E.collect_bid_documents(t, "bid-MY")
        self.assertEqual(problem, "")
        self.assertEqual(len(docs), 30 + 40 + 2 + 10)
        envelopes = {d["конверт"] for d in docs}
        self.assertEqual(envelopes, set(E.BID_ENVELOPES))

    def test_the_bill_of_quantities_is_there(self):
        docs, _ = E.collect_bid_documents(tender_eu(), "bid-MY")
        self.assertTrue(any("Кошторис" in d["назва"] for d in docs),
                        "кошторис із financialDocuments не потрапив у справу")


class TestChuzhaPropozyciya(unittest.TestCase):
    """Дефект 2: без bid_id у справу качалась пропозиція ІНШОГО учасника."""

    def test_missing_bid_id_gives_nothing_not_someone_else(self):
        docs, problem = E.collect_bid_documents(tender_eu(), "")
        self.assertEqual(docs, [])
        self.assertEqual(problem, "BID_ID_NOT_ESTABLISHED")

    def test_unknown_bid_id_gives_nothing(self):
        docs, problem = E.collect_bid_documents(tender_eu(), "bid-NOPE")
        self.assertEqual(docs, [])
        self.assertEqual(problem, "BID_NOT_FOUND_IN_TENDER")

    def test_source_data_is_not_mutated(self):
        """legacy bid_docs_for дописував у список Prozorro на місці."""
        t = tender_eu()
        before = copy.deepcopy(t)
        E.collect_bid_documents(t, "bid-MY")
        E.collect_bid_documents(t, "bid-MY")
        self.assertEqual(t, before)


class TestBezTykhykhLimitiv(unittest.TestCase):
    """Дефект 3: ліміти 120/200/60 мовчки обрізали документи."""

    def test_no_count_limit(self):
        many = [doc(i, "ТД") for i in range(1, 501)]
        self.assertEqual(len(E.pack_documents(many)), 500)

    def test_a_document_without_a_link_stays_visible(self):
        packed = E.pack_documents([doc(1, "Конфіденційний", url=False)])
        self.assertEqual(len(packed), 1)
        self.assertTrue(packed[0]["без_посилання"])

    def test_a_signature_file_stays_visible(self):
        packed = E.pack_documents([doc(1, "Підпис", "p7s")])
        self.assertEqual(len(packed), 1)
        self.assertTrue(packed[0]["підпис"])

    def test_version_marker_goes_before_the_extension(self):
        """«Документ.pdf (редакція 2)» не відкривався подвійним кліком."""
        packed = E.pack_documents([doc(1, "ТД"), doc(1, "ТД")])
        self.assertEqual(packed[1]["назва"], "ТД 1 (редакція 2).pdf")


class TestFactsCilkom(unittest.TestCase):
    """Наскрізно через справжній розбір відхилення."""

    def setUp(self):
        import lead_machine_v1 as L
        self.L = L
        self.real = L.download_protocol
        L.download_protocol = lambda s, a, f: "Не надано довідку."
        self.src = E.LegacyLeadSource.__new__(E.LegacyLeadSource)
        self.src.m = L
        self.src.case_dir = tempfile.mkdtemp()
        self.src.download_protocols = True
        self.src.verbose = False
        self.src.skipped, self.src.warnings = [], []

    def tearDown(self):
        self.L.download_protocol = self.real
        shutil.rmtree(self.src.case_dir, ignore_errors=True)

    def test_everything_reaches_the_facts(self):
        t = tender_eu()
        facts, _ = self.src._facts(object(), t, "awards", t["awards"][0],
                                   self.src.case_dir)
        self.assertEqual(len(facts.td_documents), 130)
        self.assertEqual(len(facts.bid_documents), 82)
        self.assertEqual(len(facts.award_documents), 2)
        self.assertFalse(any("ЧУЖА" in d["назва"] for d in facts.bid_documents))

    def test_a_bid_problem_is_reported_not_swallowed(self):
        t = tender_eu()
        aw = dict(t["awards"][0])
        aw.pop("bid_id")
        self.src._facts(object(), t, "awards", aw, self.src.case_dir)
        self.assertTrue(any("BID_ID_NOT_ESTABLISHED" in w
                            for _, w in self.src.warnings))


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
        self.saved = (E.HTTP_BACKOFF, E.MAX_CASE_BYTES, E.MAX_DOC_BYTES)
        E.HTTP_BACKOFF = (0, 0, 0, 0)

    def tearDown(self):
        E.HTTP_BACKOFF, E.MAX_CASE_BYTES, E.MAX_DOC_BYTES = self.saved
        try:
            self.eng.close()
        except Exception:                                           # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def facts(self, td=3, nbid=3, naward=2, **kw):
        f = T.facts(**kw)
        f.td_documents = [{"назва": f"ТД {i}.pdf", "url": f"https://p/td{i}"}
                          for i in range(td)]
        f.bid_documents = [{"назва": f"Пропозиція {i}.pdf",
                            "url": f"https://p/b{i}"} for i in range(nbid)]
        f.award_documents = [{"назва": f"Рішення {i}.pdf",
                              "url": f"https://p/a{i}"} for i in range(naward)]
        f.qa_documents = []
        return f

    def ingest(self, f):
        self.eng.ingest([f])
        row = self.eng.db.one(
            "SELECT rejection_id FROM rejections WHERE award_id=?", (f.award_id,))
        return row["rejection_id"]

    def row(self, rid):
        return self.eng.db.one("SELECT * FROM rejections WHERE rejection_id=?",
                               (rid,))

    def download(self, ids, net=None, **kw):
        return E.download_case_documents(self.db, self.root,
                                         legacy=net or Merezha(),
                                         rejection_ids=ids, **kw)

    def manifest(self, ua="UA-2026-08-28-000001-a"):
        with open(os.path.join(E.case_archive(self.root, ua), E.MANIFEST_NAME),
                  encoding="utf-8") as fh:
            return json.load(fh)


class Merezha:
    """Підставна мережа legacy: рахує звернення, вміє ламатись."""

    def __init__(self, fail=(), huge=(), payload=b"%PDF-1.4 x"):
        self.fail, self.huge, self.payload = set(fail), set(huge), payload
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


class TestStariZapysyLikuyutsya(Baza):
    """Дефект 4: справа, записана старою версією, лишалась без документів."""

    def test_an_empty_old_record_gets_the_lists(self):
        stara = self.facts(td=0, nbid=0, naward=0)
        rid = self.ingest(stara)
        self.assertEqual(json.loads(self.row(rid)["td_documents"]), [])
        self.ingest(self.facts(td=5, nbid=4, naward=2))
        r = self.row(rid)
        self.assertEqual(len(json.loads(r["td_documents"])), 5)
        self.assertEqual(len(json.loads(r["bid_documents"])), 4)
        self.assertEqual(len(json.loads(r["award_documents"])), 2)

    def test_new_td_amendments_are_added_nothing_removed(self):
        rid = self.ingest(self.facts(td=3))
        pizni = self.facts(td=0)
        pizni.td_documents = [{"назва": "Зміни до ТД.pdf",
                               "url": "https://p/zmina"}]
        self.ingest(pizni)
        urls = [d["url"] for d in json.loads(self.row(rid)["td_documents"])]
        self.assertEqual(len(urls), 4, "старі документи зникли або нове не додалось")
        self.assertIn("https://p/zmina", urls)

    def test_new_documents_reset_the_case_state(self):
        rid = self.ingest(self.facts())
        self.download([rid])
        self.assertEqual(self.row(rid)["docs_state"], "COMPLETE")
        nova = self.facts()
        nova.td_documents.append({"назва": "Зміни.pdf", "url": "https://p/new"})
        self.ingest(nova)
        self.assertIsNone(self.row(rid)["docs_state"],
                          "нові документи не повернули справу в роботу")

    def test_a_filled_protocol_link_is_not_overwritten(self):
        f = self.facts()
        f.protocol_url = "https://p/perwyj"
        rid = self.ingest(f)
        g = self.facts()
        g.protocol_url = "https://p/inshyj"
        self.ingest(g)
        self.assertEqual(self.row(rid)["protocol_url"], "https://p/perwyj")

    def test_merge_is_idempotent(self):
        a = [{"url": "u1"}, {"url": "u2"}]
        merged, n = E.merge_document_lists(a, a)
        self.assertEqual((len(merged), n), (2, 0))


class TestZavantazhennya(Baza):
    def test_no_count_limit_on_download(self):
        rid = self.ingest(self.facts(td=200, nbid=120, naward=5))
        st = self.download([rid], make_pdf_copies=False)
        self.assertEqual(st["завантажено"], 325)
        g = st["групи"]
        self.assertEqual((g["04_propozycia"]["є"], g["04_propozycia"]["очікувалось"]),
                         (120, 120))
        self.assertEqual((g["03_TD"]["є"], g["03_TD"]["очікувалось"]), (200, 200))

    def test_decision_and_proposal_go_first_when_space_runs_out(self):
        """Межа теки — першим лягає рішення і пропозиція, а не креслення ТД."""
        E.MAX_CASE_BYTES = 10 * len(b"%PDF-1.4 x")
        rid = self.ingest(self.facts(td=20, nbid=5, naward=2))
        st = self.download([rid], make_pdf_copies=False)
        g = st["групи"]
        self.assertEqual(g["01_rishennia"]["є"], 2)
        self.assertEqual(g["04_propozycia"]["є"], 5)
        man = self.manifest()
        skipped = [v for v in man.values() if v["статус"] == "ПРОПУЩЕНО_ЛІМІТ"]
        self.assertEqual(len(skipped), 17, "пропущене має бути видно")

    def test_a_document_without_a_link_is_in_the_manifest(self):
        f = self.facts(td=1)
        f.td_documents.append({"назва": "Конфіденційне.pdf", "url": "",
                               "document_id": "conf1", "без_посилання": True})
        rid = self.ingest(f)
        st = self.download([rid], make_pdf_copies=False)
        man = self.manifest()
        self.assertEqual(man["id:conf1"]["статус"], "НЕМАЄ_ПОСИЛАННЯ")
        self.assertEqual(st["без_посилання"], 1)

    def test_version_files_keep_their_extension(self):
        f = self.facts(td=0)
        f.td_documents = [
            {"назва": "ТД.pdf", "url": "https://p/v1", "опубліковано": "2026-07-01"},
            {"назва": "ТД.pdf", "url": "https://p/v2", "опубліковано": "2026-07-18"}]
        rid = self.ingest(f)
        self.download([rid], make_pdf_copies=False)
        names = os.listdir(E.case_archive(self.root, "UA-2026-08-28-000001-a"))
        second = [n for n in names if "ред. 2026-07-18" in n]
        self.assertEqual(len(second), 1, names)
        self.assertTrue(second[0].endswith(".pdf"), second[0])

    def test_a_failed_download_is_retried_next_run(self):
        """Дефект 5: після невдачі справа вважалась повною назавжди."""
        rid = self.ingest(self.facts(td=2, nbid=0, naward=0))
        self.download([rid], net=Merezha(fail={"https://p/td1"}),
                      make_pdf_copies=False)
        self.assertEqual(self.row(rid)["docs_state"], "INCOMPLETE")
        todo, _ = E.cases_needing_attention(self.db, self.root, [])
        self.assertIn(rid, todo, "недороблену справу не взято на доліковування")
        net = Merezha()
        self.download(todo, net=net, make_pdf_copies=False)
        self.assertEqual(net.calls, ["https://p/td1"], "качали зайве")
        self.assertEqual(self.row(rid)["docs_state"], "COMPLETE")
        todo2, _ = E.cases_needing_attention(self.db, self.root, [])
        self.assertNotIn(rid, todo2)

    def test_a_hopeless_document_does_not_block_completeness(self):
        rid = self.ingest(self.facts(td=1, nbid=0, naward=0))
        E.MAX_DOC_BYTES = 5
        self.download([rid], net=Merezha(huge={"https://p/td0"}),
                      make_pdf_copies=False)
        self.assertEqual(self.manifest()["https://p/td0"]["статус"], "ЗАВЕЛИКИЙ")
        self.assertEqual(self.row(rid)["docs_state"], "COMPLETE")

    def test_empty_lists_are_not_called_complete(self):
        rid = self.ingest(self.facts(td=0, nbid=0, naward=0))
        self.download([rid], make_pdf_copies=False)
        self.assertEqual(self.row(rid)["docs_state"], "EMPTY_LISTS")


class FakeResp:
    def __init__(self, code, body=b"", headers=None):
        self.status_code, self.body = code, body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


class FakeSession:
    """requests.Session, що відповідає за сценарієм."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        seq = self.script.get(url) or [FakeResp(200, b"%PDF ok")]
        return seq.pop(0) if len(seq) > 1 else seq[0]


class TestKodyPomylok(unittest.TestCase):
    """Правило 13: причина кожної невдачі — окремим кодом."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = E.HTTP_BACKOFF
        E.HTTP_BACKOFF = (0, 0, 0, 0)

    def tearDown(self):
        E.HTTP_BACKOFF = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetch(self, script, url="u", max_bytes=10**6):
        s = FakeSession(script)
        return E.fetch_document(None, s, url, max_bytes, self.tmp), s

    def test_404_is_permanent(self):
        res, s = self.fetch({"u": [FakeResp(404)]})
        self.assertEqual(res["статус"], "HTTP_404")
        self.assertEqual(len(s.calls), 1, "404 не треба повторювати")
        self.assertIn("HTTP_404", E.PERMANENT_FAILURES)

    def test_429_then_success(self):
        res, s = self.fetch({"u": [FakeResp(429, headers={"Retry-After": "0"}),
                                   FakeResp(200, b"%PDF ok")]})
        self.assertEqual(res["статус"], "ЗАВАНТАЖЕНО")
        self.assertEqual(len(s.calls), 2)
        with open(res["файл"], "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF ok")

    def test_persistent_5xx_is_retryable(self):
        res, s = self.fetch({"u": [FakeResp(503)]})
        self.assertEqual(res["статус"], "HTTP_5XX")
        self.assertEqual(len(s.calls), E.HTTP_TRIES)
        self.assertIn("HTTP_5XX", E.RETRYABLE_FAILURES)

    def test_too_big_is_stopped_while_streaming(self):
        res, _ = self.fetch({"u": [FakeResp(200, b"x" * 5000)]}, max_bytes=1000)
        self.assertEqual(res["статус"], "ЗАВЕЛИКИЙ")
        self.assertEqual(os.listdir(self.tmp), [], "недокачаний файл лишився")

    def test_sha256_is_recorded(self):
        import hashlib
        res, _ = self.fetch({"u": [FakeResp(200, b"%PDF ok")]})
        self.assertEqual(res["sha256"], hashlib.sha256(b"%PDF ok").hexdigest())


class TestNotebookLM(unittest.TestCase):
    """Формати — за офіційним переліком Google (звірено 20.09.2026)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, data=b"x"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_accepted_formats_need_no_copy(self):
        paths = [self.put(n) for n in ("a.pdf", "b.docx", "c.pptx", "d.jpg",
                                       "e.csv", "f.txt", "g.png")]
        res = E.make_notebooklm_copies(paths)
        self.assertTrue(all(v["статус"] == "НЕ_ПОТРІБНО" for v in res.values()),
                        res)

    def test_excel_is_not_accepted_and_gets_a_copy(self):
        self.assertNotIn(".xlsx", E.NOTEBOOKLM_ACCEPTS)
        self.assertNotIn(".doc", E.NOTEBOOKLM_ACCEPTS)
        if E._soffice_bin() is None:
            self.skipTest("немає LibreOffice")
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Розбирання покриття", 125.40])
        path = os.path.join(self.tmp, "Кошторис.xlsx")
        wb.save(path)
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "СТВОРЕНО")
        self.assertTrue(res[path]["файли"][0].endswith(".pdf"))

    def test_xml_gets_a_text_copy(self):
        path = self.put("dani.xml", "<a>Кошторис</a>".encode("utf-8"))
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "СТВОРЕНО")
        with open(res[path]["файли"][0], encoding="utf-8") as fh:
            self.assertIn("Кошторис", fh.read())

    def test_an_unknown_format_is_reported_and_kept(self):
        path = self.put("kreslennya.dwg")
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ")
        self.assertTrue(os.path.exists(path), "оригінал має лишитись")

    # --- архіви -------------------------------------------------------------
    def make_zip(self, name, members):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as zf:
            for arcname, data in members:
                zf.writestr(arcname, data)
        return path

    def test_a_zip_is_unpacked_and_its_contents_handled(self):
        path = self.make_zip("Документи.zip", [("Довідка.pdf", b"%PDF-1.4 a"),
                                               ("sub/Лист.txt", b"text")])
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "РОЗПАКОВАНО")
        dest = os.path.join(self.tmp, "Документи" + E.UNPACKED_SUFFIX)
        self.assertTrue(os.path.isfile(os.path.join(dest, "Довідка.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "Лист.txt")))

    def test_cp866_names_from_ukrainian_windows_are_readable(self):
        # У CP866 немає «і/ї/є/ґ» — старий Windows замінює їх латиницею.
        # Тут перевіряємо саме декодування: назва з літер, які в CP866 є.
        path = os.path.join(self.tmp, "arch.zip")
        name = "Протокол Замовника.pdf"
        raw_name = name.encode("cp866")
        # zipfile у Python сам ставить прапорець UTF-8 для не-ASCII назв,
        # тож справжній «віндовий» архів складаємо байтами: пишемо ASCII-
        # заглушку тієї ж довжини і підміняємо її байтами CP866.
        stub = b"Q" * len(raw_name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(stub.decode("ascii"), b"%PDF")
        data = buf.getvalue().replace(stub, raw_name)
        with open(path, "wb") as fh:
            fh.write(data)
        E.make_notebooklm_copies([path])
        dest = os.path.join(self.tmp, "arch" + E.UNPACKED_SUFFIX)
        self.assertIn(name, os.listdir(dest))

    def test_zip_slip_cannot_escape_the_folder(self):
        path = self.make_zip("zly.zip", [("../../vtik.txt", b"x"),
                                         ("normal.txt", b"ok")])
        E.make_notebooklm_copies([path])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "..", "vtik.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(self.tmp), "vtik.txt")))
        dest = os.path.join(self.tmp, "zly" + E.UNPACKED_SUFFIX)
        self.assertEqual(os.listdir(dest), ["normal.txt"])

    def test_an_archive_bomb_is_refused(self):
        saved = E.ARCHIVE_MAX_FILES
        E.ARCHIVE_MAX_FILES = 3
        try:
            path = self.make_zip("bomba.zip", [(f"{i}.txt", b"x") for i in range(10)])
            res = E.make_notebooklm_copies([path])
            self.assertEqual(res[path]["статус"], "ПІДОЗРІЛИЙ_АРХІВ")
        finally:
            E.ARCHIVE_MAX_FILES = saved

    def test_a_corrupted_zip_is_reported(self):
        path = self.put("bytyj.zip", b"PK\x03\x04 bytyj")
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "НЕ_РОЗПАКОВАНО")

    def test_rar_without_a_tool_says_what_to_install(self):
        import shutil as sh
        real = sh.which
        sh.which = lambda name: None
        try:
            path = self.put("arch.rar", b"Rar!")
            res = E.make_notebooklm_copies([path])
        finally:
            sh.which = real
        self.assertEqual(res[path]["статус"], "НЕМАЄ_РОЗПАКОВУВАЧА")
        self.assertIn("p7zip", res[path]["деталі"])

    # --- підписи КЕП --------------------------------------------------------
    def cms(self, detached=False, stream=False):
        if not shutil.which("openssl"):
            self.skipTest("немає openssl")
        work = tempfile.mkdtemp(dir=self.tmp)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-nodes", "-keyout", f"{work}/k.pem", "-out",
                        f"{work}/c.pem", "-days", "1", "-subj", "/CN=t"],
                       capture_output=True, check=True)
        with open(f"{work}/doc.pdf", "wb") as fh:
            fh.write(b"%PDF-1.4 vkladenyj")
        cmd = ["openssl", "cms", "-sign", "-in", f"{work}/doc.pdf",
               "-signer", f"{work}/c.pem", "-inkey", f"{work}/k.pem",
               "-outform", "DER", "-binary", "-out", f"{work}/s.p7s"]
        if not detached:
            cmd.append("-nodetach")
        if stream:
            cmd.append("-stream")
        subprocess.run(cmd, capture_output=True, check=True)
        with open(f"{work}/s.p7s", "rb") as fh:
            return fh.read()

    def test_an_attached_signature_gives_back_the_document(self):
        path = self.put("Довідка.pdf.p7s", self.cms())
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "ВИТЯГНУТО")
        inner = os.path.join(self.tmp, "Довідка.pdf")
        with open(inner, "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1.4 vkladenyj")

    def test_a_streamed_ber_signature_also_works(self):
        raw = self.cms(stream=True)
        self.assertEqual(E.extract_signed_content(raw), b"%PDF-1.4 vkladenyj")

    def test_a_detached_signature_is_named_as_such(self):
        path = self.put("sign.p7s", self.cms(detached=True))
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "ПІДПИС_БЕЗ_ДОКУМЕНТА")

    def test_garbage_in_a_p7s_does_not_crash(self):
        path = self.put("bytyj.p7s", b"\x30\x80\xff\xff")
        res = E.make_notebooklm_copies([path])
        self.assertEqual(res[path]["статус"], "ПІДПИС_БЕЗ_ДОКУМЕНТА")


class TestPidtyaguvannyaPerelikiv(Baza):
    """Справа старої версії: переліки підтягуються з Prozorro повторно."""

    def test_an_empty_case_is_refilled_from_prozorro(self):
        f = self.facts(td=0, nbid=0, naward=0, bid="bid-MY", award="award-1")
        f.tender.tender_id = "a" * 32
        rid = self.ingest(f)
        t = tender_eu()

        class Net(Merezha):
            API = "https://public-api.prozorro.gov.ua/api/2.5/tenders"

            def get_json(self, session, url, params=None, tries=4):
                return {"data": t} if url.endswith("a" * 32) else None

        st = E.rehydrate_documents(self.db, Net(), [rid])
        self.assertEqual(st["поповнено"], 1)
        r = self.row(rid)
        self.assertEqual(len(json.loads(r["td_documents"])), 130)
        self.assertEqual(len(json.loads(r["bid_documents"])), 82)
        self.assertEqual(len(json.loads(r["award_documents"])), 2)

    def test_without_network_it_says_so(self):
        st = E.rehydrate_documents(self.db, None, [])
        self.assertTrue(st["причини"])


class TestZvitLyudskoyu(unittest.TestCase):
    def test_the_report_shows_counts_and_the_drive_path(self):
        stats = {"завантажено": 5, "вже_були": 0,
                 "групи": {"04_propozycia": {"очікувалось": 82, "є": 80,
                                             "не_вдалось": 2},
                           "03_TD": {"очікувалось": 130, "є": 130,
                                     "не_вдалось": 0}},
                 "теки": ["/content/drive/MyDrive/TENDERWIN/spravy/_arhiv/UA-1"]}
        text = E.documents_report(stats)
        self.assertIn("пропозиція учасника", text)
        self.assertIn("80 з 82", text)
        self.assertIn("не вдалось 2", text)
        self.assertIn("Мій диск › TENDERWIN › spravy › _arhiv › UA-1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestZvitNeVvodytVOmanu(Baza):
    """404 — це не «збій мережі, повториться сам»."""

    def test_a_404_is_reported_as_final_not_as_a_network_glitch(self):
        rid = self.ingest(self.facts(td=2, nbid=0, naward=0))

        class Sess:
            def get(self, url, stream=False, timeout=None):
                return FakeResp(404 if url.endswith("td1") else 200, b"%PDF")

        class Net(Merezha):
            def make_session(self):
                return Sess()

        st = self.download([rid], net=Net(), make_pdf_copies=False)
        self.assertEqual(st.get("недоступні"), 1)
        self.assertEqual(st["помилок"], 0)
        text = E.documents_report(st)
        self.assertIn("повтор марний", text)
        self.assertNotIn("спробує ще раз", text)


class TestObryvColab(Baza):
    """Правило 14: жодного тихого перезапису, навіть після обриву сеансу."""

    def test_an_existing_different_file_is_never_overwritten(self):
        folder = E.case_archive(self.root, "UA-X")
        os.makedirs(folder)
        with open(os.path.join(folder, "03_TD_001_ТД.pdf"), "wb") as fh:
            fh.write(b"old content")
        name = E._free_name(folder, "03_TD_001_ТД.pdf",
                            __import__("hashlib").sha256(b"new").hexdigest())
        self.assertEqual(name, "03_TD_001_ТД (2).pdf")

    def test_the_same_content_is_not_duplicated_after_a_crash(self):
        """Файл ліг на Диск, маніфест не встиг — повтор не робить дубль."""
        rid = self.ingest(self.facts(td=3, nbid=0, naward=0))
        self.download([rid], make_pdf_copies=False)
        folder = E.case_archive(self.root, "UA-2026-08-28-000001-a")
        before = sorted(os.listdir(folder))
        os.remove(os.path.join(folder, E.MANIFEST_NAME))   # «обрив» до маніфесту
        self.download([rid], make_pdf_copies=False)
        after = sorted(os.listdir(folder))
        self.assertEqual(before, after, "після повтору зʼявились дублікати")

    def test_the_manifest_is_written_atomically(self):
        folder = E.case_archive(self.root, "UA-Y")
        os.makedirs(folder)
        path = os.path.join(folder, E.MANIFEST_NAME)
        E._write_manifest(path, {"u": {"статус": "ЗАВАНТАЖЕНО"}})
        self.assertEqual(E._read_manifest(path)["u"]["статус"], "ЗАВАНТАЖЕНО")
        self.assertFalse(os.path.exists(path + ".tmp"))
