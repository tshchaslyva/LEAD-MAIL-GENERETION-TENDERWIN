# -*- coding: utf-8 -*-
"""
Журнал прогонів у Google Таблиці.

Мережі тут немає: клієнт Google підмінено. Перевіряємо ПОВЕДІНКУ —
що саме движок надсилає в API і чого не надсилає ніколи.

Запуск:  python3 -m unittest test_zhurnal
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


# --------------------------------------------------------------------------
#  Підроблений Google Sheets: тримає заголовок і рядки, записує виклики.
# --------------------------------------------------------------------------
class FakeSheets:
    def __init__(self, header=None, rows=None, fail=None):
        self.header = list(header or [])
        self.rows = [list(r) for r in (rows or [])]
        self.appended: list = []
        self.updated: list = []
        self.created: list = []
        self.fail = fail

    # --- інтерфейс, який використовує движок ---
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def create(self, body=None):
        self.created.append(body)
        sheet = body["sheets"][0]["data"][0]["rowData"][0]["values"]
        self.header = [v["userEnteredValue"]["stringValue"] for v in sheet]
        return _Exec({"spreadsheetId": "SID-123"})

    def get(self, spreadsheetId=None, range=None):
        if range.endswith("!1:1"):
            return _Exec({"values": [self.header]} if self.header else {})
        # діапазон ключів: A2:A
        col = range.split("!")[1].split("2")[0]
        n = 0
        for ch in col:
            n = n * 26 + (ord(ch) - 64)
        n -= 1
        return _Exec({"values": [[r[n] if n < len(r) else ""]
                                 for r in self.rows]})

    def append(self, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, body=None):
        if self.fail:
            raise self.fail
        self.appended.extend(body["values"])
        self.rows.extend(body["values"])
        return _Exec({})

    def batchUpdate(self, spreadsheetId=None, body=None):
        if self.fail:
            raise self.fail
        self.updated.extend(body["data"])
        for item in body["data"]:
            cell = item["range"].split("!")[1]
            letter = "".join(c for c in cell if c.isalpha())
            line = int("".join(c for c in cell if c.isdigit()))
            n = 0
            for ch in letter:
                n = n * 26 + (ord(ch) - 64)
            n -= 1
            row = self.rows[line - 2]
            while len(row) <= n:
                row.append("")
            row[n] = item["values"][0][0]
        return _Exec({})


class _Exec:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


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
        f = T.facts(reason="Учасником не надано довідку про наявність "
                           "працівників, що вимагалося пунктом 1 Додатку 2.")
        f.tender.title = "Капітальний ремонт покрівлі"
        f.tender.buyer = "КНП «Міська лікарня № 3»"
        f.lot_value_amount = 4045480.0
        f.lots_total = 1
        f.lot_resolved = "YES"
        f.lot_id = "lot-1"
        f.bid_amount = 2251738.0
        f.winner_amount = 2609900.0
        f.notice_24h = "YES"
        self.eng.ingest([f])
        self.eng.build_queue()
        self.eng.send()
        self.ids = list(self.eng.touched_rejections)
        self.prev = E.JOURNAL_ENABLED
        E.JOURNAL_ENABLED = True

    def tearDown(self):
        E.JOURNAL_ENABLED = self.prev
        try:
            self.eng.close()
        except Exception:                                           # noqa: BLE001
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def zapysaty(self, fake, **kw):
        real = E._journal_service
        E._journal_service = lambda: fake
        try:
            return E.journal_write(self.db, self.root, self.ids, **kw)
        finally:
            E._journal_service = real

    def stvoryty(self, fake):
        real = E._journal_service
        E._journal_service = lambda: fake
        try:
            return E.journal_create(self.db)
        finally:
            E._journal_service = real


class TestStvorennya(Baza):
    def test_the_engine_creates_the_sheet_with_its_own_header(self):
        fake = FakeSheets()
        res = self.stvoryty(fake)
        self.assertEqual(res["статус"], "СТВОРЕНО")
        self.assertIn(E.JOURNAL_KEY_COLUMN, fake.header)
        for name in ("Закупівля", "Учасник", "ЄДРПОУ", "Плата до АМКУ"):
            self.assertIn(name, fake.header)
        for name in E.JOURNAL_HUMAN_COLUMNS:
            self.assertIn(name, fake.header)

    def test_the_id_is_remembered_next_to_the_database(self):
        self.stvoryty(FakeSheets())
        self.assertEqual(E.journal_id(self.db), "SID-123")
        path = os.path.join(self.tmp, E.JOURNAL_ID_FILE)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["spreadsheet_id"], "SID-123")

    def test_a_second_call_does_not_create_a_second_sheet(self):
        self.stvoryty(FakeSheets())
        fake = FakeSheets()
        res = self.stvoryty(fake)
        self.assertEqual(res["статус"], "УЖЕ_БУВ")
        self.assertEqual(fake.created, [], "створено ДРУГУ таблицю")

    def test_the_narrow_scope_is_used(self):
        """drive.file, а не доступ до всіх таблиць власника."""
        self.assertEqual(E.SHEETS_SCOPE,
                         "https://www.googleapis.com/auth/drive.file")
        self.assertIn(E.SHEETS_SCOPE, E.oauth_scopes())


class TestZapys(Baza):
    def header(self):
        return [n for n, _ in E.JOURNAL_COLUMNS] + E.JOURNAL_HUMAN_COLUMNS

    def test_one_row_per_case(self):
        fake = FakeSheets(header=self.header())
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        res = self.zapysaty(fake)
        self.assertEqual(res["статус"], "ЗАПИСАНО")
        self.assertEqual(res["додано"], 1)
        row = dict(zip(fake.header, fake.appended[0]))
        self.assertEqual(row["Закупівля"], "UA-2026-08-28-000001-a")
        self.assertEqual(row["ЄДРПОУ"], "12345678")
        self.assertIn("4 045 480", row["Очікувана вартість лота"])
        self.assertIn("24 280", row["Плата до АМКУ"])
        self.assertIn("дешевше за переможця", row["Різниця з переможцем"])

    def test_a_repeat_run_updates_and_does_not_duplicate(self):
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        fake = FakeSheets(header=self.header())
        self.zapysaty(fake)
        self.assertEqual(len(fake.rows), 1)
        res = self.zapysaty(fake)
        self.assertEqual(res["додано"], 0)
        self.assertEqual(res["оновлено"], 1)
        self.assertEqual(len(fake.rows), 1, "зʼявився дублікат рядка")


class TestVashiKolonky(Baza):
    """Головна обіцянка: колонки людини движок не чіпає."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)

    def test_unknown_columns_are_never_written(self):
        header = [E.JOURNAL_KEY_COLUMN, "Закупівля", "МОЯ КОЛОНКА", "ЄДРПОУ"]
        fake = FakeSheets(header=header)
        self.zapysaty(fake)
        row = dict(zip(header, fake.appended[0]))
        self.assertEqual(row["МОЯ КОЛОНКА"], "")
        self.assertEqual(row["Закупівля"], "UA-2026-08-28-000001-a")

    def test_reordered_columns_still_work(self):
        header = ["ЄДРПОУ", "Мої нотатки", "Закупівля", E.JOURNAL_KEY_COLUMN]
        fake = FakeSheets(header=header)
        self.zapysaty(fake)
        row = dict(zip(header, fake.appended[0]))
        self.assertEqual(row["ЄДРПОУ"], "12345678")
        self.assertEqual(row["Закупівля"], "UA-2026-08-28-000001-a")

    def test_a_deleted_column_is_simply_skipped(self):
        header = [E.JOURNAL_KEY_COLUMN, "Закупівля"]
        fake = FakeSheets(header=header)
        res = self.zapysaty(fake)
        self.assertEqual(res["статус"], "ЗАПИСАНО")
        self.assertEqual(len(fake.appended[0]), 2)

    def test_my_notes_survive_the_next_run(self):
        header = [E.JOURNAL_KEY_COLUMN, "Закупівля", "Результат"]
        fake = FakeSheets(header=header)
        self.zapysaty(fake)
        fake.rows[0][2] = "домовились на аудит"
        self.zapysaty(fake)
        self.assertEqual(fake.rows[0][2], "домовились на аудит")

    def test_without_the_key_column_nothing_is_written(self):
        """Без ключа повторний прогін плодив би дублікати — краще стоп."""
        fake = FakeSheets(header=["Закупівля", "ЄДРПОУ"])
        res = self.zapysaty(fake)
        self.assertEqual(res["статус"], "ПОМИЛКА")
        self.assertIn(E.JOURNAL_KEY_COLUMN, res["чому"])
        self.assertEqual(fake.appended, [])


class TestZbiyNeLamaeProhin(Baza):
    """Вітрина не має зупиняти прогін (правило 13)."""

    def test_a_network_failure_returns_a_code_not_an_exception(self):
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        fake = FakeSheets(header=[n for n, _ in E.JOURNAL_COLUMNS],
                          fail=RuntimeError("503 backend error"))
        res = self.zapysaty(fake)
        self.assertEqual(res["статус"], "ПОМИЛКА")
        self.assertEqual(res["код"], "SHEETS_TEMPORARY")
        self.assertIn("не Ваша помилка", res["чому"])

    def test_a_scope_problem_says_what_to_do(self):
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        fake = FakeSheets(header=[n for n, _ in E.JOURNAL_COLUMNS],
                          fail=RuntimeError("403 insufficient permissions"))
        res = self.zapysaty(fake)
        self.assertEqual(res["код"], "SHEETS_SCOPE_MISSING")
        self.assertIn("setup_gmail", res["чому"])

    def test_disabled_journal_is_a_no_op(self):
        E.JOURNAL_ENABLED = False
        res = E.journal_write(self.db, self.root, self.ids)
        self.assertEqual(res["статус"], "ПРОПУЩЕНО")

    def test_disabled_journal_does_not_change_the_mail_scopes(self):
        """Вимкнений журнал не має ламати відправку пошти."""
        E.JOURNAL_ENABLED = False
        self.assertNotIn(E.SHEETS_SCOPE, E.oauth_scopes())
        self.assertIn(E.GMAIL_SEND_SCOPE, E.oauth_scopes())


class TestFiltrProhonu(Baza):
    def test_empty_list_writes_nothing(self):
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        fake = FakeSheets(header=[n for n, _ in E.JOURNAL_COLUMNS])
        real = E._journal_service
        E._journal_service = lambda: fake
        try:
            res = E.journal_write(self.db, self.root, [])
        finally:
            E._journal_service = real
        self.assertEqual(res["статус"], "НЕМАЄ_ЧОГО_ПИСАТИ")
        self.assertEqual(fake.appended, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPomylkyGoogleLyudskoyu(unittest.TestCase):
    """Правило 13: КОД, що сталося, чи можна далі, що робити. Не traceback."""

    REAL_403 = (
        '<HttpError 403 when requesting '
        'https://sheets.googleapis.com/v4/spreadsheets?alt=json returned '
        '"Google Sheets API has not been used in project 604019504962 before '
        'or it is disabled. Enable it by visiting '
        'https://console.developers.google.com/apis/api/sheets.googleapis.com/'
        'overview?project=604019504962 then retry.". Details: '
        '"[{\'@type\': \'type.googleapis.com/google.rpc.ErrorInfo\', '
        '\'reason\': \'SERVICE_DISABLED\', \'domain\': \'googleapis.com\'}]">')

    def test_the_api_not_enabled_error_is_explained(self):
        info = E._google_error(RuntimeError(self.REAL_403))
        self.assertEqual(info["код"], "SHEETS_API_NOT_ENABLED")
        self.assertIn("не увімкнено", info["що"])
        self.assertIn("Enable", info["що_робити"])
        self.assertEqual(
            info["посилання"],
            "https://console.developers.google.com/apis/api/"
            "sheets.googleapis.com/overview?project=604019504962")

    def test_the_link_is_clean_without_trailing_punctuation(self):
        info = E._google_error(RuntimeError(self.REAL_403))
        self.assertFalse(info["посилання"].endswith(("'", '"', ",", ".")),
                         info["посилання"])

    def test_a_missing_scope_tells_how_to_reauthorize(self):
        info = E._google_error(RuntimeError(
            '<HttpError 403 ... "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT">'))
        self.assertEqual(info["код"], "SHEETS_SCOPE_MISSING")
        self.assertIn("reset_gmail_token", info["що_робити"])

    def test_a_deleted_spreadsheet_is_recognised(self):
        info = E._google_error(RuntimeError(
            "<HttpError 404 when requesting ... not found>"))
        self.assertEqual(info["код"], "JOURNAL_NOT_FOUND")
        self.assertIn("zhurnal.json", info["що_робити"])

    def test_a_rate_limit_says_nothing_is_lost(self):
        info = E._google_error(RuntimeError("<HttpError 429 ...>"))
        self.assertEqual(info["код"], "SHEETS_RATE_LIMIT")
        self.assertIn("не втрачені", info["що_робити"])

    def test_an_unknown_error_still_gets_a_code(self):
        info = E._google_error(RuntimeError("щось геть незнайоме"))
        self.assertEqual(info["код"], "SHEETS_API_ERROR")
        self.assertTrue(info["що_робити"])

    def test_the_journal_failure_says_the_run_is_not_affected(self):
        err = E._journal_fail(RuntimeError(self.REAL_403))
        text = str(err)
        self.assertIn("JOURNAL_UNAVAILABLE", text)
        self.assertIn("можна продовжити", text)
        self.assertIn("Листи, картки і документи", text)
        self.assertNotIn("Traceback", text)


class TestStvorennyaNePadaeTraceback(Baza):
    """journal_create більше не випускає сирий HttpError назовні."""

    def test_a_google_error_becomes_a_readable_message(self):
        class Bad(FakeSheets):
            def create(self, body=None):
                raise RuntimeError(TestPomylkyGoogleLyudskoyu.REAL_403)

        with self.assertRaises(E.EngineError) as ctx:
            self.stvoryty(Bad())
        text = str(ctx.exception)
        self.assertIn("Google Sheets API не увімкнено", text)
        self.assertIn("console.developers.google.com", text)

    def test_write_reports_the_code_and_keeps_the_run_alive(self):
        with open(os.path.join(self.tmp, E.JOURNAL_ID_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": "SID-123"}, fh)
        fake = FakeSheets(header=[n for n, _ in E.JOURNAL_COLUMNS],
                          fail=RuntimeError(TestPomylkyGoogleLyudskoyu.REAL_403))
        res = self.zapysaty(fake)
        self.assertEqual(res["статус"], "ПОМИЛКА")
        self.assertEqual(res["код"], "SHEETS_API_NOT_ENABLED")
        self.assertIn("console.developers.google.com", res["чому"])


class TestDozvolyTokena(unittest.TestCase):
    """Токен памʼятає свої дозволи — інакше 403 прилітає посеред роботи."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prev_dir, self.prev_j = E.TOKEN_DIR, E.JOURNAL_ENABLED
        E.TOKEN_DIR = self.tmp

    def tearDown(self):
        E.TOKEN_DIR, E.JOURNAL_ENABLED = self.prev_dir, self.prev_j
        shutil.rmtree(self.tmp, ignore_errors=True)

    def poklasty(self, scopes):
        data = {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
        if scopes is not None:
            data["scopes"] = scopes
        E.save_token(data, self.tmp)

    def test_a_mail_only_token_is_reported_as_missing_the_sheet_scope(self):
        E.JOURNAL_ENABLED = True
        self.poklasty([E.GMAIL_SEND_SCOPE, E.GMAIL_READ_SCOPE])
        self.assertEqual(E.missing_scopes(), [E.SHEETS_SCOPE])

    def test_a_full_token_is_fine(self):
        E.JOURNAL_ENABLED = True
        self.poklasty([E.GMAIL_SEND_SCOPE, E.GMAIL_READ_SCOPE, E.SHEETS_SCOPE])
        self.assertEqual(E.missing_scopes(), [])

    def test_with_the_journal_off_nothing_is_missing(self):
        E.JOURNAL_ENABLED = False
        self.poklasty([E.GMAIL_SEND_SCOPE, E.GMAIL_READ_SCOPE])
        self.assertEqual(E.missing_scopes(), [])

    def test_an_old_token_without_the_list_is_not_guessed_about(self):
        """Старий токен без переліку — не вигадуємо, що там усередині."""
        E.JOURNAL_ENABLED = True
        self.poklasty(None)
        self.assertIsNone(E.token_scopes())
        self.assertEqual(E.missing_scopes(), [])

    def test_reset_removes_the_token_file(self):
        self.poklasty([E.GMAIL_SEND_SCOPE])
        res = E.reset_gmail_token(self.tmp)
        self.assertTrue(res["видалено"])
        self.assertFalse(os.path.exists(res["файл"]))
        self.assertIsNone(E.load_token())
