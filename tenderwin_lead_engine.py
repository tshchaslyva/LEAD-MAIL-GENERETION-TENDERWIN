# -*- coding: utf-8 -*-
"""
=============================================================================
 TENDERWIN LEAD ENGINE v0.1  ·  НАДІЙНИЙ ПЕРШИЙ КОНТАКТ
=============================================================================

 ЩО ЦЕ

 Шар надійності над наявним лід-генератором. Він НЕ шукає закупівлі заново —
 пошук уміє `lead_machine_v1.py`, і він лишається. Цей файл відповідає за те,
 чого там не було: стан у базі, дедуплікацію, стоп-лист, чергу, безпечну
 відправку через Gmail, звірку після збою і звіт.

 ГОЛОВНІ ГАРАНТІЇ

 1. Одна компанія отримує ОДИН перший лист. Назавжди.
    Не має значення, скільки в неї адрес, закупівель, днів і перезапусків.
 2. Тестові листи цю гарантію НЕ витрачають.
 3. Повтор ніколи не створює другий лист.
 4. Немає бази — немає відправки. Краще пропущений день, ніж день без
    дедуплікації.
 5. Бойова відправка вмикається двома прапорцями і пройденим preflight.
 6. Рядок у журнал пишеться ПІСЛЯ підтвердження від Gmail, не до.

 ДОСТУП ДО ПОШТИ — ВАЖЛИВО

 Ключ службового акаунта (.json) НЕ потрібен, і в більшості організацій
 Google Workspace його й не вдасться створити: політика
 iam.disableServiceAccountKeyCreation увімкнена за замовчуванням саме щоб
 такі файли не витікали.

 Робочий шлях — OAuth власника скриньки:

   mint_oauth_token(CLIENT_ID, CLIENT_SECRET)  ->  секрет GMAIL_OAUTH_JSON

 Потрібен OAuth client ID типу «Desktop app». Це НЕ службовий акаунт і
 НЕ ключ; заборона на ключі його не стосується. Плюс він відкриває одну
 вашу скриньку, а не пошту всього домену, як робило б делегування.

 Службовий акаунт лишається запасним шляхом для тих, у кого ключ уже є.

 ЯК ЗАПУСТИТИ В COLAB

   КЛІТИНКА 1   !pip -q install requests openpyxl \
                  google-api-python-client google-auth
   КЛІТИНКА 2   from google.colab import drive; drive.mount('/content/drive')
   КЛІТИНКА 3   from tenderwin_lead_engine import run_colab
   КЛІТИНКА 4   run_colab(day='2026-08-28', dry_run=True)   # без пошти
   КЛІТИНКА 5   секрет GMAIL_OAUTH_JSON -> run_colab(..., dry_run=False)

 БЛОКИ ФАЙЛА — один блок, одна відповідальність

   0  НАЛАШТУВАННЯ           те єдине, що редагує Vitalii
   1  ІМПОРТИ, ЧАС, ID
   2  ТИПІЗОВАНІ ПОМИЛКИ
   3  СХЕМА БАЗИ
   4  БАЗА І РЕПОЗИТОРІЙ     єдина точка запису стану
   5  ЖУРНАЛ ПОДІЙ           append-only
   6  ІДЕНТИЧНІСТЬ КОМПАНІЇ  ЄДРПОУ, не e-mail
   7  ПЕРСОНАЛІЗАЦІЯ         кличний відмінок і підстава, з довірою
   8  ШАБЛОНИ                незмінні версії
   9  ЕКСПЕРИМЕНТ A/B        стабільне призначення
  10  ПОЛІТИКА ЗВЕРНЕННЯ     придатність, стоп-лист, перший дотик
  11  ЧЕРГА ВІДПРАВКИ        стани і резервування
  12  ТРАНСПОРТ              DryRun / Gmail
  13  ВІДПРАВНИК І ЗВІРКА
  14  ВІДПОВІДІ І СТОП
  15  PREFLIGHT              гейт бойового режиму
  16  ЗВІТ                   XLSX, тільки для людини
  17  ДЖЕРЕЛО ЛІДІВ          адаптер до lead_machine_v1 і фікстури
  18  ОРКЕСТРАТОР
  19  ТОЧКА ВХОДУ

=============================================================================
"""
from __future__ import annotations

# ============================================================================
#  БЛОК 0. НАЛАШТУВАННЯ — ЄДИНЕ МІСЦЕ, ЯКЕ ВИ РЕДАГУЄТЕ
# ============================================================================

# --- Режим -----------------------------------------------------------------
# TEST_MODE = True  -> доставка ТІЛЬКИ на адреси з TEST_RECIPIENTS
# Щоб надсилати реальним клієнтам, потрібні ОБИДВА:
#     TEST_MODE = False   І   LIVE_SEND_ENABLED = True
# і додатково пройдений preflight. Одна випадкова зміна нічого не вмикає.
TEST_MODE = True
LIVE_SEND_ENABLED = False

# --- Хто відправляє ---------------------------------------------------------
SENDER_EMAIL = "vitalii@tenderwin.com.ua"
SENDER_NAME = "Віталій Щасливий"

# --- Тестові скриньки (білий список) ---------------------------------------
# У тестовому режимі лист не піде НІКУДИ, крім цих адрес.
TEST_RECIPIENTS = [
    "ppvetik1@gmail.com",
    "mike.vital.llc@gmail.com",
    "mike.vital.lls@gmail.com",
    "t.shchaslyva@gmail.com",
    "saveukrainekdk@gmail.com",
    "vitalijsolovskij@gmail.com",
    "ppvetik4@gmail.com",
    "kiev.development.company@gmail.com",
    "rebuilduaprojects@gmail.com",
    "ppvetik.ecomerce@gmail.com",
    "yuriybigunbhd@gmail.com",
    "budinvestproect@gmail.com",
    "vtgglibalsolutions@gmail.com",
    "vitaliishchaslyvyi@gmail.com",
    "budrec1@gmail.com",
    "vtsolution6@gmail.com",
]

# --- Де живе стан -----------------------------------------------------------
# У Colab поставте шлях на Диск, інакше база зникне разом із сеансом.
DB_PATH = "tenderwin_leads.db"
OUT_DIR = "vyhid"

# --- Політика першого контакту ---------------------------------------------
# FIRST_TOUCH_ONLY: одна компанія — один перший лист, довічно.
# Cooldown і повторні звернення навмисно НЕ реалізовані (§5 ТЗ).
FIRST_TOUCH_ONLY = True

# --- Підстава відхилення в листі -------------------------------------------
# "verbatim"  — дослівна цитата з протоколу. РЕКОМЕНДОВАНО і за замовчуванням.
# "classifier"— назва категорії R01..R14. На перевірці 16 формулювань дала
#               2 суттєві помилки, зокрема натяк на підстави рівня статті 17.
#               Для бойового режиму заблоковано.
# "both"      — категорія плюс цитата.
REASON_MODE = "verbatim"

# --- Експеримент ------------------------------------------------------------
EXPERIMENT_ID = "FIRST_TOUCH_AB_2026Q3"
# Рішення власника продукту 2026-08-30: усі листи йдуть КОРОТКИМ варіантом B.
# Вимкнено, а не видалено: A/B можна ввімкнути назад одним значенням, і вже
# збережені призначення при цьому не змінюються (тригер VARIANT_ASSIGNMENT_
# IMMUTABLE), тому історія експерименту лишається читабельною.
EXPERIMENT_ENABLED = False         # False -> усім варіант B

# --- Темп відправки ---------------------------------------------------------
# Пауза потрібна для керованої обробки черги, а не для обходу фільтрів.
MIN_SEND_DELAY_SECONDS = 120
MAX_SEND_DELAY_SECONDS = 180
MAX_SENDS_PER_RUN = 25

# --- Gmail ------------------------------------------------------------------
# Секрети живуть у Colab Secrets або у змінних середовища. У код НЕ вставляти.
#
# ОСНОВНИЙ ШЛЯХ — GMAIL_OAUTH_JSON. Робиться через mint_oauth_token() і
# потребує лише OAuth client ID типу «Desktop app». Ключів не завантажується,
# тому заборона на створення ключів службових акаунтів його не стосується.
#
# ЗАПАСНИЙ — GMAIL_SA_JSON. Тільки якщо ключ службового акаунта у вас уже є.
# Створити новий у більшості організацій Workspace не вийде: політика
# iam.disableServiceAccountKeyCreation увімкнена за замовчуванням. Крім того,
# цей шлях вимагає домен-делегування, тобто прав суперадміністратора.
GMAIL_OAUTH_ENV = "GMAIL_OAUTH_JSON"  # основний: токен власника скриньки
GMAIL_SA_ENV = "GMAIL_SA_JSON"        # запасний: ключ службового акаунта
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

#: Доступ до таблиць. drive.file — найвужчий з можливих: додаток бачить
#: ЛИШЕ ті файли, які створив сам. Решта Диска для нього не існує.
#: Тому журнал створює движок, а не людина: за ширший дозвіл
#: (auth/spreadsheets — усі таблиці власника) платити немає за що.
#: Перевірено 10.09.2026 за офіційною документацією Google Sheets API v4.
SHEETS_SCOPE = "https://www.googleapis.com/auth/drive.file"

#: Журнал прогонів у Google Таблиці. ВИМКНЕНО за замовчуванням: вмикання
#: змінює набір дозволів і вимагає одноразової повторної авторизації.
JOURNAL_ENABLED = False
JOURNAL_TITLE = "TenderWin — журнал прогонів"
JOURNAL_SHEET = "Справи"
JOURNAL_ID_FILE = "zhurnal.json"          # поруч із базою; це НЕ секрет
JOURNAL_KEY_COLUMN = "Ключ"               # технічний ключ рядка
GMAIL_DOMAIN = "tenderwin.com.ua"

# --- Джерело лідів ----------------------------------------------------------
# Модуль наявного лід-генератора. Звідти беремо те, що вже працює:
# сканування Prozorro, розбір претензій, кличний відмінок, контакти.
LEGACY_MODULE = "lead_machine_v1"


# ============================================================================
#  БЛОК 1. ІМПОРТИ, ЧАС, ІДЕНТИФІКАТОРИ
# ============================================================================
import base64
import csv
import hashlib
import importlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Iterable, Optional, Protocol
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 10

#: Версія движка. Друкується при завантаженні, щоб було видно, ЯКИЙ файл
#: насправді працює. Без цього стара копія на Диску падає через десять
#: клітинок незрозумілим AttributeError замість чесного «у вас стара версія».
ENGINE_VERSION = "0.13.0"
ENGINE_BUILD = "2026-09-20"

#: Функції, без яких ноутбук не запрацює. colab_setup звіряє їх наявність
#: ОДРАЗУ після копіювання, а не чекає, поки людина дійде до потрібної клітинки.
REQUIRED_API = (
    "go", "colab_setup", "colab_tests", "colab_state", "colab_check",
    "setup_gmail", "why_no_letters", "reset_dry_run_sends",
    "build_case_cards", "download_case_documents",
    "migrate_case_layout", "case_folders", "run_folder", "convert_to_pdf",
    "calculate_amcu_fee", "validate_service_card_consistency",
    "delivery_display", "round_up_to_increment",
    "analyze_protocol", "build_search_order", "keywords_line",
    "person_name_block", "letter_html", "followup_sentence",
    "case_archive", "convert_many_to_pdf", "write_run_index",
    "journal_create", "journal_write", "journal_id", "oauth_scopes",
    "cases_needing_attention", "journal_check", "reset_gmail_token",
    "collect_bid_documents", "pack_documents", "merge_document_lists",
    "make_notebooklm_copies", "rehydrate_documents", "documents_report",
    "extract_signed_content", "fetch_document",
    "migrate_legacy_history", "whats_i_need", "whats_this_file",
)
KYIV = ZoneInfo("Europe/Kyiv")          # §26: ніякого захардкодженого UTC+3


def now() -> datetime:
    return datetime.now(KYIV)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def new_run_id() -> str:
    """Кожен прогін отримує власний ID (правило 14)."""
    return f"run-{now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


# ============================================================================
#  БЛОК 2. ТИПІЗОВАНІ ПОМИЛКИ
# ----------------------------------------------------------------------------
#  Кожна помилка каже: що сталося, на що вплинуло, чи можна далі, що робити.
#  Мовчазних except у движку немає.
# ============================================================================
@dataclass(frozen=True)
class EngineError(Exception):
    code: str
    what: str
    affected: str
    may_continue: bool
    next_step: str

    def __str__(self) -> str:
        stan = "можна продовжити" if self.may_continue else "ЗУПИНЕНО"
        return (f"[{self.code}] {self.what}\n"
                f"    вплив : {self.affected}\n"
                f"    статус: {stan}\n"
                f"    далі  : {self.next_step}")


def _e(code, what, affected, may_continue, next_step) -> EngineError:
    return EngineError(code, what, affected, may_continue, next_step)


ERR = {
    "STORAGE_UNAVAILABLE": lambda d: _e(
        "STORAGE_UNAVAILABLE", f"Немає доступу до бази стану: {d}",
        "Дедуплікація і стоп-лист непрацездатні", False,
        "Перевірте шлях і права. Відправку заблоковано навмисно: краще "
        "пропущений день, ніж день без дедуплікації."),
    "SCHEMA_MISMATCH": lambda f, x: _e(
        "SCHEMA_MISMATCH", f"Версія схеми {f}, движок очікує {x}",
        "Уся робота з базою", False, "Виконайте міграцію або вкажіть іншу базу."),
    "IDENTITY_NOT_ESTABLISHED": lambda d: _e(
        "IDENTITY_NOT_ESTABLISHED", f"Не встановлено ЄДРПОУ/ІПН: {d}",
        "Один лід", True,
        "Лід іде в MANUAL_REVIEW_REQUIRED. E-mail і назва ключем бути не можуть."),
    "REASON_NOT_ESTABLISHED": lambda d: _e(
        "REASON_NOT_ESTABLISHED", f"Немає дослівної підстави відхилення: {d}",
        "Один лід", True,
        "Лід іде в MANUAL_REVIEW_REQUIRED. Писати людині про її відхилення, "
        "не знаючи слів замовника, не можна."),
    "TEST_RECIPIENT_NOT_ALLOWED": lambda m: _e(
        "TEST_RECIPIENT_NOT_ALLOWED", f"Доставка на «{m}» у тестовому режимі заборонена",
        "Один лист, НЕ надіслано", True,
        "Додайте адресу в TEST_RECIPIENTS або зʼясуйте, чому реальна адреса "
        "клієнта потрапила в доставку."),
    "LIVE_SEND_BLOCKED": lambda r: _e(
        "LIVE_SEND_BLOCKED", "Бойову відправку заблоковано preflight: " + "; ".join(r),
        "Уся бойова розсилка", False, "Закрийте перелічені пункти і повторіть preflight."),
    "FIRST_TOUCH_EXISTS": lambda c: _e(
        "FIRST_TOUCH_EXISTS", f"Компанія {c} вже отримувала перший лист",
        "Один лід", True, "Очікувана поведінка політики FIRST_TOUCH_ONLY."),
    "PLACEHOLDER_IN_LETTER": lambda f: _e(
        "PLACEHOLDER_IN_LETTER",
        f"У готовому листі лишились незаповнені мітки: {f}",
        "Один лист, НЕ надіслано", True,
        "Лист із міткою на кшталт [Ім'я] клієнту надсилати не можна. "
        "Лід іде на ручний перегляд."),
    "CARD_DATA_CONTRADICTION": lambda f: _e(
        "INTERNAL_DATA_CONTRADICTION",
        f"Дані картки суперечать самі собі: {f}",
        "Одна службова картка", True,
        "Картку не сформовано навмисно. Усуньте причину суперечності — "
        "інакше картка показувала б неправдиву картину."),
    "QUEUE_INSERT_FAILED": lambda m: _e(
        "QUEUE_INSERT_FAILED", f"База не прийняла запис звернення: {m}",
        "Один лід", True,
        "Це не політика дедуплікації, а помилка цілісності даних. "
        "Покажіть цей текст розробнику."),
    "COMPANY_SUPPRESSED": lambda c, s: _e(
        "COMPANY_SUPPRESSED", f"Компанія {c} у стоп-листі: {s}",
        "Один лід", True, "Автоматичні звернення заборонені."),
    "SEND_FAILED": lambda d: _e(
        "SEND_FAILED", f"Gmail відхилив відправку: {d}",
        "Один лист", True,
        "Статус SEND_FAILED, повтор дозволено. Другий лист не створюється."),
    "DELIVERY_UNKNOWN": lambda o: _e(
        "DELIVERY_UNKNOWN", f"Невідомо, чи пішов лист {o}",
        "Один лист", True,
        "Запустіть звірку. Сліпого повтору не буде."),
    "TRANSPORT_UNAVAILABLE": lambda d: _e(
        "TRANSPORT_UNAVAILABLE", f"Транспорт Gmail недоступний: {d}",
        "Уся відправка цього прогону", False,
        "Перевірте бібліотеки та ключ службового акаунта."),
    "JOURNAL_UNAVAILABLE": lambda d: _e(
        "JOURNAL_UNAVAILABLE", d,
        "Тільки журнал у Google Таблиці. Листи, картки і документи "
        "працюють як завжди", True,
        "Виправте за підказкою вище і запустіть клітинку ще раз. "
        "Прогін E.go() від цього не залежить."),
    "LEGACY_UNAVAILABLE": lambda d: _e(
        "LEGACY_UNAVAILABLE", f"Не вдалося підключити джерело лідів: {d}",
        "Пошук відхилень", False,
        f"Покладіть {LEGACY_MODULE}.py поруч або передайте ліди вручну."),
}

# ============================================================================
#  БЛОК 3. СХЕМА БАЗИ
# ----------------------------------------------------------------------------
#  Дублі блокує САМА БАЗА (унікальні індекси і тригери), а не код.
#  Перевірка «if row exists» у Python — не захист: між перевіркою і записом
#  може статися що завгодно.
#  Портованість на PostgreSQL: тільки TEXT/INTEGER, час ISO-8601,
#  часткові унікальні індекси є в обох СУБД.
# ============================================================================
SCHEMA_SQL = r"""
-- =============================================================================
--  TENDERWIN LEAD ENGINE — SQLITE MVP SCHEMA  v1
--  §1  SQLite = канонічний стан. XLSX/CSV = тільки експорт.
--  §21 Дублі блокує БАЗА (constraints/indexes), не код.
--  Портованість: PostgreSQL — тільки TEXT/INTEGER, ISO-8601 час,
--  часткові унікальні індекси (є і в SQLite 3.8+, і в PostgreSQL).
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ---------- версія схеми ------------------------------------------------------
CREATE TABLE schema_meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

-- ---------- 3. КАНОНІЧНА ІДЕНТИЧНІСТЬ КОМПАНІЇ -------------------------------
CREATE TABLE companies (
  company_id        TEXT PRIMARY KEY,            -- uuid4
  edrpou_norm       TEXT NOT NULL,               -- нормалізований ЄДРПОУ/ІПН
  company_name      TEXT NOT NULL,
  first_seen_at     TEXT NOT NULL,
  CHECK (length(edrpou_norm) BETWEEN 8 AND 10)
);
-- §3: первинний ключ дедуплікації — ЄДРПОУ, НЕ e-mail
CREATE UNIQUE INDEX ux_companies_edrpou ON companies(edrpou_norm);

-- ---------- 3. КОНТАКТИ: одна компанія — багато адрес ------------------------
CREATE TABLE contacts (
  contact_id           TEXT PRIMARY KEY,
  company_id           TEXT NOT NULL REFERENCES companies(company_id),
  contact_email        TEXT,
  contact_phone        TEXT,
  contact_name_raw     TEXT,
  contact_first_name   TEXT,
  contact_vocative     TEXT,
  -- §7: невпевненість зберігається, а не приховується
  vocative_confidence  TEXT NOT NULL DEFAULT 'UNKNOWN'
                       CHECK (vocative_confidence IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
  name_source          TEXT,
  contact_region       TEXT,
  contact_address      TEXT,
  email_state          TEXT NOT NULL DEFAULT 'ACTIVE'
                       CHECK (email_state IN ('ACTIVE','INVALID_EMAIL','BOUNCED','UNKNOWN')),
  first_seen_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_contacts_company_email
  ON contacts(company_id, contact_email) WHERE contact_email IS NOT NULL;

-- ---------- 4. ЗАКУПІВЛІ ------------------------------------------------------
CREATE TABLE tenders (
  tender_id     TEXT PRIMARY KEY,                -- внутрішній UUID Prozorro
  ua_id         TEXT NOT NULL,
  tender_title  TEXT,
  buyer_name    TEXT,
  buyer_edrpou  TEXT,
  cpv           TEXT,
  retrieved_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_tenders_uaid ON tenders(ua_id);

-- ---------- 4. ПОДІЯ ВІДХИЛЕННЯ ≠ КОМПАНІЯ -----------------------------------
CREATE TABLE rejections (
  rejection_id        TEXT PRIMARY KEY,
  tender_id           TEXT NOT NULL REFERENCES tenders(tender_id),
  company_id          TEXT NOT NULL REFERENCES companies(company_id),
  lot_id              TEXT,
  participant_id      TEXT,
  bid_id              TEXT,
  award_id            TEXT,
  qualification_id    TEXT,
  rejection_date      TEXT NOT NULL,
  -- §7: сирий текст не змінюється НІКОЛИ
  rejection_reason_raw    TEXT NOT NULL,
  rejection_reason_short  TEXT,
  reason_source       TEXT NOT NULL
                      CHECK (reason_source IN ('PROTOCOL_VERBATIM','CLASSIFIER','MANUAL','NOT_ESTABLISHED')),
  reason_confidence   TEXT NOT NULL DEFAULT 'UNKNOWN'
                      CHECK (reason_confidence IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
  reason_code         TEXT,                      -- R01..R14/R99 — внутрішньо
  -- Для службової картки. Джерело — Prozorro, не наші обчислення.
  stage               TEXT,                      -- awards / qualifications
  lot_amount          REAL,                      -- ЗАСТАРІЛЕ: див. lot_value_amount
  -- База плати до АМКУ. Три РІЗНІ величини — три різні колонки: раніше вони
  -- жили в одній, і «немає вартості лоту» неможливо було відрізнити від
  -- «лот не визначено» та від «взяли суму всього тендера».
  tender_value_amount REAL,                      -- очікувана вартість ЗАКУПІВЛІ
  lot_value_amount    REAL,                      -- очікувана вартість ЛОТУ
  lots_total          INTEGER,                   -- скільки лотів у закупівлі
  lot_resolved        TEXT,                      -- YES / NO / NO_LOTS
  bid_amount          REAL,                      -- ЦІНА ПРОПОЗИЦІЇ учасника, грн
  winner_amount       REAL,                      -- ціна переможця, грн
  winner_name         TEXT,                      -- хто виграв
  protocol_url        TEXT,                      -- пряме посилання на протокол
  protocol_title      TEXT,
  protocol_published  TEXT,
  notice_24h          TEXT,                      -- YES / NO / NOT_ESTABLISHED
  notice_24h_url      TEXT,                      -- посилання на саму вимогу
  notice_24h_title    TEXT,
  td_documents        TEXT,                      -- JSON: документи ТД і зміни
  bid_documents       TEXT,                      -- JSON: документи учасника
  award_documents     TEXT,                      -- JSON: рішення і вимоги 24 год
  qa_documents        TEXT,                      -- JSON: запитання/вимоги/скарги
  qa_text             TEXT,                      -- текст листування із замовником
  -- Провенанс розрахунку плати до АМКУ (§31 специфікації).
  amcu_fee_status         TEXT,
  amcu_fee_base_type      TEXT,
  amcu_fee_base_amount    TEXT,
  amcu_fee_rate           TEXT,
  amcu_fee_raw            TEXT,
  amcu_fee_min_applied    INTEGER,
  amcu_fee_max_applied    INTEGER,
  amcu_fee_rounding_applied TEXT,
  amcu_fee_calculated     TEXT,
  amcu_fee_rule_id        TEXT,
  amcu_fee_source_object_id TEXT,
  amcu_fee_reason         TEXT,
  cpv                 TEXT,
  -- Стан документів справи. NULL = ще не перевіряли; COMPLETE = усе, що
  -- можна було взяти, лежить у теці; INCOMPLETE = є що доробити;
  -- EMPTY_LISTS = переліків документів немає (запис старої версії).
  docs_state          TEXT,
  docs_checked_at     TEXT,
  docs_attempts       INTEGER NOT NULL DEFAULT 0,
  complaint_deadline  TEXT,                      -- кінець періоду оскарження
  source_locator      TEXT NOT NULL,             -- JSON: документ/версія/сторінка
  discovered_at       TEXT NOT NULL
);
-- §4: подія відхилення дедуплікується стабільними ID, не назвою і не e-mail
CREATE UNIQUE INDEX ux_rejection_identity
  ON rejections(tender_id, COALESCE(lot_id,''), COALESCE(bid_id,''),
                COALESCE(award_id,''), COALESCE(qualification_id,''));

-- ---------- 24. ЛІД І ЙОГО ПРИДАТНІСТЬ ---------------------------------------
CREATE TABLE leads (
  lead_id        TEXT PRIMARY KEY,
  rejection_id   TEXT NOT NULL REFERENCES rejections(rejection_id),
  company_id     TEXT NOT NULL REFERENCES companies(company_id),
  contact_id     TEXT REFERENCES contacts(contact_id),
  status         TEXT NOT NULL
                 CHECK (status IN ('DISCOVERED','VALIDATING','MANUAL_REVIEW_REQUIRED',
                                   'READY_FOR_OUTREACH','SUPPRESSED','NOT_ELIGIBLE')),
  ineligible_reason TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_leads_rejection ON leads(rejection_id);

-- ---------- 6. СПИСОК ПРИГНІЧЕННЯ --------------------------------------------
CREATE TABLE suppressions (
  suppression_id   TEXT PRIMARY KEY,
  company_id       TEXT REFERENCES companies(company_id),
  email            TEXT,
  state            TEXT NOT NULL
                   CHECK (state IN ('OPTED_OUT','DO_NOT_CONTACT','INVALID_EMAIL',
                                    'CLIENT','MANUAL_HOLD')),
  suppression_reason TEXT NOT NULL,
  suppression_source TEXT NOT NULL,
  suppressed_at    TEXT NOT NULL,
  human_note       TEXT,                          -- §36 KB: AI не перезаписує
  CHECK (company_id IS NOT NULL OR email IS NOT NULL)
);
CREATE INDEX ix_suppr_company ON suppressions(company_id);
CREATE INDEX ix_suppr_email   ON suppressions(email);

-- ---------- 8. ШАБЛОНИ: НЕЗМІННІ ВЕРСІЇ --------------------------------------
CREATE TABLE templates (
  template_id       TEXT NOT NULL,               -- TENDERWIN_FIRST_TOUCH_A
  template_version  TEXT NOT NULL,               -- V1, V2 ...
  variant           TEXT NOT NULL CHECK (variant IN ('A','B')),
  subject_template  TEXT NOT NULL,
  body_template     TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  PRIMARY KEY (template_id, template_version)
);
-- §8: історичний текст не переписується
CREATE TRIGGER trg_templates_immutable BEFORE UPDATE ON templates
BEGIN SELECT RAISE(ABORT, 'TEMPLATE_VERSION_IMMUTABLE'); END;

-- ---------- 9. ЕКСПЕРИМЕНТ І СТАБІЛЬНЕ ПРИЗНАЧЕННЯ ---------------------------
CREATE TABLE experiments (
  experiment_id TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  ended_at      TEXT
);

CREATE TABLE experiment_assignments (
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  company_id    TEXT NOT NULL REFERENCES companies(company_id),
  variant       TEXT NOT NULL CHECK (variant IN ('A','B')),
  assigned_at   TEXT NOT NULL,
  PRIMARY KEY (experiment_id, company_id)
);
-- §9: варіант не змінюється між прогонами
CREATE TRIGGER trg_assignment_stable BEFORE UPDATE OF variant ON experiment_assignments
BEGIN SELECT RAISE(ABORT, 'VARIANT_ASSIGNMENT_IMMUTABLE'); END;

-- ---------- 13. ПОДІЯ ЗВЕРНЕННЯ ----------------------------------------------
CREATE TABLE outreach_events (
  outreach_id            TEXT PRIMARY KEY,
  company_id             TEXT NOT NULL REFERENCES companies(company_id),
  contact_id             TEXT REFERENCES contacts(contact_id),
  lead_id                TEXT REFERENCES leads(lead_id),
  outreach_type          TEXT NOT NULL
                         CHECK (outreach_type IN ('FIRST_TOUCH','FOLLOW_UP','RECONTACT')),
  -- §10: режим — колонка, а не глобальна змінна
  mode                   TEXT NOT NULL CHECK (mode IN ('TEST','LIVE')),
  status                 TEXT NOT NULL
                         CHECK (status IN ('QUEUED','SENDING','SENT','SEND_FAILED',
                                           'DELIVERY_UNKNOWN','BOUNCED','REPLIED',
                                           'OPTED_OUT','CANCELLED')),
  experiment_id          TEXT REFERENCES experiments(experiment_id),
  variant                TEXT CHECK (variant IN ('A','B')),
  template_id            TEXT,
  template_version       TEXT,
  subject_rendered       TEXT NOT NULL,
  body_rendered          TEXT NOT NULL,
  -- §10: дві адреси зберігаються ОКРЕМО
  contact_email_original TEXT,
  delivery_email_actual  TEXT NOT NULL,
  message_id_header      TEXT NOT NULL,          -- <idempotency_key@tenderwin.com.ua>
  gmail_message_id       TEXT,
  gmail_thread_id        TEXT,
  provider_timestamp     TEXT,
  -- ENGINE   — лист створив і надіслав цей движок
  -- MIGRATED — рядок перенесено зі старої розсилки; він займає перший дотик,
  --            але НЕ рахується в метриках, бо цей лист надсилали не ми
  origin                 TEXT NOT NULL DEFAULT 'ENGINE'
                         CHECK (origin IN ('ENGINE','MIGRATED')),
  generated_at           TEXT NOT NULL,
  sent_at                TEXT,
  error_code             TEXT,
  reply_state            TEXT CHECK (reply_state IN ('NONE','REPLIED','MEANINGFUL_REPLY',
                                                     'NOT_INTERESTED','OPTED_OUT',
                                                     'MANUAL_REVIEW_REQUIRED')),
  FOREIGN KEY (template_id, template_version) REFERENCES templates(template_id, template_version)
);

-- =============================================================================
--  §5 ГОЛОВНЕ ОБМЕЖЕННЯ: ОДИН FIRST_TOUCH НА КОМПАНІЮ, НАЗАВЖДИ
--  Працює попри різні e-mail, різні закупівлі, різні дні, різні прогони.
--  mode='LIVE' у предикаті: тестові листи НЕ спалюють єдиний дотик компанії.
--  SEND_FAILED / CANCELLED виходять з індексу -> повторна спроба дозволена.
--  DELIVERY_UNKNOWN лишається в індексі -> сліпого повтору не буде (§15).
-- =============================================================================
-- Один FIRST_TOUCH на компанію В МЕЖАХ РЕЖИМУ.
-- mode у ключі, а не в предикаті: тестовий лист не витрачає бойовий дотик
-- (Пастка 1), але й у тестовому режимі компанія отримує один лист, навіть
-- якщо в неї відхилили дві пропозиції. Інакше тест показував би не те,
-- що станеться в бою.
CREATE UNIQUE INDEX ux_first_touch
  ON outreach_events(company_id, outreach_type, mode)
  WHERE status IN ('QUEUED','SENDING','SENT','DELIVERY_UNKNOWN',
                   'BOUNCED','REPLIED','OPTED_OUT');

-- §10: у TEST-режимі реальна адреса клієнта не може опинитися в доставці
CREATE TRIGGER trg_test_mode_fail_closed BEFORE INSERT ON outreach_events
WHEN NEW.mode = 'TEST'
 AND NEW.delivery_email_actual NOT IN (SELECT email FROM test_recipient_allowlist)
BEGIN SELECT RAISE(ABORT, 'TEST_RECIPIENT_NOT_ALLOWED'); END;

-- §6: пригнічена компанія не отримує нічого автоматично
CREATE TRIGGER trg_block_suppressed BEFORE INSERT ON outreach_events
WHEN EXISTS (SELECT 1 FROM suppressions s
             WHERE s.state IN ('OPTED_OUT','DO_NOT_CONTACT')
               AND (s.company_id = NEW.company_id
                    OR (s.email IS NOT NULL AND s.email = NEW.contact_email_original)))
BEGIN SELECT RAISE(ABORT, 'COMPANY_SUPPRESSED'); END;

-- ---------- 10. БІЛИЙ СПИСОК ТЕСТОВИХ АДРЕС ----------------------------------
CREATE TABLE test_recipient_allowlist (
  email      TEXT PRIMARY KEY,
  added_at   TEXT NOT NULL,
  note       TEXT
);

-- ---------- 15. ІДЕМПОТЕНТНІСТЬ ----------------------------------------------
CREATE TABLE send_attempts (
  attempt_id       TEXT PRIMARY KEY,
  outreach_id      TEXT NOT NULL REFERENCES outreach_events(outreach_id),
  idempotency_key  TEXT NOT NULL,
  retry_no         INTEGER NOT NULL DEFAULT 0,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  result           TEXT CHECK (result IN ('SENT','FAILED','UNKNOWN')),
  error_code       TEXT
);
-- §15: один ключ — одна спроба виклику Gmail. Повтор не створює другий лист.
CREATE UNIQUE INDEX ux_send_idempotency ON send_attempts(idempotency_key);

-- ---------- 23. AUDIT LOG: append-only ---------------------------------------
CREATE TABLE audit_log (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  entity      TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  event       TEXT NOT NULL,
  payload     TEXT
);
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'AUDIT_LOG_APPEND_ONLY'); END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'AUDIT_LOG_APPEND_ONLY'); END;

-- ---------- 22. ЛЮДСЬКИЙ ЕКСПОРТ (звіт, не джерело правди) -------------------
CREATE VIEW v_outreach_export AS
SELECT
  o.generated_at, o.sent_at, c.company_name, c.edrpou_norm,
  t.ua_id, t.tender_title, r.rejection_id, r.rejection_date,
  ct.contact_name_raw, ct.contact_vocative, ct.vocative_confidence,
  o.contact_email_original, o.delivery_email_actual, o.mode,
  o.experiment_id, o.variant, o.template_id, o.template_version,
  o.subject_rendered, o.body_rendered, o.status,
  o.gmail_message_id, o.gmail_thread_id, o.reply_state, o.error_code,
  (SELECT COUNT(*) FROM send_attempts sa WHERE sa.outreach_id = o.outreach_id) AS retry_count
FROM outreach_events o
JOIN companies c   ON c.company_id = o.company_id
LEFT JOIN leads l  ON l.lead_id = o.lead_id
LEFT JOIN rejections r ON r.rejection_id = l.rejection_id
LEFT JOIN tenders t    ON t.tender_id = r.tender_id
LEFT JOIN contacts ct  ON ct.contact_id = o.contact_id;

"""

# ============================================================================
#  БЛОК 4. БАЗА І РЕПОЗИТОРІЙ — ЄДИНА ТОЧКА ЗАПИСУ СТАНУ
# ============================================================================
class Db:
    """Зʼєднання і схема. Нічого не знає про бізнес-правила."""

    def __init__(self, path: str):
        self.path = path
        try:
            need_init = path == ":memory:" or not os.path.exists(path)
            parent = os.path.dirname(os.path.abspath(path))
            if path != ":memory:" and parent:
                os.makedirs(parent, exist_ok=True)
            self.conn = sqlite3.connect(path, isolation_level=None,
                                        detect_types=0)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            # WAL швидший, але на змонтованому Google Диску блокування
            # працюють інакше і зʼєднання може зависнути. Пробуємо, і якщо
            # не вийшло — лишаємось на стандартному журналі.
            try:
                self.conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                pass
            self.conn.execute("PRAGMA busy_timeout = 15000")
            if need_init:
                self.conn.executescript(SCHEMA_SQL)
                self.conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                    (str(SCHEMA_VERSION),))
            self._check_version()
        except (sqlite3.Error, OSError) as exc:
            raise ERR["STORAGE_UNAVAILABLE"](str(exc)) from exc

    def _check_version(self) -> None:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'").fetchone()
        found = int(row["value"]) if row else 0
        if found == SCHEMA_VERSION:
            return
        if found == 1:
            self._migrate_1_to_2()
            found = 2
        if found == 2:
            self._migrate_2_to_3()
            found = 3
        if found == 3:
            self._migrate_3_to_4()
            found = 4
        if found == 4:
            self._migrate_4_to_5()
            found = 5
        if found == 5:
            self._migrate_5_to_6()
            found = 6
        if found == 6:
            self._migrate_6_to_7()
            found = 7
        if found == 7:
            self._migrate_7_to_8()
            found = 8
        if found == 8:
            self._migrate_8_to_9()
            found = 9
        if found == 9:
            self._migrate_9_to_10()
            return
        raise ERR["SCHEMA_MISMATCH"](found, SCHEMA_VERSION)

    def _migrate_9_to_10(self) -> None:
        """
        Стан документів справи — у базі.

        Раніше «справа повна» визначалось так: у теці є картка і маніфест.
        Справа, де половина файлів не завантажилась, теж виглядала повною,
        і жоден наступний прогін її вже не доробляв. Усі наявні справи
        отримують NULL = «ще не перевіряли» і пройдуть перевірку поступово.
        """
        for col, typ in (("docs_state", "TEXT"), ("docs_checked_at", "TEXT"),
                         ("docs_attempts", "INTEGER NOT NULL DEFAULT 0")):
            try:
                self.conn.execute(
                    f"ALTER TABLE rejections ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','10')")

    AMCU_COLUMNS = (
        ("tender_value_amount", "REAL"), ("lot_value_amount", "REAL"),
        ("lots_total", "INTEGER"), ("lot_resolved", "TEXT"),
        ("amcu_fee_status", "TEXT"), ("amcu_fee_base_type", "TEXT"),
        ("amcu_fee_base_amount", "TEXT"), ("amcu_fee_rate", "TEXT"),
        ("amcu_fee_raw", "TEXT"), ("amcu_fee_min_applied", "INTEGER"),
        ("amcu_fee_max_applied", "INTEGER"),
        ("amcu_fee_rounding_applied", "TEXT"),
        ("amcu_fee_calculated", "TEXT"), ("amcu_fee_rule_id", "TEXT"),
        ("amcu_fee_source_object_id", "TEXT"), ("amcu_fee_reason", "TEXT"),
    )

    def _migrate_8_to_9(self) -> None:
        """База плати до АМКУ розділяється на лот / закупівлю / не встановлено."""
        for col, typ in self.AMCU_COLUMNS:
            try:
                self.conn.execute(
                    f"ALTER TABLE rejections ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','9')")

    def _migrate_7_to_8(self) -> None:
        """Повний набір документів справи: рішення, листування із замовником."""
        for col in ("award_documents", "qa_documents", "qa_text"):
            try:
                self.conn.execute(
                    f"ALTER TABLE rejections ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','8')")

    def _migrate_6_to_7(self) -> None:
        """Посилання на документи: ТД, пропозиція учасника, вимога 24 год."""
        for col in ("notice_24h_url", "notice_24h_title",
                    "td_documents", "bid_documents"):
            try:
                self.conn.execute(
                    f"ALTER TABLE rejections ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','7')")

    def _migrate_5_to_6(self) -> None:
        """Факти для картки, які раніше не зберігались: посилання на протокол,
        ціна переможця, ознака повідомлення про усунення невідповідностей."""
        for col, typ in (("winner_amount", "REAL"), ("winner_name", "TEXT"),
                         ("protocol_url", "TEXT"), ("protocol_title", "TEXT"),
                         ("protocol_published", "TEXT"), ("notice_24h", "TEXT"),
                         ("cpv", "TEXT")):
            try:
                self.conn.execute(
                    f"ALTER TABLE rejections ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','6')")

    def _migrate_4_to_5(self) -> None:
        """Ціна пропозиції та ЄДРПОУ замовника. Плата за подання рахується
        від ЦІНИ ПРОПОЗИЦІЇ, а не від вартості лоту — раніше цього поля не було
        і число довелося б вигадувати."""
        for sql in ("ALTER TABLE rejections ADD COLUMN bid_amount REAL",
                    "ALTER TABLE tenders ADD COLUMN buyer_edrpou TEXT"):
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','5')")

    def _migrate_3_to_4(self) -> None:
        """Поля для службової картки. Наявні рядки лишаються з NULL."""
        for sql in (
            "ALTER TABLE rejections ADD COLUMN stage TEXT",
            "ALTER TABLE rejections ADD COLUMN lot_amount REAL",
            "ALTER TABLE rejections ADD COLUMN complaint_deadline TEXT",
            "ALTER TABLE contacts ADD COLUMN contact_region TEXT",
            "ALTER TABLE contacts ADD COLUMN contact_address TEXT",
        ):
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass                    # колонка вже є — міграцію вже робили
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','4')")

    def _migrate_2_to_3(self) -> None:
        """
        Дедуплікація починає діяти і в тестовому режимі.

        До цього індекс мав предикат mode='LIVE', і компанія з двома
        відхиленнями отримувала два тестові листи. Наявні дублі скасовуємо,
        лишаючи найраніший, інакше новий індекс не створиться.
        """
        blocking = ("'QUEUED','SENDING','SENT','DELIVERY_UNKNOWN',"
                    "'BOUNCED','REPLIED','OPTED_OUT'")
        dups = list(self.conn.execute(
            f"SELECT outreach_id FROM outreach_events o"
            f" WHERE status IN ({blocking}) AND EXISTS ("
            f"   SELECT 1 FROM outreach_events x"
            f"    WHERE x.company_id = o.company_id"
            f"      AND x.outreach_type = o.outreach_type"
            f"      AND x.mode = o.mode"
            f"      AND x.status IN ({blocking})"
            f"      AND (x.generated_at < o.generated_at"
            f"           OR (x.generated_at = o.generated_at"
            f"               AND x.outreach_id < o.outreach_id)))"))
        for row in dups:
            self.conn.execute(
                "UPDATE outreach_events SET status='CANCELLED',"
                " error_code='DUPLICATE_FIRST_TOUCH' WHERE outreach_id=?",
                (row[0],))
        # обидві назви: v1/v2 звався ux_first_touch_live. Скидаємо і нову назву
        # теж — інакше IF NOT EXISTS мовчки лишив би індекс зі старим предикатом.
        self.conn.execute("DROP INDEX IF EXISTS ux_first_touch_live")
        self.conn.execute("DROP INDEX IF EXISTS ux_first_touch")
        self.conn.execute(
            f"CREATE UNIQUE INDEX ux_first_touch"
            f" ON outreach_events(company_id, outreach_type, mode)"
            f" WHERE status IN ({blocking})")
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','3')")
        if dups:
            print(f"   міграція: скасовано {len(dups)} дублів першого дотику")

    def _migrate_1_to_2(self) -> None:
        """
        Додає outreach_events.origin. До цієї версії перенесені історичні
        записи рахувалися як листи, надіслані движком, і псували
        meaningful_reply_rate — метрику, заради якої робиться A/B.
        """
        self.conn.execute(
            "ALTER TABLE outreach_events ADD COLUMN origin TEXT NOT NULL "
            "DEFAULT 'ENGINE'")
        self.conn.execute(
            "UPDATE outreach_events SET origin='MIGRATED'"
            " WHERE message_id_header LIKE '<migrated-%'")
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version','2')")

    def tx(self):
        return _Tx(self.conn)

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, args))

    def one(self, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchone()

    def close(self) -> None:
        self.conn.close()


class _Tx:
    """Транзакція. Резервування місця в черзі має бути атомарним (§15)."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


class Repository:
    """
    Усі записи стану проходять тут. Більше ніде в движку немає INSERT/UPDATE
    у таблиці стану — це дає одне місце, де видно всі переходи.
    """

    def __init__(self, db: Db, audit: "Audit"):
        self.db = db
        self.audit = audit

    # --- маркери стану ------------------------------------------------------
    def is_history_migrated(self) -> bool:
        row = self.db.one("SELECT value FROM schema_meta WHERE key='history_migrated'")
        return bool(row and row["value"] == "1")

    def mark_history_migrated(self, note: str) -> None:
        """
        Ставиться ОДИН раз, після перенесення історії попередніх розсилок.
        Без цього маркера бойова відправка заблокована: на порожній базі
        перший прогін напише «перший» лист усім, включно з тими, хто вже
        казав «стоп».
        """
        self.db.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('history_migrated','1')")
        self.audit.log("migration", "history", "HISTORY_MIGRATED", {"note": note})

    # --- довідники ----------------------------------------------------------
    def sync_test_allowlist(self, emails: Iterable[str]) -> int:
        n = 0
        for m in emails:
            m = (m or "").strip().lower()
            if not m:
                continue
            self.db.conn.execute(
                "INSERT OR IGNORE INTO test_recipient_allowlist(email, added_at, note) "
                "VALUES (?,?,?)", (m, now_iso(), "з налаштувань TEST_RECIPIENTS"))
            n += 1
        return n

    def upsert_template(self, tpl: "Template") -> None:
        exist = self.db.one(
            "SELECT body_template FROM templates WHERE template_id=? AND template_version=?",
            (tpl.template_id, tpl.version))
        if exist is None:
            self.db.conn.execute(
                "INSERT INTO templates(template_id, template_version, variant, "
                "subject_template, body_template, created_at) VALUES (?,?,?,?,?,?)",
                (tpl.template_id, tpl.version, tpl.variant, tpl.subject, tpl.body,
                 now_iso()))
            self.audit.log("template", f"{tpl.template_id}:{tpl.version}",
                           "TEMPLATE_REGISTERED", {"variant": tpl.variant})
        elif exist["body_template"] != tpl.body:
            # §8: історичний текст не переписується. Змінили текст — це нова версія.
            raise _e("TEMPLATE_VERSION_CONFLICT",
                     f"Текст {tpl.template_id} {tpl.version} відрізняється від збереженого",
                     "Реєстрація шаблону", False,
                     "Створіть нову версію (V2), а не змінюйте наявну.")

    def ensure_experiment(self, experiment_id: str, name: str) -> None:
        self.db.conn.execute(
            "INSERT OR IGNORE INTO experiments(experiment_id, name, started_at) "
            "VALUES (?,?,?)", (experiment_id, name, now_iso()))

    # --- компанії й контакти ------------------------------------------------
    def get_or_create_company(self, edrpou: str, name: str) -> str:
        row = self.db.one("SELECT company_id FROM companies WHERE edrpou_norm=?",
                          (edrpou,))
        if row:
            return row["company_id"]
        cid = new_id()
        self.db.conn.execute(
            "INSERT INTO companies(company_id, edrpou_norm, company_name, first_seen_at) "
            "VALUES (?,?,?,?)", (cid, edrpou, name, now_iso()))
        self.audit.log("company", cid, "COMPANY_CREATED", {"edrpou": edrpou})
        return cid

    def get_or_create_contact(self, company_id: str, person: "Person") -> str:
        if person.email:
            row = self.db.one(
                "SELECT contact_id FROM contacts WHERE company_id=? AND contact_email=?",
                (company_id, person.email))
            if row:
                return row["contact_id"]
        ctid = new_id()
        self.db.conn.execute(
            "INSERT INTO contacts(contact_id, company_id, contact_email, contact_phone,"
            " contact_name_raw, contact_first_name, contact_vocative,"
            " vocative_confidence, name_source, contact_region, contact_address,"
            " first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctid, company_id, person.email, person.phone, person.name_raw,
             person.first_name, person.vocative, person.vocative_confidence,
             person.name_source, person.region, person.address, now_iso()))
        return ctid

    def mark_email_invalid(self, email: str) -> None:
        self.db.conn.execute(
            "UPDATE contacts SET email_state='INVALID_EMAIL' WHERE contact_email=?",
            (email,))

    # --- закупівлі й відхилення --------------------------------------------
    def get_or_create_tender(self, t: "TenderRef") -> str:
        row = self.db.one("SELECT tender_id FROM tenders WHERE ua_id=?", (t.ua_id,))
        if row:
            return row["tender_id"]
        self.db.conn.execute(
            "INSERT INTO tenders(tender_id, ua_id, tender_title, buyer_name,"
            " buyer_edrpou, cpv, retrieved_at) VALUES (?,?,?,?,?,?,?)",
            (t.tender_id, t.ua_id, t.title, t.buyer, t.buyer_edrpou, t.cpv,
             now_iso()))
        return t.tender_id

    #: Колонки з переліками документів справи і поля з RejectionFacts.
    DOC_LIST_COLUMNS = (("td_documents", "td_documents"),
                        ("bid_documents", "bid_documents"),
                        ("award_documents", "award_documents"),
                        ("qa_documents", "qa_documents"))

    #: Одиничні посилання: заповнюємо, лише якщо в базі порожньо.
    FILL_IF_EMPTY = ("protocol_url", "protocol_title", "protocol_published",
                     "notice_24h_url", "notice_24h_title")

    def refresh_rejection_documents(self, rid: str, r: "RejectionFacts") -> dict:
        """
        Зливає переліки документів справи з тим, що бачимо в Prozorro зараз.

        Злиття, а не заміна: документ, який колись був у переліку, лишається
        в ньому, навіть якщо Prozorro його більше не показує — це теж факт.
        Порожні одиничні поля (посилання на протокол, на вимогу 24 год)
        заповнюються; заповнені НЕ переписуються.
        """
        row = self.db.one("SELECT * FROM rejections WHERE rejection_id=?", (rid,))
        if not row:
            return {}
        changes, added = {}, {}
        for col, attr in self.DOC_LIST_COLUMNS:
            new_items = list(getattr(r, attr, None) or [])
            if not new_items:
                continue
            try:
                old_items = json.loads(row[col] or "[]")
            except (ValueError, TypeError):
                old_items = []
            merged, n = merge_document_lists(old_items, new_items)
            if n:
                changes[col] = json.dumps(merged, ensure_ascii=False)
                added[col] = n
        for col in self.FILL_IF_EMPTY:
            new_val = getattr(r, col, None)
            if new_val and not row[col]:
                changes[col] = new_val
                added[col] = 1
        if r.notice_24h and row["notice_24h"] in (None, "", "NOT_ESTABLISHED") \
                and r.notice_24h != "NOT_ESTABLISHED":
            changes["notice_24h"] = r.notice_24h
            added["notice_24h"] = 1
        if r.qa_text and r.qa_text != (row["qa_text"] or "") \
                and len(r.qa_text) >= len(row["qa_text"] or ""):
            # Нові запитання і вимоги накопичуються: свіжий зріз повніший.
            changes["qa_text"] = r.qa_text
            added["qa_text"] = 1
        if changes:
            # Нові документи = теку справи треба доробити.
            changes["docs_state"] = None
            cols = ", ".join(f"{c}=?" for c in changes)
            self.db.conn.execute(
                f"UPDATE rejections SET {cols} WHERE rejection_id=?",
                (*changes.values(), rid))
            self.audit.log("rejection", rid, "DOCUMENTS_MERGED", added)
        return added

    def set_docs_state(self, rid: str, state: str) -> None:
        """Стан документів справи. Невдачі рахуються, успіх обнуляє лічильник."""
        self.db.conn.execute(
            "UPDATE rejections SET docs_state=?, docs_checked_at=?,"
            " docs_attempts=CASE WHEN ?='COMPLETE' THEN 0"
            "                    ELSE COALESCE(docs_attempts,0)+1 END"
            " WHERE rejection_id=?",
            (state, now_iso(), state, rid))

    def get_or_create_rejection(self, r: "RejectionFacts", tender_id: str,
                                company_id: str) -> tuple[str, bool]:
        """Повертає (rejection_id, чи_нове). Ідентичність — за стабільними ID."""
        row = self.db.one(
            "SELECT rejection_id FROM rejections WHERE tender_id=?"
            " AND COALESCE(lot_id,'')=? AND COALESCE(bid_id,'')=?"
            " AND COALESCE(award_id,'')=? AND COALESCE(qualification_id,'')=?",
            (tender_id, r.lot_id or "", r.bid_id or "", r.award_id or "",
             r.qualification_id or ""))
        if row:
            # Справа вже є. Раніше тут просто повертався її ід — і справа,
            # записана старою версією без переліку документів, лишалась
            # без документів НАЗАВЖДИ. Так само губились нові зміни ТД і
            # пізніше опубліковані рішення. Тепер переліки ЗЛИВАЮТЬСЯ:
            # нове додається, старе не видаляється ніколи (правило 14).
            self.refresh_rejection_documents(row["rejection_id"], r)
            return row["rejection_id"], False
        rid = new_id()
        # Плата рахується ОДИН раз, при збереженні факту, і зберігається
        # разом із провенансом. Картка її не перераховує — вона показує.
        fee = calculate_amcu_fee(
            lot_value=r.lot_value_amount, tender_value=r.tender_value_amount,
            lots_total=r.lots_total or 0, lot_resolved=r.lot_resolved,
            lot_id=r.lot_id or "")
        self.db.conn.execute(
            "INSERT INTO rejections(rejection_id, tender_id, company_id, lot_id,"
            " participant_id, bid_id, award_id, qualification_id, rejection_date,"
            " rejection_reason_raw, rejection_reason_short, reason_source,"
            " reason_confidence, reason_code, stage, lot_amount, bid_amount,"
            " tender_value_amount, lot_value_amount, lots_total, lot_resolved,"
            " amcu_fee_status, amcu_fee_base_type, amcu_fee_base_amount,"
            " amcu_fee_rate, amcu_fee_raw, amcu_fee_min_applied,"
            " amcu_fee_max_applied, amcu_fee_rounding_applied,"
            " amcu_fee_calculated, amcu_fee_rule_id,"
            " amcu_fee_source_object_id, amcu_fee_reason,"
            " winner_amount, winner_name, protocol_url, protocol_title,"
            " protocol_published, notice_24h, notice_24h_url, notice_24h_title,"
            " td_documents, bid_documents, award_documents, qa_documents,"
            " qa_text, cpv,"
            " complaint_deadline, source_locator, discovered_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "         ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, tender_id, company_id, r.lot_id, r.participant_id, r.bid_id,
             r.award_id, r.qualification_id, r.rejection_date, r.reason_raw,
             r.reason_short, r.reason_source, r.reason_confidence, r.reason_code,
             r.stage, r.lot_amount, r.bid_amount,
             r.tender_value_amount, r.lot_value_amount, r.lots_total,
             r.lot_resolved,
             fee["amcu_fee_status"], fee["amcu_fee_base_type"],
             fee["amcu_fee_base_amount"], fee["amcu_fee_rate"],
             fee["amcu_fee_raw"], fee["amcu_fee_min_applied"],
             fee["amcu_fee_max_applied"], fee["amcu_fee_rounding_applied"],
             fee["amcu_fee_calculated"], fee["amcu_fee_rule_id"],
             fee["amcu_fee_source_object_id"], fee["amcu_fee_reason"],
             r.winner_amount, r.winner_name, r.protocol_url, r.protocol_title,
             r.protocol_published, r.notice_24h, r.notice_24h_url,
             r.notice_24h_title,
             json.dumps(r.td_documents, ensure_ascii=False),
             json.dumps(r.bid_documents, ensure_ascii=False),
             json.dumps(r.award_documents, ensure_ascii=False),
             json.dumps(r.qa_documents, ensure_ascii=False),
             r.qa_text, r.cpv, r.complaint_deadline,
             json.dumps(r.source_locator, ensure_ascii=False), now_iso()))
        self.audit.log("rejection", rid, "LEAD_DISCOVERED",
                       {"ua_id": r.ua_id, "edrpou": r.edrpou})
        return rid, True

    def get_or_create_lead(self, rejection_id: str, company_id: str,
                           contact_id: Optional[str]) -> str:
        row = self.db.one("SELECT lead_id FROM leads WHERE rejection_id=?",
                          (rejection_id,))
        if row:
            return row["lead_id"]
        lid = new_id()
        self.db.conn.execute(
            "INSERT INTO leads(lead_id, rejection_id, company_id, contact_id,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (lid, rejection_id, company_id, contact_id, "DISCOVERED",
             now_iso(), now_iso()))
        return lid

    def set_lead_status(self, lead_id: str, status: str,
                        reason: Optional[str] = None) -> None:
        self.db.conn.execute(
            "UPDATE leads SET status=?, ineligible_reason=?, updated_at=?"
            " WHERE lead_id=?", (status, reason, now_iso(), lead_id))
        self.audit.log("lead", lead_id, f"LEAD_{status}", {"reason": reason})

    # --- стоп-лист ----------------------------------------------------------
    def suppress(self, state: str, reason: str, source: str,
                 company_id: Optional[str] = None, email: Optional[str] = None,
                 note: Optional[str] = None) -> str:
        sid = new_id()
        self.db.conn.execute(
            "INSERT INTO suppressions(suppression_id, company_id, email, state,"
            " suppression_reason, suppression_source, suppressed_at, human_note)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, company_id, (email or "").lower() or None, state, reason,
             source, now_iso(), note))
        self.audit.log("suppression", sid, "OPT_OUT_RECEIVED",
                       {"state": state, "company_id": company_id, "email": email})
        return sid

    def suppression_of(self, company_id: str,
                       email: Optional[str]) -> Optional[sqlite3.Row]:
        return self.db.one(
            "SELECT * FROM suppressions WHERE state IN ('OPTED_OUT','DO_NOT_CONTACT')"
            " AND (company_id=? OR (email IS NOT NULL AND email=?)) LIMIT 1",
            (company_id, (email or "").lower()))

    # --- експеримент --------------------------------------------------------
    def get_assignment(self, experiment_id: str, company_id: str) -> Optional[str]:
        row = self.db.one(
            "SELECT variant FROM experiment_assignments"
            " WHERE experiment_id=? AND company_id=?", (experiment_id, company_id))
        return row["variant"] if row else None

    def save_assignment(self, experiment_id: str, company_id: str,
                        variant: str) -> None:
        self.db.conn.execute(
            "INSERT OR IGNORE INTO experiment_assignments(experiment_id, company_id,"
            " variant, assigned_at) VALUES (?,?,?,?)",
            (experiment_id, company_id, variant, now_iso()))
        self.audit.log("experiment", company_id, "VARIANT_ASSIGNED",
                       {"experiment_id": experiment_id, "variant": variant})

    # --- звернення ----------------------------------------------------------
    def has_first_touch(self, company_id: str, mode: str = "LIVE") -> bool:
        """
        Чи вже писали цій компанії в цьому режимі.

        Режим у запиті, а не 'LIVE' назавжди: інакше в тестовому прогоні
        компанія з двома відхиленнями отримувала два листи, і тест
        показував не те, що станеться в бою.
        """
        row = self.db.one(
            "SELECT 1 FROM outreach_events WHERE company_id=?"
            " AND outreach_type='FIRST_TOUCH' AND mode=?"
            " AND status IN ('QUEUED','SENDING','SENT','DELIVERY_UNKNOWN',"
            "                'BOUNCED','REPLIED','OPTED_OUT') LIMIT 1",
            (company_id, mode))
        return row is not None

    def reserve_outreach(self, ev: "OutreachDraft") -> str:
        """
        Атомарне резервування (§15). Рядок зʼявляється у статусі QUEUED ДО
        будь-якої мережевої дії. Якщо унікальний індекс не пустив — значить
        цій компанії вже пишуть або написали, і другого листа не буде.
        """
        oid = new_id()
        try:
            with self.db.tx() as c:
                c.execute(
                    "INSERT INTO outreach_events(outreach_id, company_id, contact_id,"
                    " lead_id, outreach_type, mode, status, experiment_id, variant,"
                    " template_id, template_version, subject_rendered, body_rendered,"
                    " contact_email_original, delivery_email_actual, message_id_header,"
                    " generated_at, reply_state)"
                    " VALUES (?,?,?,?,?,?, 'QUEUED', ?,?,?,?,?,?,?,?,?,?, 'NONE')",
                    (oid, ev.company_id, ev.contact_id, ev.lead_id, ev.outreach_type,
                     ev.mode, ev.experiment_id, ev.variant, ev.template_id,
                     ev.template_version, ev.subject, ev.body,
                     ev.contact_email_original, ev.delivery_email_actual,
                     ev.message_id_header, now_iso()))
        except sqlite3.IntegrityError as exc:
            msg = str(exc)
            if "TEST_RECIPIENT_NOT_ALLOWED" in msg:
                raise ERR["TEST_RECIPIENT_NOT_ALLOWED"](ev.delivery_email_actual) from exc
            if "COMPANY_SUPPRESSED" in msg:
                raise ERR["COMPANY_SUPPRESSED"](ev.company_id, "стоп-лист") from exc
            if "ux_first_touch" in msg:
                raise ERR["FIRST_TOUCH_EXISTS"](ev.company_id) from exc
            # Раніше сюди звалювалась БУДЬ-ЯКА помилка цілісності й видавалась
            # за «компанія вже отримувала лист». Через це справжня причина
            # (зламаний зовнішній ключ, NOT NULL) виглядала як штатна робота
            # політики — тихий fallback, заборонений правилом 13.
            raise ERR["QUEUE_INSERT_FAILED"](msg) from exc
        self.audit.log("outreach", oid, "OUTREACH_RESERVED",
                       {"mode": ev.mode, "variant": ev.variant,
                        "delivery_email_actual": ev.delivery_email_actual})
        return oid

    def set_outreach_status(self, outreach_id: str, status: str, **fields) -> None:
        cols, args = ["status=?"], [status]
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(v)
        args.append(outreach_id)
        self.db.conn.execute(
            f"UPDATE outreach_events SET {', '.join(cols)} WHERE outreach_id=?",
            tuple(args))
        self.audit.log("outreach", outreach_id, f"OUTREACH_{status}", fields)

    def outreach(self, outreach_id: str) -> Optional[sqlite3.Row]:
        return self.db.one("SELECT * FROM outreach_events WHERE outreach_id=?",
                           (outreach_id,))

    def queued(self, mode: str, limit: int) -> list[sqlite3.Row]:
        return self.db.q(
            "SELECT * FROM outreach_events WHERE status='QUEUED' AND mode=?"
            " ORDER BY generated_at LIMIT ?", (mode, limit))

    def unresolved(self) -> list[sqlite3.Row]:
        return self.db.q(
            "SELECT * FROM outreach_events WHERE status IN ('SENDING','DELIVERY_UNKNOWN')"
            " ORDER BY generated_at")

    # --- спроби відправки ---------------------------------------------------
    def open_attempt(self, outreach_id: str, idem_key: str, retry_no: int) -> str:
        aid = new_id()
        try:
            self.db.conn.execute(
                "INSERT INTO send_attempts(attempt_id, outreach_id, idempotency_key,"
                " retry_no, started_at) VALUES (?,?,?,?,?)",
                (aid, outreach_id, idem_key, retry_no, now_iso()))
        except sqlite3.IntegrityError as exc:
            raise _e("DUPLICATE_SEND_ATTEMPT",
                     f"Спроба з ключем {idem_key} вже існує",
                     "Один лист", True,
                     "Це і є захист від подвійної відправки. Нічого не робіть.") from exc
        return aid

    def close_attempt(self, attempt_id: str, result: str,
                      error_code: Optional[str] = None) -> None:
        self.db.conn.execute(
            "UPDATE send_attempts SET finished_at=?, result=?, error_code=?"
            " WHERE attempt_id=?", (now_iso(), result, error_code, attempt_id))

    def retry_count(self, outreach_id: str) -> int:
        row = self.db.one(
            "SELECT COUNT(*) n FROM send_attempts WHERE outreach_id=?", (outreach_id,))
        return int(row["n"]) if row else 0


# ============================================================================
#  БЛОК 5. ЖУРНАЛ ПОДІЙ — ТІЛЬКИ ДОДАВАННЯ
# ============================================================================
class Audit:
    """Історія не переписується (тригери в схемі це підстраховують)."""

    def __init__(self, db: Db, run_id: str):
        self.db = db
        self.run_id = run_id

    def log(self, entity: str, entity_id: str, event: str,
            payload: Optional[dict] = None) -> None:
        body = dict(payload or {})
        body["run_id"] = self.run_id
        self.db.conn.execute(
            "INSERT INTO audit_log(at, entity, entity_id, event, payload)"
            " VALUES (?,?,?,?,?)",
            (now_iso(), entity, entity_id, event,
             json.dumps(body, ensure_ascii=False)))

# ============================================================================
#  БЛОК 6. ІДЕНТИЧНІСТЬ КОМПАНІЇ — КЛЮЧ ЦЕ ЄДРПОУ, НЕ E-MAIL
# ============================================================================
@dataclass
class Person:
    name_raw: str = ""
    first_name: str = ""
    vocative: str = ""
    vocative_confidence: str = "UNKNOWN"     # HIGH / MEDIUM / LOW / UNKNOWN
    name_source: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    address: Optional[str] = None


@dataclass
class TenderRef:
    tender_id: str
    ua_id: str
    title: str = ""
    buyer: str = ""
    buyer_edrpou: str = ""
    cpv: str = ""


@dataclass
class RejectionFacts:
    """Факти про одну подію відхилення. Сирий текст ніколи не змінюється."""
    ua_id: str
    edrpou: str
    company_name: str
    rejection_date: str
    reason_raw: str
    tender: TenderRef
    person: Person = field(default_factory=Person)
    lot_id: Optional[str] = None
    participant_id: Optional[str] = None
    bid_id: Optional[str] = None
    award_id: Optional[str] = None
    qualification_id: Optional[str] = None
    reason_short: Optional[str] = None
    reason_source: str = "NOT_ESTABLISHED"
    reason_confidence: str = "UNKNOWN"
    reason_code: Optional[str] = None
    stage: Optional[str] = None
    lot_amount: Optional[float] = None
    bid_amount: Optional[float] = None
    tender_value_amount: Optional[float] = None
    lot_value_amount: Optional[float] = None
    lots_total: int = 0
    lot_resolved: str = ""
    winner_amount: Optional[float] = None
    winner_name: str = ""
    protocol_url: str = ""
    protocol_title: str = ""
    protocol_published: str = ""
    notice_24h: str = "NOT_ESTABLISHED"
    notice_24h_url: str = ""
    notice_24h_title: str = ""
    td_documents: list = field(default_factory=list)
    bid_documents: list = field(default_factory=list)
    award_documents: list = field(default_factory=list)
    qa_documents: list = field(default_factory=list)
    qa_text: str = ""
    cpv: str = ""
    complaint_deadline: Optional[str] = None
    source_locator: dict = field(default_factory=dict)


_EDRPOU_RE = re.compile(r"\d{8,10}")


def normalize_edrpou(raw: Any) -> Optional[str]:
    """
    ЄДРПОУ — 8 цифр, ІПН/РНОКПП — 10. Провідні нулі значущі, тому працюємо
    з рядком і ніколи не приводимо до int.

    Це КЛЮЧ КОМПАНІЇ. Від нього залежить обіцянка «один перший лист на
    фірму назавжди»: якщо той самий код дасть два різні ключі, фірма
    отримає другий лист. Тому:

      * «12345678.0» (код проїхав через число) = «12345678»;
      * «ЄДРПОУ 12 345 678» = «12345678»;
      * шматок IBAN поруч із кодом ключем НЕ стає;
      * два різні коди в одному полі -> None, а не вгаданий перший.

    Правило 5: НЕ ВСТАНОВЛЕНО краще за чужу компанію в базі.
    """
    text = str(raw if raw is not None else "").strip()
    # Число з дробовою частиною-нулем: 12345678.0 -> 12345678.
    text = re.sub(r"^(\d+)\.0+$", r"\1", text)

    s = re.sub(r"\D", "", text)
    if len(s) == 9:                      # трапляється загублений провідний нуль
        s = "0" + s
    if len(s) in (8, 10):
        return s

    # У полі не лише код: «п/р UA…27цифр ЄДРПОУ 12345678», «код …, тел. …».
    # Беремо тільки САМОСТІЙНІ числа потрібної довжини — довгий номер
    # рахунку сюди не потрапляє за побудовою.
    visim = _unique(re.findall(r"(?<!\d)\d{8}(?!\d)", text))
    if len(visim) == 1:
        return visim[0]
    if visim:
        return None                      # два різні коди — не вгадуємо
    desyat = _unique(re.findall(r"(?<!\d)\d{10}(?!\d)", text))
    return desyat[0] if len(desyat) == 1 else None


def _unique(items) -> list:
    """Порядок зберігаємо, повтори прибираємо."""
    return list(dict.fromkeys(items))


class CompanyIdentity:
    """
    Встановлює стабільний ідентифікатор компанії.
    E-mail і назва компанії ключем бути НЕ можуть: у компанії кілька адрес,
    а назви збігаються і змінюються.
    """

    def __init__(self, repo: Repository):
        self.repo = repo

    def resolve(self, facts: RejectionFacts) -> str:
        edrpou = normalize_edrpou(facts.edrpou)
        if not edrpou:
            raise ERR["IDENTITY_NOT_ESTABLISHED"](
                f"{facts.company_name or '?'} у закупівлі {facts.ua_id}")
        return self.repo.get_or_create_company(edrpou, facts.company_name)


# ============================================================================
#  БЛОК 7. ПЕРСОНАЛІЗАЦІЯ — КОЖНЕ ПОЛЕ МАЄ ДЖЕРЕЛО І ДОВІРУ
# ----------------------------------------------------------------------------
#  Вигаданої персоналізації не буває. Не впевнені у кличному відмінку —
#  пишемо «Добрий день!». Не маємо дослівної підстави — лист не формуємо.
# ============================================================================
SAFE_GREETING = "Добрий день!"

#: Звертати на ім'я та по батькові чи лише на ім'я. Канонічна специфікація
#: від 2026-09-03 §8 вимагає лише ім'я: «Добрий день, Іване!».
GREETING_WITH_PATRONYMIC = False


#: Організаційні форми та слова, які означають ПІДПРИЄМСТВО, а не людину.
#: ФОП сюди НЕ входить: «ФОП Коваленко Іван» — це людина з ім'ям.
ORG_FORM_RE = re.compile(
    r"\b(?:тов|тзов|тдв|пп|пат|прат|ат|ват|зат|дп|кп|кнп|нкп|сп|фг|ооо|осбб|"
    r"укб|мкп|дкп|спд|тов\.|корпораці\w*|концерн\w*|компані\w*|підприємств\w*|"
    r"фірм\w*|агенці\w*|холдинг\w*|груп\w*|завод\w*|фабрик\w*|комбінат\w*|"
    r"об'єднанн\w*|консорціум\w*|асоціаці\w*|управлінн\w*|служб\w*|центр\w*|"
    r"інститут\w*|лікарн\w*|школ\w*|рад\w*|департамент\w*)\b",
    re.IGNORECASE)


def _letters_only(text: str) -> str:
    return re.sub(r"[^а-яіїєґa-z]", "", str(text or "").lower())


def followup_sentence(when=None) -> str:
    """
    Речення про наступний крок. «Вже сьогодні» — тільки якщо це реально.

    Обіцянка часу — зобовʼязання перед потенційним клієнтом, і давати її
    після робочого вікна не можна: невиконана обіцянка в першому ж листі
    коштує дорожче за нейтральне формулювання (§4).
    """
    if not SAME_DAY_FOLLOWUP_ENABLED:
        return FOLLOWUP_SAFE
    moment = when or now()
    if moment.hour >= SAME_DAY_CUTOFF_HOUR or moment.weekday() >= 5:
        return FOLLOWUP_SAFE
    return FOLLOWUP_TODAY


def looks_like_company(raw_name: str, company_name: str = "") -> bool:
    """
    Чи стоїть у полі «уповноважена особа» назва ПІДПРИЄМСТВА замість імені.

    Prozorro часто віддає в цьому полі саму фірму. Відмінювати її як ім'я
    не можна: у листі виходить «Шановний Ірбудтрансе!» — і це перше, що
    прочитає клієнт. Краще нейтральне «Добрий день!» (правило 5: не
    вигадувати те, чого ми не встановили).
    """
    raw = str(raw_name or "").strip()
    if not raw:
        return False
    # ФОП з іменем — це людина, а не фірма
    if re.match(r"^\s*(?:фоп|ф\.о\.п|пп\s+фоп)\b", raw, re.IGNORECASE):
        return False
    if ORG_FORM_RE.search(raw):
        return True
    core = _letters_only(raw)
    firma = _letters_only(company_name)
    if core and firma:
        # «Ірбудтранс» усередині «ТЗОВ «ІРБУДТРАНС»» -> це фірма
        if core in firma or firma in core:
            return True
        # або всі слова поля є словами назви фірми
        words = [w for w in re.findall(r"[\w'’\-]+", raw.lower()) if len(w) > 2]
        firma_words = set(re.findall(r"[\w'’\-]+", company_name.lower()))
        if words and all(w in firma_words for w in words):
            return True
    return False


#: Слова-посади та службові адреси. Це НЕ імена. Список із канонічної
#: специфікації 2026-09-03 §7 («Tender Department», «info», «office»,
#: «відділ закупівель», «директор») плюс найчастіші сусіди з Prozorro.
#: Форми перелічені явно, без \\w*, щоб «Директоренко» лишався прізвищем.
ROLE_WORD_RE = re.compile(
    r"\b(?:"
    r"info|office|mail|e-?mail|admin|contact|contacts|tender|tenders|"
    r"procurement|purchase|purchasing|supply|sales|department|dept|"
    r"manager|director|secretary|reception|"
    r"відділ|відділу|відділом|сектор|сектору|служба|служби|"
    r"тендер|тендери|тендерів|тендерний|тендерного|тендерна|тендерної|"
    r"закупівля|закупівлі|закупівель|закупівлями|закупівельний|"
    r"директор|директора|директором|директорка|"
    r"керівник|керівника|керівництво|начальник|начальника|"
    r"бухгалтер|бухгалтера|бухгалтерія|бухгалтерії|головбух|"
    r"менеджер|менеджера|секретар|секретаря|секретарка|"
    r"юрист|юриста|юридичний|приймальня|канцелярія|"
    r"адміністрація|адміністратор|"
    r"уповноважена|уповноважений|уповноваженою|особа|особи|"
    r"представник|представника|постачання|постачальник|"
    r"фахівець|спеціаліст|інженер|інженера"
    r")\b",
    re.IGNORECASE)

#: Причини, з яких значення поля «уповноважена особа» не може стати іменем.
NOT_A_PERSON_REASONS = (
    "NO_LETTERS",              # «-», «123», порожньо після чистки
    "EMAIL_NOT_PERSON",        # irbudtrans19@gmail.com
    "ROLE_NOT_PERSON",         # Tender Department, відділ закупівель, директор
    "LATIN_NOT_PERSON",        # рядок латиницею — українського кличного не буде
    "COMPANY_NOT_PERSON",      # ТОВ «БУДІНВЕСТ»
)


def person_name_block(raw_name: str, company_name: str = "") -> Optional[str]:
    """
    Причина, з якої це значення НЕ можна вживати як ім'я, або None.

    Це шлюз перед відмінюванням. Без нього парсер ПІБ чесно виконує свою
    роботу над будь-яким рядком і повертає «ім'я» з чого завгодно:
    «Tender Department» -> «Те», «irbudtrans19@gmail.com» -> «Аіе».
    Хибна персоналізація гірша за нейтральну (§8), тому сумнів завжди
    вирішується на користь «Добрий день!».
    """
    raw = str(raw_name or "").strip()
    if not raw:
        return "NO_LETTERS"
    if "@" in raw:
        return "EMAIL_NOT_PERSON"
    if ROLE_WORD_RE.search(raw):
        return "ROLE_NOT_PERSON"
    cyr = len(re.findall(r"[а-яіїєґА-ЯІЇЄҐ]", raw))
    lat = len(re.findall(r"[A-Za-z]", raw))
    if cyr == 0:
        # Ні кирилиці — ні «-», ні «N/A», ні «John Smith»: кличного відмінка
        # за українським правописом тут не побудувати.
        return "NO_LETTERS" if lat == 0 else "LATIN_NOT_PERSON"
    if lat > cyr:
        return "LATIN_NOT_PERSON"
    if looks_like_company(raw, company_name):
        return "COMPANY_NOT_PERSON"
    return None


class Personalizer:
    """
    Кличний відмінок беремо з наявного лід-генератора, якщо він підключений:
    там це зроблено за правописом-2019 §87 і обкатано. Немає його —
    працює безпечний запасний варіант.
    """

    def __init__(self, legacy: Optional[Any] = None):
        self.legacy = legacy

    # --- звертання ----------------------------------------------------------
    def greeting(self, raw_name: str, company_name: str = "") -> tuple[str, str, str]:
        """Повертає (звертання, довіра, джерело)."""
        raw = (raw_name or "").strip()
        if not raw:
            return SAFE_GREETING, "LOW", "NO_NAME"
        block = person_name_block(raw, company_name)
        if block:
            # У полі не людина. Звертання на ім'я тут неможливе.
            return SAFE_GREETING, "LOW", block
        if self.legacy is None:
            return SAFE_GREETING, "LOW", "NO_VOCATIVE_ENGINE"
        try:
            pib = self.legacy.parse_pib(raw, company_name)
            text = self.legacy.greeting(pib, company_name)
        except Exception as exc:                       # noqa: BLE001 — причина в довірі
            return SAFE_GREETING, "LOW", f"VOCATIVE_ERROR:{type(exc).__name__}"
        if not text or text == getattr(self.legacy, "GREETING_FALLBACK", None):
            return SAFE_GREETING, "LOW", "LEGACY_FALLBACK"
        # Ім'я + по батькові — найнадійніша форма; лише прізвище — найризикованіша
        if pib.get("імя") and pib.get("по_батькові"):
            return text, "HIGH", "LEGACY_PIB"
        if pib.get("імя"):
            return text, "MEDIUM", "LEGACY_NAME"
        return SAFE_GREETING, "LOW", "SURNAME_ONLY"

    def address(self, raw_name: str, company_name: str = "") -> tuple[str, str, str]:
        """
        Звертання за редакцією 2026-08-31: «Добрий день, Іване Петровичу!»
        або просто «Добрий день!».

        Ім'я береться лише тоді, коли воно СПРАВДІ встановлене: порожнє поле,
        назва підприємства чи саме прізвище дають нейтральне вітання.
        """
        raw = (raw_name or "").strip()
        if not raw:
            return SAFE_GREETING, "LOW", "NO_NAME"
        block = person_name_block(raw, company_name)
        if block:
            return SAFE_GREETING, "LOW", block
        if self.legacy is None:
            return SAFE_GREETING, "LOW", "NO_VOCATIVE_ENGINE"
        try:
            pib = self.legacy.parse_pib(raw, company_name)
        except Exception as exc:                                    # noqa: BLE001
            return SAFE_GREETING, "LOW", f"VOCATIVE_ERROR:{type(exc).__name__}"
        name, patr = pib.get("імя"), pib.get("по_батькові")
        if not name:
            # Саме прізвище — не звертаємось на ім'я (правило 5).
            return SAFE_GREETING, "LOW", "SURNAME_ONLY"
        fem = pib.get("стать") == "ж"
        try:
            voc = (self.legacy.voc_first_female(name) if fem
                   else self.legacy.voc_first_male(name))
            # SPEC_CONFLICT (зафіксовано 2026-09-03):
            #   вказівка 02.09 — «ім'я та по батькові, якщо є такі дані»;
            #   канонічна специфікація 03.09 §8 — «extract the given name
            #   only», зразок «Добрий день, Іване!».
            # Взято канонічну як пізнішу і явну. Повернути по батькові —
            # одне значення GREETING_WITH_PATRONYMIC.
            if patr and GREETING_WITH_PATRONYMIC:
                voc += " " + self.legacy.voc_patronymic(patr, fem)
        except Exception as exc:                                    # noqa: BLE001
            return SAFE_GREETING, "LOW", f"VOCATIVE_ERROR:{type(exc).__name__}"
        text = f"Добрий день, {voc}!"
        return text, ("HIGH" if patr else "MEDIUM"), (
            "PIB" if patr else "NAME")

    # --- підстава відхилення ------------------------------------------------
    def reason(self, reason_raw: str, mode: str) -> tuple[str, str, str, Optional[str]]:
        """
        Повертає (текст_для_листа, джерело, довіра, код_класифікатора).

        mode="verbatim"   — дослівна цитата. Точність за побудовою.
        mode="classifier" — назва категорії. У бойовому режимі заблоковано:
                            на перевірці 16 формулювань дала 2 суттєві помилки,
                            зокрема натяк на підстави рівня статті 17.
        mode="both"       — категорія плюс цитата.
        """
        raw = (reason_raw or "").strip()
        code = self._classify(raw)
        quote = self._best_quote(raw)

        if mode == "verbatim":
            if not quote:
                return "", "NOT_ESTABLISHED", "UNKNOWN", code
            return (f"У протоколі замовник зазначив: «{quote}».",
                    "PROTOCOL_VERBATIM", "HIGH", code)

        label = self._label(code)
        if mode == "classifier":
            if not label:
                return "", "NOT_ESTABLISHED", "UNKNOWN", code
            conf = "LOW" if code in ("R02", "R03", "R99") else "MEDIUM"
            return (f"Вашу пропозицію відхилено через {label}.",
                    "CLASSIFIER", conf, code)

        if mode == "both":
            if not quote:
                return "", "NOT_ESTABLISHED", "UNKNOWN", code
            head = f"Вашу пропозицію відхилено через {label}. " if label else ""
            return (f"{head}У протоколі замовник зазначив: «{quote}».",
                    "PROTOCOL_VERBATIM", "HIGH", code)

        raise ValueError(f"Невідомий REASON_MODE: {mode}")

    # --- внутрішнє ----------------------------------------------------------
    def _classify(self, raw: str) -> Optional[str]:
        if self.legacy is None or not raw:
            return None
        try:
            code, _label = self.legacy.classify(raw)
            return code
        except Exception:                              # noqa: BLE001
            return None

    def _label(self, code: Optional[str]) -> Optional[str]:
        if self.legacy is None or not code:
            return None
        table = getattr(self.legacy, "CODE_HUMAN", {})
        return table.get(code)

    def _best_quote(self, raw: str, limit: int = 260) -> str:
        """
        Найконкретніша претензія з протоколу. Ми цитуємо, а не переказуємо:
        точність тут важливіша за красу формулювання.
        """
        if not raw:
            return ""
        claims = [raw]
        if self.legacy is not None:
            try:
                split = self.legacy.split_claims(raw)
                if split:
                    claims = split
                    claims.sort(key=lambda c: -self.legacy.claim_specificity(c))
            except Exception:                          # noqa: BLE001
                claims = [raw]
        text = re.sub(r"\s+", " ", str(claims[0])).strip(" ;.,")
        if len(text) > limit:
            cut = text[:limit].rsplit(" ", 1)[0]
            text = cut + "…"
        return text

# ============================================================================
#  БЛОК 8. ШАБЛОНИ — НЕЗМІННІ ВЕРСІЇ
# ----------------------------------------------------------------------------
#  Текст надісланого листа не переписується ніколи. Змінили формулювання —
#  це V2, а не правка V1. Інакше через місяць неможливо сказати, що саме
#  прочитала компанія, яка відповіла.
# ============================================================================
@dataclass(frozen=True)
class Template:
    template_id: str
    version: str
    variant: str
    subject: str
    body: str


SENDER_PHONE = "+380 50 310 14 92"

SIGNATURE = (
    "З повагою,\n"
    "Віталій Щасливий\n"
    "радник з публічних закупівель у будівництві\n"
    "TenderWin\n"
    + SENDER_PHONE + "\n"
    "vitalii@tenderwin.com.ua"
)

_SUBJECT = "Щодо закупівлі {ua_id} — {company}"

#: Тема і текст за редакцією власника продукту від 2026-08-31.
#: Шаблони незмінні (тригер VARIANT_IMMUTABLE), тому це НОВА версія V2,
#: а не правка V1: історія того, що саме надсилалось, має лишатись читабельною.
_SUBJECT_V2 = "Щодо закупівлі {ua_id} та {buyer}"

SIGNATURE_V2 = (
    "З повагою,\n"
    "Віталій Щасливий\n"
    "TenderWin\n"
    + SENDER_PHONE
)

TEMPLATE_A_V1 = Template(
    template_id="TENDERWIN_FIRST_TOUCH_A",
    version="V1",
    variant="A",
    subject=_SUBJECT,
    body=(
        "{greeting}\n"
        "\n"
        "Побачив рішення замовника у закупівлі {ua_id} щодо {company}. {reason}\n"
        "\n"
        "У таких ситуаціях основне питання, на мій погляд, не просто в тому, чи можна "
        "подати скаргу, а в тому, що доцільно робити далі.\n"
        "\n"
        "Чи справді замовник мав достатні підстави для відхилення? Чи є у Вашої позиції "
        "сильні аргументи? Чи має практичний сенс витрачати час і кошти на оскарження — "
        "або краще врахувати цю ситуацію в наступних закупівлях?\n"
        "\n"
        "Я займаюся незалежним аналізом закупівель у будівництві саме з такої позиції. "
        "Моє завдання — не знайти привід для скарги, а допомогти підприємству прийняти "
        "обґрунтоване рішення на основі документів.\n"
        "\n"
        "Такий погляд зі сторони може бути корисним не лише після відхилення. До мене "
        "можна звернутися і раніше — наприклад, коли потрібно перед участю оцінити "
        "вимоги та ризики закупівлі або перед поданням незалежно звірити підготовлену "
        "пропозицію з вимогами тендерної документації.\n"
        "\n"
        "Якщо питання цієї закупівлі для Вас ще актуальне, можете просто відповісти на "
        "цей лист. Я спочатку коротко перегляну ситуацію і скажу, чи бачу сенс "
        "аналізувати її детальніше.\n"
        "\n"
        "Якщо ця закупівля для Вас уже закрита — також усе гаразд. Можете зберегти мій "
        "контакт на майбутнє. Якщо зʼявиться інша закупівля, щодо якої потрібен "
        "незалежний погляд, достатньо надіслати UA-ID і коротко написати, яке рішення "
        "Ви зараз приймаєте: чи брати участь, чи все гаразд із підготовленою "
        "пропозицією, чи що робити після рішення замовника.\n"
        "\n"
        + SIGNATURE + "\n"
        "\n"
        "P.S. Якщо такі звернення Вам неактуальні, достатньо відповісти «стоп», "
        "і я більше не турбуватиму.\n"
    ),
)

TEMPLATE_B_V1 = Template(
    template_id="TENDERWIN_FIRST_TOUCH_B",
    version="V1",
    variant="B",
    subject=_SUBJECT,
    body=(
        "{greeting}\n"
        "\n"
        "Побачив рішення замовника у закупівлі {ua_id} щодо {company}. {reason}\n"
        "\n"
        "У такій ситуації головне питання, на мій погляд, не тільки в тому, чи можна "
        "оскаржувати, а в тому, чи є в цьому практичний сенс.\n"
        "\n"
        "Я займаюся незалежним аналізом закупівель у будівництві. Моє завдання — "
        "не знайти привід для скарги, а допомогти підприємству зрозуміти, що робити "
        "далі на основі документів.\n"
        "\n"
        "Такий другий погляд може бути корисним і раніше: перед участю в закупівлі або "
        "перед поданням пропозиції, коли потрібно незалежно звірити її з вимогами "
        "тендерної документації.\n"
        "\n"
        "Якщо ця закупівля для Вас ще актуальна, можете просто відповісти на лист. "
        "Я коротко перегляну ситуацію і скажу, чи бачу сенс аналізувати її детальніше.\n"
        "\n"
        "Якщо ні — можете зберегти мій контакт на майбутнє. Для первинного погляду на "
        "іншу закупівлю достатньо надіслати UA-ID і коротко написати, яке рішення "
        "потрібно прийняти.\n"
        "\n"
        + SIGNATURE + "\n"
        "\n"
        "P.S. Якщо такі звернення Вам неактуальні, достатньо відповісти «стоп».\n"
    ),
)

TEMPLATE_B_V2 = Template(
    template_id="TENDERWIN_FIRST_TOUCH_B",
    version="V2",
    variant="B",
    subject=_SUBJECT_V2,
    body=(
        "{greeting}\n"
        "\n"
        "Побачив, що пропозицію {company} відхилили у закупівлі {ua_id}, "
        "яку {buyer_verb} {buyer} на закупівлю {subject_genitive}.\n"
        "\n"
        "Переглянув рішення замовника та документи, яких стосується відхилення. "
        "Після такого рішення головне, на мою думку, — не поспішати зі скаргою, "
        "а зрозуміти, чи справді у замовника були достатні підстави і чи є сенс "
        "боротися далі.\n"
        "\n"
        "Нижче залишаю одну обставину, на яку я б звернув увагу саме у Вашій "
        "ситуації.\n"
        "\n"
        "{reason}\n"
        "\n"
        + SIGNATURE_V2 + "\n"
    ),
)

# --------------------------------------------------------------------------
#  FIRST TOUCH v3 — канонічна редакція власника продукту від 2026-09-03.
#  Це НЕ лист-продаж: жодного CTA, жодної ціни, жодного переліку послуг.
#  Мета — доречність, особиста увага, компетентність, довіра, цікавість.
#
#  SPEC_CONFLICT (зафіксовано 2026-09-03, чекає на рішення власника):
#    Вимога A (02.09) — у тілі листа згадати ЗАМОВНИКА і ПРЕДМЕТ закупівлі
#      у родовому відмінку, а наприкінці дати «одну обставину» з протоколу.
#      Реалізовано у TEMPLATE_B_V2 і працює: subject_genitive + buyer_verb.
#    Вимога B (03.09, канонічна специфікація) — коротке канонічне тіло без
#      замовника, без предмета і без цитати з протоколу.
#    Взято B як пізнішу і дослівно задану. Наслідок: лист став менш
#      конкретним — з нього не видно, ЯКУ саме закупівлю ми дивилися.
#    Повернення до A — це один рядок: ACTIVE_TEMPLATES["B"] = TEMPLATE_B_V2.
#      Обидві версії лишаються в базі, вже надіслані листи не переписуються.
# --------------------------------------------------------------------------
TENDERWIN_URL = "https://tenderwin.com.ua"

_SUBJECT_V3 = "Щодо закупівлі {ua_id}"

SIGNATURE_V3 = (
    "З повагою,\n"
    "Віталій Щасливий\n"
    "радник з публічних закупівель у будівництві\n"
    "TenderWin\n"
    + SENDER_PHONE
)

#: Обіцянка «вже сьогодні» — це зобовʼязання перед клієнтом. Її не можна
#: давати наосліп: якщо аудит сьогодні не встигне, лист стає обманом (§4).
FOLLOWUP_TODAY = ("Наразі закінчую аудит рішення замовника та вже сьогодні "
                  "надішлю свій погляд на ситуацію.")
FOLLOWUP_SAFE = ("Наразі закінчую аудит рішення замовника та після перевірки "
                 "надішлю свій погляд на ситуацію.")

#: Робоче вікно, у якому обіцянка «сьогодні» ще реалістична.
SAME_DAY_CUTOFF_HOUR = 16
SAME_DAY_FOLLOWUP_ENABLED = True

TEMPLATE_B_V3 = Template(
    template_id="TENDERWIN_FIRST_TOUCH_V3",
    version="V3",
    variant="B",
    subject=_SUBJECT_V3,
    body=(
        "{greeting}\n"
        "\n"
        "Побачив, що пропозицію {company} відхилили у закупівлі {ua_id}.\n"
        "\n"
        "Після такого рішення головне, на мою думку, — не поспішати зі "
        "скаргою, а зрозуміти, чи справді у замовника були достатні підстави "
        "і чи є сенс боротися далі.\n"
        "\n"
        "{followup}\n"
        "\n"
        + SIGNATURE_V3 + "\n"
    ),
)

#: Усі версії, які треба зареєструвати в базі (зовнішній ключ звернень).
ALL_TEMPLATE_VERSIONS = (TEMPLATE_A_V1, TEMPLATE_B_V1, TEMPLATE_B_V2,
                         TEMPLATE_B_V3)

#: Яка версія шаблону надсилається зараз. Старі лишаються в базі й у коді:
#: за ними написані вже надіслані листи, і переписувати їх не можна.
ACTIVE_TEMPLATES: dict[str, Template] = {"A": TEMPLATE_A_V1, "B": TEMPLATE_B_V3}
TEMPLATES: dict[str, Template] = ACTIVE_TEMPLATES


class TemplateStore:
    def __init__(self, repo: Repository):
        self.repo = repo
        for tpl in ALL_TEMPLATE_VERSIONS:
            self.repo.upsert_template(tpl)

    def get(self, variant: str) -> Template:
        return TEMPLATES[variant]

    def render(self, variant: str, ctx: dict) -> tuple[str, str]:
        tpl = self.get(variant)
        # Поля перевіряємо в ШАБЛОНІ, а не в готовому тексті: назва компанії
        # може містити фігурні дужки, і це не привід вважати поле порожнім.
        needed = set(re.findall(r"\{(\w+)\}", tpl.subject + tpl.body))
        left = sorted(f for f in needed
                      if not str(ctx.get(f) or "").strip())
        if left:
            raise _e("TEMPLATE_FIELD_MISSING",
                     f"У шаблоні {tpl.template_id} лишилися незаповнені поля: {left}",
                     "Один лист", True,
                     "Лід іде в MANUAL_REVIEW_REQUIRED. Порожнє поле в холодному "
                     "листі гірше за ненадісланий лист.")
        safe = {k: ctx[k] for k in needed}
        return tpl.subject.format(**safe), tpl.body.format(**safe)


# ============================================================================
#  БЛОК 9. ЕКСПЕРИМЕНТ A/B — ПРИЗНАЧЕННЯ, ЯКЕ ПЕРЕЖИВАЄ ПЕРЕЗАПУСК
# ----------------------------------------------------------------------------
#  Вбудований hash() для рядків рандомізований на кожен процес: три запуски
#  поспіль дають різні відповіді. Тому тільки sha256.
# ============================================================================
class ExperimentAssigner:
    def __init__(self, repo: Repository, experiment_id: str, enabled: bool = True):
        self.repo = repo
        self.experiment_id = experiment_id
        self.enabled = enabled
        if enabled:
            self.repo.ensure_experiment(experiment_id, "Перший контакт: A проти B")

    @staticmethod
    def compute(experiment_id: str, company_id: str) -> str:
        digest = hashlib.sha256(f"{experiment_id}|{company_id}".encode()).hexdigest()
        return "AB"[int(digest, 16) % 2]

    def variant_for(self, company_id: str) -> str:
        if not self.enabled:
            return "B"
        saved = self.repo.get_assignment(self.experiment_id, company_id)
        if saved:
            return saved
        variant = self.compute(self.experiment_id, company_id)
        self.repo.save_assignment(self.experiment_id, company_id, variant)
        return variant

# ============================================================================
#  БЛОК 10. ПОЛІТИКА ЗВЕРНЕННЯ
# ----------------------------------------------------------------------------
#  Не кожен відхилений учасник має отримати лист. Тут вирішується, кому
#  писати можна, а кого відправляємо на ручний перегляд.
# ============================================================================
@dataclass
class OutreachDraft:
    company_id: str
    contact_id: Optional[str]
    lead_id: Optional[str]
    outreach_type: str
    mode: str
    experiment_id: Optional[str]
    variant: Optional[str]
    template_id: str
    template_version: str
    subject: str
    body: str
    contact_email_original: Optional[str]
    delivery_email_actual: str
    message_id_header: str


@dataclass
class PolicyVerdict:
    allowed: bool
    status: str                    # READY_FOR_OUTREACH / SUPPRESSED / ...
    reason: str = ""


class OutreachPolicy:
    """
    Порядок перевірок навмисний: спершу те, що забороняє писати назавжди,
    потім те, що просто відкладає.
    """

    def __init__(self, repo: Repository, first_touch_only: bool = True,
                 live_reason_sources: tuple = ("PROTOCOL_VERBATIM",)):
        self.repo = repo
        self.first_touch_only = first_touch_only
        self.live_reason_sources = live_reason_sources

    def evaluate(self, *, company_id: str, email: Optional[str],
                 reason_source: str, reason_text: str,
                 greeting_confidence: str, mode: str) -> PolicyVerdict:
        # 1. Стоп-лист — сильніший за все інше
        sup = self.repo.suppression_of(company_id, email)
        if sup is not None:
            return PolicyVerdict(False, "SUPPRESSED",
                                 f"стоп-лист: {sup['state']} ({sup['suppression_reason']})")

        # 2. Перший дотик уже витрачено. Тестові листи його не витрачають.
        if self.first_touch_only and self.repo.has_first_touch(company_id, mode):
            return PolicyVerdict(False, "NOT_ELIGIBLE",
                                 "компанія вже отримувала перший лист")

        # 3. Немає адреси — писати нікуди
        if not email:
            return PolicyVerdict(False, "MANUAL_REVIEW_REQUIRED",
                                 "не знайдено e-mail учасника")

        # 4. Підстава відхилення. У бойовому режимі — тільки дослівна цитата.
        if reason_source == "NOT_ESTABLISHED" or not reason_text:
            return PolicyVerdict(False, "MANUAL_REVIEW_REQUIRED",
                                 "не встановлено підставу відхилення")
        if mode == "LIVE" and reason_source not in self.live_reason_sources:
            return PolicyVerdict(False, "MANUAL_REVIEW_REQUIRED",
                                 f"джерело підстави {reason_source} не дозволене "
                                 f"для бойового режиму")

        # 5. Звертання. Низька довіра не блокує: є безпечне «Добрий день!».
        if greeting_confidence == "UNKNOWN":
            return PolicyVerdict(False, "MANUAL_REVIEW_REQUIRED",
                                 "невідомий стан персоналізації звертання")

        return PolicyVerdict(True, "READY_FOR_OUTREACH")


# ============================================================================
#  БЛОК 11. ЧЕРГА ВІДПРАВКИ
# ----------------------------------------------------------------------------
#  Пошук лідів НЕ надсилає листи. Він доводить лід до READY_FOR_OUTREACH,
#  політика перевіряє, і тільки потім зʼявляється рядок у черзі.
# ============================================================================
class TestRecipientRotation:
    """
    Ротація тестових скриньок. Детермінована: той самий індекс дає ту саму
    адресу при повторному прогоні. На дедуплікацію не впливає ніяк —
    скринька це пункт доставки, а не особа.
    """

    def __init__(self, recipients: list[str]):
        self.recipients = [r.strip().lower() for r in recipients if r and r.strip()]
        if not self.recipients:
            raise _e("TEST_ALLOWLIST_EMPTY",
                     "Білий список тестових адрес порожній",
                     "Уся тестова відправка", False,
                     "Заповніть TEST_RECIPIENTS у блоці налаштувань.")
        self._i = 0

    def next(self) -> str:
        addr = self.recipients[self._i % len(self.recipients)]
        self._i += 1
        return addr


class SendQueue:
    """Ставить у чергу і віддає готові до відправки."""

    def __init__(self, repo: Repository, mode: str,
                 rotation: Optional[TestRecipientRotation] = None):
        self.repo = repo
        self.mode = mode
        self.rotation = rotation

    def delivery_address(self, real_email: str) -> str:
        if self.mode == "TEST":
            if self.rotation is None:
                raise _e("TEST_ROTATION_MISSING",
                         "Тестовий режим без білого списку адрес",
                         "Уся відправка", False,
                         "Створіть TestRecipientRotation з TEST_RECIPIENTS.")
            return self.rotation.next()
        return real_email

    def enqueue(self, draft: OutreachDraft) -> str:
        return self.repo.reserve_outreach(draft)

    def pending(self, limit: int) -> list[sqlite3.Row]:
        return self.repo.queued(self.mode, limit)

    def requeue_failed(self, max_retries: int) -> int:
        """
        Повертає в чергу листи, які не пішли через тимчасову помилку.

        Без цього кроку одна помилка Gmail стирала б лід назавжди: рядок
        звернення лишався в SEND_FAILED, лід — у READY_FOR_OUTREACH, і
        жоден із наступних кроків його вже не бачив.

        DELIVERY_UNKNOWN сюди НЕ потрапляє ніколи: там лист міг піти,
        і його місце — у звірці, а не в повторній відправці.
        """
        rows = self.repo.db.q(
            "SELECT outreach_id, company_id, outreach_type FROM outreach_events"
            " WHERE status='SEND_FAILED' AND mode=?", (self.mode,))
        n = 0
        for row in rows:
            if self.repo.retry_count(row["outreach_id"]) >= max_retries:
                self.repo.set_outreach_status(
                    row["outreach_id"], "CANCELLED",
                    error_code="RETRY_LIMIT_REACHED")
                continue
            # Поки цей лист лежав у SEND_FAILED, компанія могла отримати
            # НОВИЙ перший дотик за іншим відхиленням. Тоді повернення
            # старого в чергу порушує єдиність дотику — і раніше прогін
            # падав тут із IntegrityError просто посеред роботи.
            zaynyato = self.repo.db.one(
                "SELECT outreach_id FROM outreach_events"
                " WHERE company_id=? AND outreach_type=? AND mode=?"
                "   AND outreach_id<>?"
                "   AND status IN ('QUEUED','SENDING','SENT','DELIVERY_UNKNOWN',"
                "                  'BOUNCED','REPLIED','OPTED_OUT')",
                (row["company_id"], row["outreach_type"], self.mode,
                 row["outreach_id"]))
            if zaynyato:
                self.repo.set_outreach_status(
                    row["outreach_id"], "CANCELLED",
                    error_code="SUPERSEDED_BY_NEWER_TOUCH")
                continue
            # Новий ключ ідемпотентності: це нова спроба, не повтор старої.
            try:
                self.repo.set_outreach_status(
                    row["outreach_id"], "QUEUED", error_code=None,
                    message_id_header=build_message_id(uuid.uuid4().hex))
            except sqlite3.IntegrityError:
                # Гонка з іншим дотиком. Дубля не буде — база не пустила;
                # прогін теж не має падати (правило 13).
                self.repo.set_outreach_status(
                    row["outreach_id"], "CANCELLED",
                    error_code="SUPERSEDED_BY_NEWER_TOUCH")
                continue
            n += 1
        return n


# ============================================================================
#  БЛОК 12. ТРАНСПОРТ
# ----------------------------------------------------------------------------
#  Відправник не знає, чим саме надсилається лист. Це дозволяє прогнати
#  весь пайплайн без жодного реального листа.
# ============================================================================
@dataclass
class SendResult:
    ok: bool
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    provider_timestamp: Optional[str] = None
    error: Optional[str] = None
    unknown: bool = False          # True -> невідомо, чи пішов лист


class Transport(Protocol):
    name: str

    def send(self, raw_rfc822: bytes, message_id_header: str) -> SendResult: ...

    def find_by_message_id(self, message_id_header: str) -> Optional[dict]: ...


class DryRunTransport:
    """
    Нічого не надсилає. Складає готовий лист у файл, щоб його можна було
    відкрити і прочитати очима. Режим за замовчуванням.
    """

    name = "DRY_RUN"

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.sent: list[str] = []

    def send(self, raw_rfc822: bytes, message_id_header: str) -> SendResult:
        safe = re.sub(r"[^\w.\-]", "_", message_id_header)[:80]
        path = os.path.join(self.out_dir, f"{safe}.eml")
        with open(path, "wb") as fh:
            fh.write(raw_rfc822)
        self.sent.append(message_id_header)
        return SendResult(ok=True, message_id=f"dryrun-{uuid.uuid4().hex[:12]}",
                          thread_id=None, provider_timestamp=now_iso())

    def find_by_message_id(self, message_id_header: str) -> Optional[dict]:
        if message_id_header in self.sent:
            return {"id": "dryrun-found", "threadId": None}
        return None


class GmailTransport:
    """
    Реальна відправка через Gmail API службовим акаунтом з домен-делегуванням.

    Розділення повноважень (§16): цей клас має тільки gmail.send і НЕ бачить
    скриньку. Пошук у надісланих робить окремий читач з gmail.readonly —
    інакше довелося б дати відправнику доступ до всієї пошти.
    """

    name = "GMAIL"

    def __init__(self, sender: str, sa_json: str, read_enabled: bool = True):
        try:
            from google.oauth2 import service_account            # noqa: PLC0415
            from googleapiclient.discovery import build          # noqa: PLC0415
        except ImportError as exc:
            raise ERR["TRANSPORT_UNAVAILABLE"](
                "немає google-api-python-client / google-auth") from exc
        try:
            info = json.loads(sa_json)
            send_cred = service_account.Credentials.from_service_account_info(
                info, scopes=[GMAIL_SEND_SCOPE]).with_subject(sender)
            self._send_api = build("gmail", "v1", credentials=send_cred,
                                   cache_discovery=False)
            self._read_api = None
            if read_enabled:
                read_cred = service_account.Credentials.from_service_account_info(
                    info, scopes=[GMAIL_READ_SCOPE]).with_subject(sender)
                self._read_api = build("gmail", "v1", credentials=read_cred,
                                       cache_discovery=False)
        except Exception as exc:                                  # noqa: BLE001
            raise ERR["TRANSPORT_UNAVAILABLE"](str(exc)) from exc
        self.sender = sender

    def send(self, raw_rfc822: bytes, message_id_header: str) -> SendResult:
        body = {"raw": base64.urlsafe_b64encode(raw_rfc822).decode()}
        try:
            res = self._send_api.users().messages().send(userId="me", body=body).execute()
        except Exception as exc:                                  # noqa: BLE001
            text = str(exc)
            # Мережевий обрив і таймаут — це НЕ відмова. Лист міг піти.
            if any(s in text.lower() for s in ("timeout", "timed out", "connection",
                                               "ssl", "broken pipe")):
                return SendResult(ok=False, unknown=True, error=text[:200])
            return SendResult(ok=False, error=text[:200])
        return SendResult(ok=True, message_id=res.get("id"),
                          thread_id=res.get("threadId"),
                          provider_timestamp=now_iso())

    def find_by_message_id(self, message_id_header: str) -> Optional[dict]:
        """Звірка через оператор rfc822msgid. Потребує gmail.readonly."""
        if self._read_api is None:
            return None
        bare = message_id_header.strip("<>")
        try:
            res = self._read_api.users().messages().list(
                userId="me", q=f"rfc822msgid:{bare}", maxResults=1).execute()
        except Exception:                                          # noqa: BLE001
            return None
        msgs = res.get("messages") or []
        return msgs[0] if msgs else None

# ============================================================================
#  БЛОК 13. ВІДПРАВНИК І ЗВІРКА
# ----------------------------------------------------------------------------
#  Найважливіше правило файла: статус SENT ставиться ТІЛЬКИ за наявності
#  ідентифікатора від провайдера. Немає ID — немає SENT — лід не втрачено.
# ============================================================================
def build_message_id(idem_key: str, domain: str = GMAIL_DOMAIN) -> str:
    return f"<{idem_key}@{domain}>"


def build_rfc822(*, sender_name: str, sender_email: str, to_email: str,
                 subject: str, body: str, message_id: str,
                 intended_to: Optional[str] = None,
                 html_body: Optional[str] = None) -> bytes:
    """
    Збирає лист. Cc і Bcc не заповнюються ніколи і ніде — саме тому
    сценарій «TEST + Cc/Bcc» неможливий структурно, а не за перевіркою.
    """
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = now().strftime("%a, %d %b %Y %H:%M:%S %z")
    if intended_to and intended_to != to_email:
        # Видно, кому лист призначався насправді. Тільки для тестового режиму.
        msg["X-TenderWin-Intended-To"] = intended_to
    # §51: жоден плейсхолдер не має піти клієнту. Перевіряємо ГОТОВИЙ текст.
    left = _leftover_placeholders(subject + "\n" + body)
    if left:
        raise ERR["PLACEHOLDER_IN_LETTER"](", ".join(left))

    msg.set_content(body, subtype="plain", charset="utf-8")
    if html_body is None:
        html_body = letter_html(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    return msg.as_bytes()


#: Що вважаємо незаповненим плейсхолдером у готовому листі.
PLACEHOLDER_RE = re.compile(
    r"\{[a-zA-Z_]\w*\}"                       # {greeting}
    r"|\[(?:Ім|ім)[^\]]{0,20}\]"                # [Ім'я]
    r"|\[КОМПАНІЯ\]|\[UA-ID\]|\[ЗАМОВНИК\]"     # прямі мітки зі специфікації
    r"|\[телефон\]|\[ПРЕДМЕТ[^\]]*\]|\[phone\]")


def _leftover_placeholders(text: str) -> list:
    return sorted(set(PLACEHOLDER_RE.findall(text or "")))


def letter_html(body: str) -> str:
    """
    HTML-версія листа: особиста ділова кореспонденція, не розсилка.

    Свідомо НЕМАЄ: банера, логотипа, кнопки, кольорових панелей, емодзі,
    зовнішніх шрифтів, зображень і пікселя відстеження (§13, §18). Лист
    має лишатись читабельним, навіть якщо клієнт побачить лише текст.
    """
    import html as _html                                            # noqa: PLC0415
    paragraphs = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    css_p = ("margin:0 0 14px 0;font-size:16px;line-height:1.55;"
             "color:#1f2328;font-family:Arial,Helvetica,'Segoe UI',sans-serif;")
    out = []
    for para in paragraphs[:-1]:
        text = _html.escape(para).replace("\n", "<br>")
        out.append(f'<p style="{css_p}">{text}</p>')

    # Підпис — окремим блоком, стримано.
    sign_lines = [_html.escape(x) for x in paragraphs[-1].split("\n") if x.strip()]
    sign = []
    for i, line in enumerate(sign_lines):
        if line == "TenderWin":
            line = (f'<a href="{TENDERWIN_URL}" '
                    f'style="color:#1f2328;text-decoration:underline;">'
                    f'TenderWin</a>')
        elif line.replace(" ", "").startswith("+380"):
            tel = re.sub(r"[^\d+]", "", line)
            line = (f'<a href="tel:{tel}" '
                    f'style="color:#1f2328;text-decoration:none;">{line}</a>')
        elif i == 1:
            line = f"<strong>{line}</strong>"      # ім'я
        sign.append(line)
    css_sign = ("margin:18px 0 0 0;font-size:15px;line-height:1.5;color:#3a4149;"
                "font-family:Arial,Helvetica,'Segoe UI',sans-serif;")
    out.append(f'<p style="{css_sign}">' + "<br>".join(sign) + "</p>")
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,'
            'initial-scale=1"></head>'
            '<body style="margin:0;padding:0;background:#ffffff;">'
            '<div style="max-width:640px;padding:16px 18px;">'
            + "".join(out) + "</div></body></html>")


class Sender:
    def __init__(self, repo: Repository, transport: Transport,
                 sender_name: str, sender_email: str,
                 min_delay: int = 0, max_delay: int = 0,
                 verbose: bool = True):
        self.verbose = verbose
        self.repo = repo
        self.transport = transport
        self.sender_name = sender_name
        self.sender_email = sender_email
        self.min_delay = min_delay
        self.max_delay = max_delay

    def send_one(self, row: sqlite3.Row) -> str:
        """Повертає підсумковий статус. Другого листа не створює ніколи."""
        oid = row["outreach_id"]
        idem = row["message_id_header"].strip("<>")
        retry_no = self.repo.retry_count(oid)

        # Резервуємо спробу ДО мережі. Той самий ключ удруге не пройде.
        attempt_id = self.repo.open_attempt(oid, idem, retry_no)
        self.repo.set_outreach_status(oid, "SENDING")
        self.repo.audit.log("outreach", oid, "GMAIL_SEND_STARTED",
                            {"attempt": retry_no})

        raw = build_rfc822(
            sender_name=self.sender_name, sender_email=self.sender_email,
            to_email=row["delivery_email_actual"], subject=row["subject_rendered"],
            body=row["body_rendered"], message_id=row["message_id_header"],
            intended_to=row["contact_email_original"])

        res = self.transport.send(raw, row["message_id_header"])

        if res.ok and res.message_id:
            self.repo.close_attempt(attempt_id, "SENT")
            self.repo.set_outreach_status(
                oid, "SENT", sent_at=now_iso(), gmail_message_id=res.message_id,
                gmail_thread_id=res.thread_id,
                provider_timestamp=res.provider_timestamp, error_code=None)
            self.repo.audit.log("outreach", oid, "GMAIL_SEND_CONFIRMED",
                                {"gmail_message_id": res.message_id})
            return "SENT"

        if res.unknown:
            # Обрив звʼязку. Лист МІГ піти. Сліпого повтору не буде.
            self.repo.close_attempt(attempt_id, "UNKNOWN", "TRANSPORT_UNKNOWN")
            self.repo.set_outreach_status(oid, "DELIVERY_UNKNOWN",
                                          error_code="TRANSPORT_UNKNOWN")
            return "DELIVERY_UNKNOWN"

        self.repo.close_attempt(attempt_id, "FAILED", "SEND_FAILED")
        self.repo.set_outreach_status(oid, "SEND_FAILED", error_code="SEND_FAILED")
        self.repo.audit.log("outreach", oid, "SEND_FAILED", {"error": res.error})
        return "SEND_FAILED"

    @property
    def paced(self) -> bool:
        """
        Пауза між листами потрібна для керованої обробки реальної черги.
        У сухому прогоні надсилати нікуди — файли пишуться на диск, і чекати
        між ними безглуздо. Раніше цього розрізнення не було, і сухий прогін
        на 25 лідів мовчки спав до години.
        """
        return self.transport.name != "DRY_RUN" and bool(self.max_delay)

    def preview(self, queue: "SendQueue", limit: int, out_dir: str) -> dict:
        """
        Сухий прогін: показати, що пішло б, і скласти листи у файли.
        Стан у базі НЕ змінюється — лист лишається в черзі.

        Раніше цю роль виконував drain() з підставним транспортом, і
        сухий прогін позначав листи як надіслані. Черга спорожнялась,
        а реальна відправка потім не знаходила нічого. Тут транспорт
        не використовується взагалі — надіслати неможливо структурно.
        """
        rows = queue.pending(limit)
        os.makedirs(out_dir, exist_ok=True)
        for i, row in enumerate(rows, 1):
            raw = build_rfc822(
                sender_name=self.sender_name, sender_email=self.sender_email,
                to_email=row["delivery_email_actual"],
                subject=row["subject_rendered"], body=row["body_rendered"],
                message_id=row["message_id_header"],
                intended_to=row["contact_email_original"])
            base = re.sub(r"[^\w.\-]", "_",
                          (row["contact_email_original"] or "lead")
                          + "_" + row["message_id_header"])[:80]
            with open(os.path.join(out_dir, f"{base}.eml"), "wb") as fh:
                fh.write(raw)
            # .eml кодується в base64 за стандартом MIME — у Блокноті це
            # нечитабельно. Поруч кладемо звичайний текст, щоб лист можна
            # було прочитати очима будь-де.
            with open(os.path.join(out_dir, f"{base}.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(f"Кому:      {row['contact_email_original']}\n"
                         f"Доставка:  {row['delivery_email_actual']}\n"
                         f"Варіант:   {row['variant']}\n"
                         f"Тема:      {row['subject_rendered']}\n"
                         f"{'-' * 70}\n{row['body_rendered']}")
            if self.verbose:
                print(f"    [{i}/{len(rows)}] лист складено  "
                      f"{(row['contact_email_original'] or '')[:34]}", flush=True)
        return {"складено": len(rows), "надіслано": 0}

    def drain(self, queue: "SendQueue", limit: int) -> dict:
        stats = {"SENT": 0, "SEND_FAILED": 0, "DELIVERY_UNKNOWN": 0}
        rows = queue.pending(limit)
        if not rows:
            return stats
        if self.paced and self.verbose:
            hv = (len(rows) - 1) * (self.min_delay + self.max_delay) / 2
            print(f"    у черзі {len(rows)}; з паузою "
                  f"{self.min_delay}-{self.max_delay} с це орієнтовно "
                  f"{hv/60:.0f} хв. Перервати можна будь-коли: надіслані листи "
                  f"вже записані, черга лишиться на наступний запуск.")
        for i, row in enumerate(rows):
            if i and self.paced:
                pause = random.uniform(self.min_delay, self.max_delay)
                if self.verbose:
                    print(f"    пауза {pause:.0f} с …", flush=True)
                time.sleep(pause)
            res = self.send_one(row)
            stats[res] += 1
            if self.verbose:
                print(f"    [{i + 1}/{len(rows)}] {res:<17} "
                      f"{(row['delivery_email_actual'] or '')[:34]}", flush=True)
        return stats


class Reconciler:
    """
    Після краху в статусі SENDING або DELIVERY_UNKNOWN питаємо у провайдера,
    чи існує лист із нашим Message-ID. Не знайшли — на ручний перегляд,
    а не повторна відправка.
    """

    def __init__(self, repo: Repository, transport: Transport):
        self.repo = repo
        self.transport = transport

    def run(self) -> dict:
        stats = {"SENT": 0, "MANUAL": 0, "checked": 0}
        for row in self.repo.unresolved():
            stats["checked"] += 1
            found = self.transport.find_by_message_id(row["message_id_header"])
            if found:
                self.repo.set_outreach_status(
                    row["outreach_id"], "SENT", sent_at=now_iso(),
                    gmail_message_id=found.get("id"),
                    gmail_thread_id=found.get("threadId"))
                self.repo.audit.log("outreach", row["outreach_id"],
                                    "RECONCILED_SENT", {})
                stats["SENT"] += 1
            else:
                self.repo.set_outreach_status(row["outreach_id"], "DELIVERY_UNKNOWN",
                                              error_code="MANUAL_REVIEW_REQUIRED")
                self.repo.audit.log("outreach", row["outreach_id"],
                                    "RECONCILE_INCONCLUSIVE", {})
                stats["MANUAL"] += 1
        return stats


# ============================================================================
#  БЛОК 14. ВІДПОВІДІ І СТОП
# ----------------------------------------------------------------------------
#  P.S. у листі обіцяє: «достатньо відповісти "стоп"». Обіцянку тримає цей
#  блок. Без нього P.S. — це слова, яких система не виконує.
# ============================================================================
STOP_PATTERNS = [
    r"\bстоп\b", r"не\s+пиш", r"не\s+надсилай", r"не\s+турбу",
    r"відпиш", r"відпис", r"unsubscribe", r"не\s+цікав", r"не\s+актуальн",
]
_STOP_RE = re.compile("|".join(STOP_PATTERNS), re.IGNORECASE)

MEANINGFUL_PATTERNS = [
    r"\bтак\b", r"подивіт", r"погляньт", r"зателефон", r"дзвон",
    r"обговор", r"цікав", r"надішл", r"збереж", r"давайте", r"коли\s+зруч",
]
_MEANINGFUL_RE = re.compile("|".join(MEANINGFUL_PATTERNS), re.IGNORECASE)


def classify_reply(subject: str, body: str) -> str:
    """
    Навмисно проста класифікація за ключовими словами.
    Агресивного AI-розбору відповідей тут немає: помилка в бік «клієнт хотів
    відписатися, а ми не помітили» коштує дорожче за ручний перегляд.
    """
    blob = f"{subject or ''}\n{(body or '')[:1500]}"
    if _STOP_RE.search(blob):
        return "OPTED_OUT"
    if _MEANINGFUL_RE.search(blob):
        return "MEANINGFUL_REPLY"
    return "MANUAL_REVIEW_REQUIRED"


class ReplyProcessor:
    def __init__(self, repo: Repository):
        self.repo = repo

    def apply(self, outreach_id: str, subject: str, body: str) -> str:
        row = self.repo.outreach(outreach_id)
        if row is None:
            raise _e("OUTREACH_NOT_FOUND", f"Немає звернення {outreach_id}",
                     "Одна відповідь", True, "Перевірте ідентифікатор.")
        kind = classify_reply(subject, body)
        if kind == "OPTED_OUT":
            self.repo.set_outreach_status(outreach_id, "OPTED_OUT",
                                          reply_state="OPTED_OUT")
            self.repo.suppress(
                state="OPTED_OUT", reason="відповів «стоп» на перший лист",
                source="gmail_reply", company_id=row["company_id"],
                email=row["contact_email_original"])
        else:
            self.repo.set_outreach_status(outreach_id, "REPLIED", reply_state=kind)
        self.repo.audit.log("outreach", outreach_id, "REPLY_RECEIVED", {"kind": kind})
        return kind

    def apply_bounce(self, outreach_id: str, permanent: bool) -> str:
        row = self.repo.outreach(outreach_id)
        if row is None:
            raise _e("OUTREACH_NOT_FOUND", f"Немає звернення {outreach_id}",
                     "Один відбій", True, "Перевірте ідентифікатор.")
        self.repo.set_outreach_status(outreach_id, "BOUNCED",
                                      error_code="PERMANENT" if permanent else "TEMPORARY")
        if permanent and row["contact_email_original"]:
            # Гасимо АДРЕСУ, не компанію: у компанії можуть бути інші контакти.
            self.repo.mark_email_invalid(row["contact_email_original"])
            self.repo.suppress(state="INVALID_EMAIL", reason="постійний відбій",
                               source="bounce", email=row["contact_email_original"])
        return "PERMANENT" if permanent else "TEMPORARY"

# ============================================================================
#  БЛОК 15. PREFLIGHT — ГЕЙТ БОЙОВОГО РЕЖИМУ
# ----------------------------------------------------------------------------
#  Бойова відправка не вмикається однією випадковою зміною змінної.
#  Потрібні два прапорці і жодної невиконаної умови.
# ============================================================================
@dataclass
class PreflightReport:
    mode: str
    passed: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"PREFLIGHT · режим {self.mode} · "
                 + ("ДОЗВОЛЕНО" if self.passed else "ЗАБЛОКОВАНО")]
        for b in self.blockers:
            lines.append(f"  БЛОКЕР      {b}")
        for w in self.warnings:
            lines.append(f"  попередження {w}")
        for u in self.unverified:
            lines.append(f"  НЕ ПЕРЕВІРЕНО {u}")
        return "\n".join(lines)


class Preflight:
    def __init__(self, cfg: "Config", repo: Repository, transport: Transport):
        self.cfg = cfg
        self.repo = repo
        self.transport = transport

    def check(self) -> PreflightReport:
        rep = PreflightReport(mode=self.cfg.mode, passed=True)

        if self.cfg.mode == "TEST":
            if not self.cfg.test_recipients:
                rep.blockers.append("порожній білий список TEST_RECIPIENTS")
            in_db = {r["email"] for r in self.repo.db.q(
                "SELECT email FROM test_recipient_allowlist")}
            missing = {m.lower() for m in self.cfg.test_recipients} - in_db
            if missing:
                rep.blockers.append(f"адреси не занесені в базу: {sorted(missing)[:3]}")
            rep.warnings.append(
                "відповіді з власних скриньок НЕ є даними про конверсію")
            rep.passed = not rep.blockers
            return rep

        # --- бойовий режим ---------------------------------------------------
        if not (self.cfg.mode == "LIVE" and self.cfg.live_send_enabled):
            rep.blockers.append(
                "потрібні ОБИДВА: TEST_MODE = False і LIVE_SEND_ENABLED = True")
        if self.cfg.reason_mode != "verbatim":
            rep.blockers.append(
                f"REASON_MODE = «{self.cfg.reason_mode}». Для бойового режиму "
                "дозволена тільки дослівна цитата: класифікатор на перевірці "
                "дав хибну підставу рівня статті 17")
        if self.transport.name == "DRY_RUN":
            rep.blockers.append("транспорт DRY_RUN — реальні листи не підуть")
        if not self.cfg.sender_email.endswith("@" + GMAIL_DOMAIN):
            rep.blockers.append(
                f"відправник {self.cfg.sender_email} не з домену {GMAIL_DOMAIN}")

        if not self.repo.is_history_migrated():
            rep.blockers.append(
                "історія попередніх розсилок не перенесена (немає маркера "
                "history_migrated). Перший прогін напише «перший» лист усім, "
                "включно з тими, хто вже казав «стоп». Виконайте перенесення "
                "і викличте repo.mark_history_migrated()")

        # Те, що движок перевірити не може і не вдає, що може (правило 56)
        rep.unverified += [
            "SPF / DKIM / DMARC на домені — перевірити зовнішнім інструментом",
            "чи зберігає Gmail наш Message-ID — надіслати один лист і знайти "
            "його через rfc822msgid, інакше звірка після збою не працює",
            "чи оплачений Workspace: тріал дає 500 листів на добу замість 2000",
            "чинність нумерації пунктів Особливостей на дату відправки",
        ]
        rep.passed = not rep.blockers
        return rep


# ============================================================================
#  БЛОК 16. ЗВІТ — ТІЛЬКИ ДЛЯ ЛЮДИНИ
# ----------------------------------------------------------------------------
#  Джерело правди — база. Файл звіту нічого не вирішує: його можна видалити
#  і перегенерувати, і на відправку це не вплине ніяк.
# ============================================================================
EXPORT_COLUMNS = [
    "generated_at", "sent_at", "company_name", "edrpou_norm", "ua_id",
    "tender_title", "rejection_id", "rejection_date", "contact_name_raw",
    "contact_vocative", "vocative_confidence", "contact_email_original",
    "delivery_email_actual", "mode", "experiment_id", "variant", "template_id",
    "template_version", "subject_rendered", "body_rendered", "status",
    "gmail_message_id", "gmail_thread_id", "reply_state", "error_code",
    "retry_count",
]


class Exporter:
    def __init__(self, repo: Repository, out_dir: str):
        self.repo = repo
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _rows(self) -> list[sqlite3.Row]:
        return self.repo.db.q(
            "SELECT * FROM v_outreach_export ORDER BY generated_at DESC")

    def to_csv(self, label: str) -> str:
        path = os.path.join(self.out_dir, f"zvernennya_{label}.csv")
        rows = self._rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(EXPORT_COLUMNS)
            for r in rows:
                w.writerow([r[c] if c in r.keys() else "" for c in EXPORT_COLUMNS])
        return path

    def to_xlsx(self, label: str) -> Optional[str]:
        try:
            from openpyxl import Workbook                          # noqa: PLC0415
            from openpyxl.styles import Alignment, Font            # noqa: PLC0415
        except ImportError:
            return None
        path = os.path.join(self.out_dir, f"zvernennya_{label}.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "Звернення"
        ws.append(EXPORT_COLUMNS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for r in self._rows():
            ws.append([r[c] if c in r.keys() else "" for c in EXPORT_COLUMNS])
        ws.freeze_panes = "A2"
        for i, col in enumerate(EXPORT_COLUMNS, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = (
                60 if col in ("body_rendered", "subject_rendered", "tender_title") else 20)
        wb.save(path)
        return path

    def funnel(self) -> dict:
        """Лійка §25. Метрики рахуються ТІЛЬКИ по бойових листах."""
        q = self.repo.db.one
        def n(sql, args=()):
            row = q(sql, args)
            return int(row["n"]) if row else 0
        # origin='ENGINE': перенесені історичні рядки займають перший дотик,
        # але надсилали їх не ми, тому в метриках їм не місце.
        live = " WHERE mode='LIVE' AND origin='ENGINE'"
        sent = n("SELECT COUNT(*) n FROM outreach_events" + live +
                 " AND status IN ('SENT','REPLIED','BOUNCED','OPTED_OUT')")
        meaningful = n("SELECT COUNT(*) n FROM outreach_events" + live +
                       " AND reply_state='MEANINGFUL_REPLY'")
        return {
            "rejections_found": n("SELECT COUNT(*) n FROM rejections"),
            "eligible_leads": n("SELECT COUNT(*) n FROM leads WHERE status='READY_FOR_OUTREACH'"),
            "suppressed_leads": n("SELECT COUNT(*) n FROM leads WHERE status='SUPPRESSED'"),
            "manual_review": n("SELECT COUNT(*) n FROM leads WHERE status='MANUAL_REVIEW_REQUIRED'"),
            "letters_sent_live": sent,
            "letters_sent_test": n("SELECT COUNT(*) n FROM outreach_events"
                                   " WHERE mode='TEST' AND status='SENT'"),
            "перенесено_з_історії": n("SELECT COUNT(*) n FROM outreach_events"
                                      " WHERE origin='MIGRATED'"),
            "send_failures": n("SELECT COUNT(*) n FROM outreach_events WHERE status='SEND_FAILED'"),
            "delivery_unknown": n("SELECT COUNT(*) n FROM outreach_events WHERE status='DELIVERY_UNKNOWN'"),
            "bounces": n("SELECT COUNT(*) n FROM outreach_events WHERE status='BOUNCED'"),
            "replies": n("SELECT COUNT(*) n FROM outreach_events" + live +
                         " AND reply_state IS NOT NULL AND reply_state NOT IN ('NONE')"),
            "meaningful_replies": meaningful,
            "opt_outs": n("SELECT COUNT(*) n FROM outreach_events WHERE reply_state='OPTED_OUT'"),
            "meaningful_reply_rate": round(meaningful / sent, 4) if sent else None,
        }

# ============================================================================
#  БЛОК 17. ДЖЕРЕЛО ЛІДІВ
# ----------------------------------------------------------------------------
#  Пошук закупівель НЕ переписуємо. У lead_machine_v1 він працює і знає
#  предметну область. Тут — тонка обгортка з чітко названим переліком того,
#  що ми звідти беремо. Усе інше лишається поза цим движком.
# ============================================================================
LEGACY_USES = [
    "scan_day", "all_rejections", "day_rejections", "tenderer_by_bid",
    "participant_contacts", "make_session", "classify", "CODE_HUMAN",
    "split_claims", "claim_specificity", "parse_pib", "greeting",
    "download_protocol", "rejection_time", "first_cpv",
]


class LeadSource(Protocol):
    def rejections_for_day(self, day: str) -> list[RejectionFacts]: ...


class FixtureSource:
    """Ліди зі списку в памʼяті. Для тестів і ручного прогону."""

    def __init__(self, items: list[RejectionFacts]):
        self.items = items

    def rejections_for_day(self, day: str) -> list[RejectionFacts]:
        return list(self.items)


#: Конверти пропозиції в Prozorro. Звірено з документацією API 2.5
#: (prozorro-api-docs.readthedocs.io, standard/bid) 20.09.2026:
#:   documents              — основна пропозиція;
#:   financialDocuments     — цінова частина, у т.ч. КОШТОРИС (billOfQuantity);
#:   eligibilityDocuments   — відповідність вимогам (прекваліфікація);
#:   qualificationDocuments — кваліфікаційні документи.
#: У lotValues документів немає — legacy-код шукав їх там марно.
BID_ENVELOPES = ("documents", "financialDocuments", "eligibilityDocuments",
                 "qualificationDocuments")


_VERSION_MARK = re.compile(r"\s*\((?:редакція \d+|ред\. [\d-]+)\)")


def _with_marker(title: str, marker: str) -> str:
    """«Документ.pdf» + «редакція 2» -> «Документ (редакція 2).pdf».

    Позначка стоїть ПЕРЕД розширенням. Раніше вона йшла після нього —
    «Документ.pdf (редакція 2)» — і такий файл не відкривався подвійним
    кліком, а конвертація не впізнавала в ньому PDF.
    """
    stem, ext = os.path.splitext(_base_title(title))
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext or ""):
        stem, ext = _base_title(title), ""
    return f"{stem} ({marker}){ext}"


def _base_title(title: str) -> str:
    """Назва без позначок редакції — і старого, і нового вигляду."""
    return _VERSION_MARK.sub("", str(title or "")).strip()


def pack_documents(docs, *, envelope: str = "") -> list:
    """
    Документи Prozorro -> наші записи. БЕЗ ЖОДНОГО ЛІМІТУ КІЛЬКОСТІ.

    Раніше тут стояли ліміти 120/200/60, і велика будівельна ТД мовчки
    втрачала хвіст (правило 17: жоден документ не зникає до аналізу).
    Документ без посилання і файл підпису лишаються в переліку з позначкою:
    людина має бачити, що він БУВ, навіть якщо скачати його не можна.
    """
    out, seen = [], {}
    for dd in docs or []:
        title = str(dd.get("title") or "").strip() or "без назви"
        url = str(dd.get("url") or "")
        key = title.lower()
        seen[key] = seen.get(key, 0) + 1
        item = {"назва": title if seen[key] == 1
                else _with_marker(title, f"редакція {seen[key]}"),
                "url": url,
                "тип": str(dd.get("documentType") or ""),
                "опубліковано": str(dd.get("datePublished") or ""),
                "змінено": str(dd.get("dateModified") or ""),
                "document_id": str(dd.get("id") or "")}
        if envelope:
            item["конверт"] = envelope
        if title.lower().endswith(".p7s"):
            item["підпис"] = True
        if not url:
            # Найчастіше — конфіденційний документ: у публічному API
            # посилання на нього немає. EXTERNAL_TECH_SPEC_NOT_VERIFIED:
            # точні правила видимості конфіденційних документів не звірені.
            item["без_посилання"] = True
        out.append(item)
    return out


def _doc_key(item: dict) -> str:
    """Ключ документа для злиття: посилання, інакше ід, інакше назва."""
    if item.get("url"):
        return str(item["url"])
    if item.get("document_id"):
        return f"id:{item['document_id']}"
    return f"t:{item.get('назва') or ''}"


def merge_document_lists(old: list, new: list) -> tuple:
    """
    Обʼєднує два переліки документів. Повертає (обʼєднаний, скільки додано).

    Старі записи йдуть першими і не змінюються; нові — дописуються.
    Нічого не видаляється (правило 14).
    """
    merged = list(old or [])
    have = {_doc_key(x) for x in merged}
    added = 0
    for item in new or []:
        key = _doc_key(item)
        if key in have:
            continue
        have.add(key)
        merged.append(item)
        added += 1
    return merged, added


def collect_bid_documents(tender: dict, bid_id: str) -> tuple:
    """
    УСІ документи пропозиції САМЕ ЦЬОГО учасника.

    Повертає (перелік, проблема). Проблема — рядок або "".

    Що було не так у legacy bid_docs_for:
      * брав лише `documents` — без кошторису, кваліфікаційних і цінових
        документів (у aboveThresholdEU це більша частина пропозиції);
      * якщо bid_id не збігся — віддавав документи ПЕРШОГО учасника, тобто
        в справу відхиленого могла потрапити чужа пропозиція (правило 2);
      * дописував у список Prozorro на місці (docs += …), і кожен повторний
        виклик множив документи.
    """
    if not bid_id:
        return [], "BID_ID_NOT_ESTABLISHED"
    for bid in (tender.get("bids") or []):
        if bid.get("id") != bid_id:
            continue
        out = []
        for envelope in BID_ENVELOPES:
            out += pack_documents(bid.get(envelope), envelope=envelope)
        return out, ("" if out else "BID_HAS_NO_PUBLIC_DOCUMENTS")
    return [], "BID_NOT_FOUND_IN_TENDER"


def collect_correspondence(tender: dict) -> tuple:
    """
    Запитання, вимоги і скарги до замовника — документи і текст.

    Повертає (перелік документів, текст листування). У Prozorro це не
    документи, а поля закупівлі, тому текст складаємо дослівно з них.
    """
    qa_docs, qa_lines = [], []
    for block, label in (("questions", "ЗАПИТАННЯ"),
                         ("complaints", "ВИМОГА/СКАРГА")):
        for q in (tender.get(block) or []):
            head = (q.get("title") or q.get("description") or "")[:200]
            answer = q.get("answer")
            if isinstance(q.get("resolution"), dict):
                answer = answer or q["resolution"]
            if isinstance(answer, dict):
                answer = answer.get("description") or answer.get("resolution")
            qa_lines.append(
                f"[{label}] {q.get('date') or q.get('dateSubmitted') or ''}"
                f" · статус: {q.get('status') or '—'}\n"
                f"  питання: {head}\n"
                f"  опис: {(q.get('description') or '')[:600]}\n"
                f"  відповідь: {str(answer or 'НЕ ВСТАНОВЛЕНО')[:600]}")
            qa_docs += pack_documents(q.get("documents"))
    for a_ in (tender.get("awards") or []):
        for c in (a_.get("complaints") or []):
            qa_lines.append(
                f"[СКАРГА НА РІШЕННЯ] {c.get('date') or ''}"
                f" · статус: {c.get('status') or '—'}\n"
                f"  {(c.get('description') or '')[:600]}")
            qa_docs += pack_documents(c.get("documents"))
    return qa_docs, "\n\n".join(qa_lines)


class LegacyLeadSource:
    """
    Обгортка над наявним лід-генератором.

    Свідомо НЕ використовуємо звідти: resolve_ua (скрейпінг Clarity),
    letters_for_crm, будь-що зі Snov.io, estimate_prospect у тексті листа.
    """

    def __init__(self, module_name: str = LEGACY_MODULE,
                 case_dir: str = "sprava", download_protocols: bool = True,
                 verbose: bool = True):
        self.case_dir = case_dir
        self.download_protocols = download_protocols
        self.verbose = verbose
        self.skipped: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        try:
            self.m = importlib.import_module(module_name)
        except Exception as exc:                                   # noqa: BLE001
            raise ERR["LEGACY_UNAVAILABLE"](f"{module_name}: {exc}") from exc
        missing = [f for f in LEGACY_USES if not hasattr(self.m, f)]
        if missing:
            raise ERR["LEGACY_UNAVAILABLE"](
                f"у модулі немає: {', '.join(missing)}")

    # Розбір одного відхилення у наші факти
    #
    # Назви полів звірені з кодом lead_machine_v1, а не вгадані:
    #   підстава  = title + description + текст протоколу. Короткий description
    #               сам по собі загальний — це прямо сказано в докстрінгу
    #               download_protocol. Саме звідси береться цитата в лист.
    #   учасник   = participant_contacts(): бере suppliers, потім tenderers,
    #               і віддає legalName, якщо він є.
    #   дата      = rejection_time(): найраніша достовірна позначка, а не
    #               просто award["date"], яке оновлюється не завжди.
    def _facts(self, session, tender: dict, stage: str,
               award: dict, case_dir: str) -> tuple[Optional[RejectionFacts], str]:
        ua_id = tender.get("tenderID") or ""
        try:
            contacts = self.m.participant_contacts(tender, award) or {}
        except Exception as exc:                                   # noqa: BLE001
            return None, f"contacts_error:{type(exc).__name__}"

        edrpou = normalize_edrpou(contacts.get("ЄДРПОУ"))
        if not edrpou:
            return None, "немає ЄДРПОУ учасника"

        # --- текст підстави: description сам по собі майже завжди загальний ---
        proto, proto_source = "", "award.title+description"
        if self.download_protocols:
            try:
                proto = self.m.download_protocol(
                    session, award, os.path.join(case_dir, ua_id, "protokol")) or ""
                if proto:
                    proto_source = "protocol_document"
            except Exception as exc:                               # noqa: BLE001
                proto_source = f"protocol_error:{type(exc).__name__}"
        reason_raw = "\n".join(x for x in (award.get("title"),
                                           award.get("description"), proto) if x).strip()
        if not reason_raw:
            return None, "порожній текст підстави"

        when = ""
        try:
            t0 = self.m.rejection_time(award)
            when = t0.astimezone(KYIV).isoformat(timespec="seconds") if t0 else ""
        except Exception:                                          # noqa: BLE001
            when = str(award.get("date") or "")

        cpv = ""
        try:
            cpv = self.m.first_cpv(tender) or ""
        except Exception:                                          # noqa: BLE001
            pass

        # «контактна_особа» — рядок. Ключ «ПІБ» — словник, у нього не лізти.
        person = Person(
            name_raw=str(contacts.get("контактна_особа") or ""),
            email=(str(contacts.get("email") or "").split(",")[0].strip().lower()
                   or None),
            phone=str(contacts.get("телефон") or "") or None,
            name_source="PROZORRO_ESZ")

        bid_id = award.get("bid_id") or award.get("bidID")

        # --- БАЗА ПЛАТИ ДО АМКУ ---------------------------------------
        # Три РІЗНІ величини — три різні поля. Раніше тут викликалась
        # legacy.lot_amount(), яка при невизначеному лоті МОВЧКИ повертала
        # суму всього тендера: у багатолотовій закупівлі база виходила в
        # рази більшою, а при нулі — «немає вартості лоту» поруч із
        # заповненою вартістю закупівлі. Тихих відкатів тут бути не може.
        def _amount(obj):
            try:
                v = float(((obj or {}).get("value") or {}).get("amount") or 0)
            except (TypeError, ValueError, AttributeError):
                return None
            return v or None

        tender_sum = _amount(tender)
        lots = list(tender.get("lots") or [])
        lot_id = award.get("lotID") or award.get("lotId") or ""
        if not lot_id and bid_id:
            for b_ in (tender.get("bids") or []):
                if b_.get("id") != bid_id:
                    continue
                for lv in (b_.get("lotValues") or []):
                    if lv.get("relatedLot"):
                        lot_id = lv["relatedLot"]
                        break
                break
        lot_sum = None
        if lot_id:
            for lot in lots:
                if lot.get("id") == lot_id:
                    lot_sum = _amount(lot)
                    break
        if not lots:
            lot_state = "NO_LOTS"
        elif lot_id and lot_sum is not None:
            lot_state = "YES"
        else:
            lot_state = "NO"          # лот не встановлено — це стан, не нуль
        # ЦІНА ПРОПОЗИЦІЇ учасника — база для плати за подання пропозиції.
        # Це НЕ вартість лоту: плутанка тут дає клієнту неправильну суму.
        bid_sum = None
        try:
            bid_sum = float((award.get("value") or {}).get("amount") or 0) or None
        except (TypeError, ValueError):
            pass
        if bid_sum is None:
            for b in (tender.get("bids") or []):
                if b.get("id") != bid_id:
                    continue
                for lv in (b.get("lotValues") or []):
                    if lv.get("relatedLot") == award.get("lotID"):
                        try:
                            bid_sum = float(
                                (lv.get("value") or {}).get("amount") or 0) or None
                        except (TypeError, ValueError):
                            pass
                        break
                if bid_sum is None:
                    try:
                        bid_sum = float(
                            (b.get("value") or {}).get("amount") or 0) or None
                    except (TypeError, ValueError):
                        pass
                break
        # --- ПОСИЛАННЯ НА ПРОТОКОЛ ------------------------------------
        # Пряме посилання на документ, а не на закупівлю: людина має
        # відкрити саме те, на що спирається аналіз (правило 21).
        proto_url = proto_title = proto_date = ""
        def _speaks(title: str) -> bool:
            low = title.lower()
            return any(w in low for w in ("протокол", "рішенн", "відхил",
                                          "розгляд", "обґрунт", "обгрунт"))
        docs = [d for d in (award.get("documents") or [])
                if d.get("url") and not str(d.get("title") or "").lower()
                .endswith(".p7s")]
        for d in sorted(docs, key=lambda d: 0 if _speaks(
                str(d.get("title") or "")) else 1):
            proto_url = str(d.get("url") or "")
            proto_title = str(d.get("title") or "")
            proto_date = str(d.get("datePublished") or "")
            break

        # --- ПОВІДОМЛЕННЯ ПРО УСУНЕННЯ НЕВІДПОВІДНОСТЕЙ (24 год) ------
        # Тристанова величина. «Не знайшли» — це НЕ «не було» (правило 5).
        notice = "NOT_ESTABLISHED"
        notice_url = notice_title = ""
        marker = re.compile(r"усуненн\w*\s+невідповідн|невідповідн\w*\s+в\s+інформац"
                            r"|24\s*годин|вимог\w*\s+про\s+усуненн", re.I)
        pool = list(award.get("documents") or [])
        for b_ in (tender.get("bids") or []):
            if b_.get("id") == bid_id:
                pool += list(b_.get("documents") or [])
        for dd in pool:
            if marker.search(str(dd.get("title") or "")):
                notice = "YES"
                notice_url = str(dd.get("url") or "")
                notice_title = str(dd.get("title") or "")
                break
        if notice == "NOT_ESTABLISHED" and marker.search(reason_raw or ""):
            notice = "MENTIONED_IN_DECISION"

        # --- ДОКУМЕНТИ ТД І ДОКУМЕНТИ УЧАСНИКА ------------------------
        # Посилання, щоб людина за секунди відкрила ту саму вимогу і той
        # самий файл, а не шукала їх у Prozorro руками (правило 21).
        # ВСЯ тендерна документація ЗІ ЗМІНАМИ. Prozorro віддає кожну редакцію
        # окремим документом; беремо всі, бо для аналізу важливо, що саме
        # діяло на дату подання пропозиції (правило 16). Без ліміту.
        td_docs = pack_documents(tender.get("documents"))

        # ВСЯ пропозиція відхиленого учасника — усі чотири конверти,
        # і тільки ЙОГО. Проблема не ковтається, а йде в журнал справи.
        bid_docs, bid_problem = collect_bid_documents(tender, bid_id)
        if bid_problem:
            # Це не пропуск справи, а попередження про неповноту — окремо.
            self.warnings.append((ua_id, f"пропозиція: {bid_problem}"))

        # Рішення замовника і вимоги про усунення — усі документи цього
        # award/qualification, а не лише той, що ми впізнали як протокол.
        award_docs = pack_documents(award.get("documents"))

        # Листування із замовником: запитання, вимоги, скарги — з відповідями.
        qa_docs, qa_text = collect_correspondence(tender)

        # --- ЦІНА ПЕРЕМОЖЦЯ -------------------------------------------
        # Найсильніше число для розмови: наскільки клієнт був дешевший.
        win_sum, win_name = None, ""
        for a_ in (tender.get("awards") or []):
            if a_.get("status") != "active":
                continue
            if award.get("lotID") and a_.get("lotID") != award.get("lotID"):
                continue
            try:
                win_sum = float((a_.get("value") or {}).get("amount") or 0) or None
            except (TypeError, ValueError):
                win_sum = None
            for sup in (a_.get("suppliers") or []):
                win_name = str(sup.get("name") or sup.get("legalName") or "")
                break
            break

        deadline = (award.get("complaintPeriod") or {}).get("endDate") or None
        addr = str(contacts.get("адреса") or "")
        region = ""
        for part in addr.split(","):
            if "обл" in part.lower():
                region = part.strip()
                break
        person.region = region or None
        person.address = addr or None

        return RejectionFacts(
            ua_id=ua_id, edrpou=edrpou,
            company_name=str(contacts.get("компанія") or ""),
            rejection_date=when, reason_raw=reason_raw,
            tender=TenderRef(
                tender_id=str(tender.get("id") or ua_id), ua_id=ua_id,
                title=str(tender.get("title") or ""),
                buyer=str((tender.get("procuringEntity") or {}).get("name") or ""),
                buyer_edrpou=str(((tender.get("procuringEntity") or {})
                                  .get("identifier") or {}).get("id") or ""),
                cpv=cpv),
            person=person,
            stage=stage, lot_amount=lot_sum, bid_amount=bid_sum,
            tender_value_amount=tender_sum, lot_value_amount=lot_sum,
            lots_total=len(lots), lot_resolved=lot_state,
            winner_amount=win_sum, winner_name=win_name,
            protocol_url=proto_url, protocol_title=proto_title,
            protocol_published=proto_date, notice_24h=notice,
            notice_24h_url=notice_url, notice_24h_title=notice_title,
            td_documents=td_docs, bid_documents=bid_docs,
            award_documents=award_docs, qa_documents=qa_docs,
            qa_text=qa_text, cpv=cpv,
            complaint_deadline=deadline,
            lot_id=lot_id or None, bid_id=bid_id,
            award_id=award.get("id") if stage == "awards" else None,
            qualification_id=award.get("id") if stage == "qualifications" else None,
            participant_id=edrpou,
            source_locator={"source": f"prozorro.{stage}", "ua_id": ua_id,
                            "object_id": award.get("id"),
                            "reason_from": proto_source}), ""

    def rejections_for_day(self, day: str) -> list[RejectionFacts]:
        from datetime import timedelta                              # noqa: PLC0415
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KYIV)
        end = start + timedelta(days=1)
        session = self.m.make_session()
        tenders = self.m.scan_day(session, start, end, now()) or {}
        self.skipped = []
        self.warnings = []
        # Уся закупівля у вибірці -> розбираємо ВСІ її відхилення, а не
        # лише сьогоднішні: інакше з тендера, де відхилили трьох у різні
        # дні, у роботу потрапляє один, а решта губиться назавжди.
        zakupivli = [(tender, list(self.m.all_rejections(tender) or []))
                     for tender in tenders.values()]
        zakupivli = [(t, rej) for t, rej in zakupivli if rej]
        if not zakupivli:
            return []

        # Кожне відхилення тягне свій протокол із Prozorro. Послідовно це
        # чисте чекання на мережу: сотня відхилень — десятки хвилин.
        # Паралелимо ПО ЗАКУПІВЛЯХ: відхилення однієї закупівлі пишуть
        # протоколи в одну теку і мають іти по черзі, інакше два потоки
        # пишуть той самий файл одночасно. Порядок результату — сталий.
        def robota(item):
            tender, rejections = item
            res = []
            for stage, award in rejections:
                try:
                    res.append(self._facts(session, tender, stage, award,
                                           self.case_dir))
                except Exception as exc:                           # noqa: BLE001
                    res.append((None, f"parse_error:{type(exc).__name__}"))
            return res

        workers = min(SCAN_WORKERS, len(zakupivli))
        out: list[RejectionFacts] = []
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(robota, zakupivli))
        else:
            results = [robota(x) for x in zakupivli]
        for (tender, _), per_tender in zip(zakupivli, results):
            for facts, why in per_tender:
                if facts is None:
                    self.skipped.append((tender.get("tenderID") or "?", why))
                else:
                    out.append(facts)
        if self.verbose:
            print(f"    розібрано відхилень: {len(out)}"
                  + (f" · пропущено {len(self.skipped)}" if self.skipped else ""))
            for ua, why in self.warnings[:10]:
                print(f"    ! {ua}: {why}")
            if len(self.warnings) > 10:
                print(f"    ! …і ще {len(self.warnings) - 10} попереджень")
        return out


# ============================================================================
#  БЛОК 18. ОРКЕСТРАТОР
# ----------------------------------------------------------------------------
#  Тут немає бізнес-правил. Тільки порядок викликів. Кожен крок можна
#  запустити окремо, і саме так вони й тестуються.
# ============================================================================
@dataclass
class Config:
    db_path: str = DB_PATH
    out_dir: str = OUT_DIR
    mode: str = "TEST"
    live_send_enabled: bool = LIVE_SEND_ENABLED
    sender_name: str = SENDER_NAME
    sender_email: str = SENDER_EMAIL
    test_recipients: list[str] = field(default_factory=lambda: list(TEST_RECIPIENTS))
    first_touch_only: bool = FIRST_TOUCH_ONLY
    reason_mode: str = REASON_MODE
    experiment_id: str = EXPERIMENT_ID
    experiment_enabled: bool = EXPERIMENT_ENABLED
    min_delay: int = MIN_SEND_DELAY_SECONDS
    max_delay: int = MAX_SEND_DELAY_SECONDS
    max_sends: int = MAX_SENDS_PER_RUN
    verbose: bool = True

    @staticmethod
    def from_globals() -> "Config":
        return Config(mode="TEST" if TEST_MODE else "LIVE",
                      live_send_enabled=LIVE_SEND_ENABLED)


class LeadEngine:
    def __init__(self, cfg: Config, source: LeadSource,
                 transport: Optional[Transport] = None,
                 legacy: Optional[Any] = None):
        self.cfg = cfg
        self.source = source
        self.run_id = new_run_id()
        self.touched_rejections: list[str] = []
        self.db = Db(cfg.db_path)
        self.audit = Audit(self.db, self.run_id)
        self.repo = Repository(self.db, self.audit)
        self.repo.sync_test_allowlist(cfg.test_recipients)
        self.identity = CompanyIdentity(self.repo)
        self.personalizer = Personalizer(legacy)
        self.templates = TemplateStore(self.repo)
        self.experiment = ExperimentAssigner(self.repo, cfg.experiment_id,
                                             cfg.experiment_enabled)
        self.policy = OutreachPolicy(self.repo, cfg.first_touch_only)
        self.transport = transport or DryRunTransport(os.path.join(cfg.out_dir, "eml"))
        self.rotation = (TestRecipientRotation(cfg.test_recipients)
                         if cfg.mode == "TEST" else None)
        self.queue = SendQueue(self.repo, cfg.mode, self.rotation)
        self.sender = Sender(self.repo, self.transport, cfg.sender_name,
                             cfg.sender_email, cfg.min_delay, cfg.max_delay,
                             verbose=cfg.verbose)
        self.reconciler = Reconciler(self.repo, self.transport)
        self.replies = ReplyProcessor(self.repo)
        self.exporter = Exporter(self.repo, cfg.out_dir)
        self.preflight = Preflight(cfg, self.repo, self.transport)

    # --- крок 1: прийняти ліди у базу ---------------------------------------
    def ingest(self, facts_list: list[RejectionFacts]) -> dict:
        # Які саме справи чіпав ЦЕЙ прогін. Без цього списку завантаження
        # документів і побудова карток ішли по всій базі, і час прогону ріс
        # з кожним робочим днем (звіт 26).
        stats = {"нових": 0, "вже_були": 0, "без_ЄДРПОУ": 0}
        for facts in facts_list:
            try:
                company_id = self.identity.resolve(facts)
            except EngineError as err:
                stats["без_ЄДРПОУ"] += 1
                self.audit.log("lead", facts.ua_id or "?", "IDENTITY_FAILED",
                               {"code": err.code, "company": facts.company_name})
                continue
            text, source, conf, code = self.personalizer.reason(
                facts.reason_raw, self.cfg.reason_mode)
            facts.reason_short, facts.reason_source = text, source
            facts.reason_confidence, facts.reason_code = conf, code
            greet, gconf, gsrc = self.personalizer.greeting(
                facts.person.name_raw, facts.company_name)
            facts.person.vocative = greet
            facts.person.vocative_confidence = gconf
            facts.person.name_source = gsrc or facts.person.name_source

            tender_id = self.repo.get_or_create_tender(facts.tender)
            rejection_id, is_new = self.repo.get_or_create_rejection(
                facts, tender_id, company_id)
            contact_id = self.repo.get_or_create_contact(company_id, facts.person)
            self.repo.get_or_create_lead(rejection_id, company_id, contact_id)
            if rejection_id not in self.touched_rejections:
                self.touched_rejections.append(rejection_id)
            stats["нових" if is_new else "вже_були"] += 1
        return stats

    # --- крок 2: перевірити придатність і поставити в чергу ------------------
    def build_queue(self) -> dict:
        stats = {"READY_FOR_OUTREACH": 0, "SUPPRESSED": 0, "NOT_ELIGIBLE": 0,
                 "MANUAL_REVIEW_REQUIRED": 0, "QUEUED": 0}
        rows = self.db.q(
            "SELECT l.lead_id, l.company_id, l.contact_id, r.rejection_id,"
            "       r.rejection_reason_short, r.reason_source,"
            "       c.contact_email, c.vocative_confidence, c.contact_vocative,"
            "       c.contact_name_raw,"
            "       co.company_name, t.ua_id, t.tender_title, t.buyer_name"
            "  FROM leads l"
            "  JOIN rejections r ON r.rejection_id = l.rejection_id"
            "  JOIN companies co ON co.company_id = l.company_id"
            "  JOIN tenders   t  ON t.tender_id = r.tender_id"
            "  LEFT JOIN contacts c ON c.contact_id = l.contact_id"
            " WHERE l.status IN ('DISCOVERED','VALIDATING')")
        for row in rows:
            verdict = self.policy.evaluate(
                company_id=row["company_id"], email=row["contact_email"],
                reason_source=row["reason_source"],
                reason_text=row["rejection_reason_short"] or "",
                greeting_confidence=row["vocative_confidence"] or "UNKNOWN",
                mode=self.cfg.mode)
            self.repo.set_lead_status(row["lead_id"], verdict.status, verdict.reason)
            stats[verdict.status] = stats.get(verdict.status, 0) + 1
            if not verdict.allowed:
                continue

            variant = self.experiment.variant_for(row["company_id"])
            tpl = self.templates.get(variant)
            subj_gen, subj_conf = subject_genitive(row["tender_title"] or "")
            if not subj_gen:
                subj_gen = "за цим предметом"
                subj_conf = "NONE"
            hello, hello_conf, _hello_src = self.personalizer.address(
                row["contact_name_raw"] or "", row["company_name"] or "")
            buyer = short_org_name(row["buyer_name"] or "") or "замовник"
            verb, _verb_conf = buyer_verb(buyer)
            ctx = {"greeting": hello,
                   "followup": followup_sentence(),
                   "ua_id": row["ua_id"],
                   "company": short_org_name(row["company_name"] or ""),
                   "buyer": buyer, "buyer_verb": verb,
                   "subject_genitive": subj_gen,
                   "reason": row["rejection_reason_short"]}
            try:
                subject, body = self.templates.render(variant, ctx)
            except EngineError as err:
                self.repo.set_lead_status(row["lead_id"], "MANUAL_REVIEW_REQUIRED",
                                          err.what)
                stats["MANUAL_REVIEW_REQUIRED"] += 1
                continue

            idem = uuid.uuid4().hex
            draft = OutreachDraft(
                company_id=row["company_id"], contact_id=row["contact_id"],
                lead_id=row["lead_id"], outreach_type="FIRST_TOUCH",
                mode=self.cfg.mode,
                # Д-37: з вимкненим A/B рядка експерименту в базі немає, і
                # зовнішній ключ падав на КОЖНОМУ листі. Лід при цьому
                # позначався NOT_ELIGIBLE, тобто розсилка мовчки зупинялась,
                # а звіт показував це як штатну дедуплікацію.
                experiment_id=(self.cfg.experiment_id
                               if self.cfg.experiment_enabled else None),
                variant=variant, template_id=tpl.template_id,
                template_version=tpl.version, subject=subject, body=body,
                contact_email_original=row["contact_email"],
                delivery_email_actual=self.queue.delivery_address(row["contact_email"]),
                message_id_header=build_message_id(idem))
            try:
                self.queue.enqueue(draft)
                stats["QUEUED"] += 1
            except EngineError as err:
                self.repo.set_lead_status(row["lead_id"], "NOT_ELIGIBLE", err.code)
                stats["NOT_ELIGIBLE"] += 1
        return stats

    # --- крок 3: відправити --------------------------------------------------
    def preview(self) -> dict:
        """Сухий прогін. Нічого не надсилає і нічого не змінює в базі."""
        return self.sender.preview(self.queue, self.cfg.max_sends,
                                   os.path.join(self.cfg.out_dir, "eml"))

    def send(self, max_retries: int = 3) -> dict:
        report = self.preflight.check()
        if not report.passed:
            raise ERR["LIVE_SEND_BLOCKED"](report.blockers)
        requeued = self.queue.requeue_failed(max_retries)
        stats = self.sender.drain(self.queue, self.cfg.max_sends)
        stats["повторно_в_черзі"] = requeued
        return stats

    def reconcile(self) -> dict:
        return self.reconciler.run()

    def lead_summary(self) -> dict:
        """Скільки лідів у якому стані. Щоб порожня черга себе пояснювала."""
        return {r["status"]: r["n"] for r in self.db.q(
            "SELECT status, COUNT(*) n FROM leads GROUP BY status ORDER BY n DESC")}

    def pending_summary(self) -> dict:
        """Що лежить у чергах і незавершеному."""
        return {r["status"]: r["n"] for r in self.db.q(
            "SELECT status, COUNT(*) n FROM outreach_events"
            " GROUP BY status ORDER BY n DESC")}

    def why_not_queued(self, limit: int = 10) -> list[dict]:
        """Найчастіші причини, чому лід не пішов у чергу."""
        return [dict(r) for r in self.db.q(
            "SELECT status, COALESCE(ineligible_reason,'—') reason, COUNT(*) n"
            "  FROM leads WHERE status <> 'READY_FOR_OUTREACH'"
            " GROUP BY status, reason ORDER BY n DESC LIMIT ?", (limit,))]

    def export(self, label: Optional[str] = None) -> dict:
        label = label or now().strftime("%Y-%m-%d")
        return {"csv": self.exporter.to_csv(label),
                "xlsx": self.exporter.to_xlsx(label),
                "funnel": self.exporter.funnel()}

    def close(self) -> None:
        self.db.close()

# ============================================================================
#  БЛОК 19. ТОЧКА ВХОДУ
# ----------------------------------------------------------------------------
#  Vitalii не редагує код. Усе, що потрібно, — блок 0 і один виклик.
#  argparse тут немає навмисно: у Colab він конфліктує з ipykernel.
# ============================================================================
# Тести ВИМИКАЮТЬ це. Інакше вони читають справжні секрети середовища і
# можуть побудувати справжній клієнт Gmail — з мережею і чеканням.
# Тест, який залежить від того, що налаштовано на машині, це не тест.
ALLOW_COLAB_SECRETS = True


def _default_secret_provider(name: str) -> Optional[str]:
    val = os.environ.get(name)
    if val:
        return val
    if not ALLOW_COLAB_SECRETS:
        return None
    try:
        from google.colab import userdata                           # noqa: PLC0415
        return userdata.get(name)
    except Exception:                                               # noqa: BLE001
        return None


#: Точка підміни. Тести ставлять сюди свою функцію і не торкаються середовища.
SECRET_PROVIDER = _default_secret_provider


def _get_secret(name: str) -> Optional[str]:
    """Ключ зі змінної середовища або Colab Secrets. У файли не пишеться."""
    return SECRET_PROVIDER(name)


def make_transport(cfg: Config, dry_run: bool) -> Transport:
    """
    Обирає спосіб авторизації. OAuth користувача — перший, бо він працює
    без Workspace, не впирається в політику заборони ключів і відкриває
    рівно одну скриньку замість усього домену.
    """
    if dry_run:
        return DryRunTransport(os.path.join(cfg.out_dir, "eml"))
    if _get_secret(GMAIL_OAUTH_ENV):
        return GmailOAuthTransport(cfg.sender_email)
    if _get_secret(GMAIL_SA_ENV):
        return GmailTransport(cfg.sender_email, _get_secret(GMAIL_SA_ENV))
    raise ERR["TRANSPORT_UNAVAILABLE"](
        f"немає ні {GMAIL_OAUTH_ENV}, ні {GMAIL_SA_ENV}. "
        f"Рекомендований шлях: mint_oauth_token() -> секрет {GMAIL_OAUTH_ENV}. "
        f"У код секрети не вставляти.")


def run_colab(day: Optional[str] = None, *, dry_run: bool = True,
              db_path: Optional[str] = None, out_dir: Optional[str] = None,
              leads: Optional[list[RejectionFacts]] = None) -> dict:
    """
    Один прогін: знайти -> перевірити -> поставити в чергу -> надіслати ->
    звірити -> звіт.

    dry_run=True (за замовчуванням) складає готові листи у файли .eml
    і не надсилає нічого нікуди.
    """
    day = day or now().strftime("%Y-%m-%d")
    cfg = Config.from_globals()
    if db_path:
        cfg.db_path = db_path
    if out_dir:
        cfg.out_dir = out_dir

    legacy = None
    if leads is None:
        source: LeadSource = LegacyLeadSource()
        legacy = source.m                     # ті самі функції для персоналізації
    else:
        source = FixtureSource(leads)
        try:
            legacy = importlib.import_module(LEGACY_MODULE)
        except Exception:                                           # noqa: BLE001
            legacy = None

    engine = LeadEngine(cfg, source, make_transport(cfg, dry_run), legacy)
    try:
        print("=" * 78)
        print(f"  TENDERWIN LEAD ENGINE {ENGINE_VERSION} від {ENGINE_BUILD}")
        print(f"  {day} · режим {cfg.mode} · транспорт {engine.transport.name}")
        print(f"  прогін {engine.run_id}")
        print("=" * 78)

        report = engine.preflight.check()
        print(report.render())
        if not report.passed:
            return {"preflight": report, "stopped": True}

        chas: dict = {}                 # стадія -> секунди

        def _stage(name, fn):
            """Кожна стадія міряється. Інакше «повільно» — це здогадка."""
            t0 = time.time()
            try:
                return fn()
            finally:
                chas[name] = time.time() - t0

        facts = _stage("пошук у Prozorro",
                       lambda: source.rejections_for_day(day))
        print(f"\nЗнайдено відхилень: {len(facts)}")
        ingested = _stage("розбір і запис у базу", lambda: engine.ingest(facts))
        print(f"Прийнято: {ingested}")
        queued = _stage("черга листів", engine.build_queue)
        print(f"Черга: {queued}")

        # Порожня черга має пояснювати себе. Раніше прогін просто друкував
        # нулі, і було неможливо зрозуміти: усе гаразд чи щось зламалось.
        if not queued.get("QUEUED"):
            leads = engine.lead_summary()
            pend = engine.pending_summary()
            print("\n  Нових листів не сформовано. Стан лідів у базі:")
            for k, v in (leads or {"—": 0}).items():
                print(f"    {v:>5}  {k}")
            if pend:
                print("  Стан звернень:")
                for k, v in pend.items():
                    print(f"    {v:>5}  {k}")
            reasons = engine.why_not_queued()
            if reasons:
                print("  Найчастіші причини:")
                for r in reasons[:6]:
                    print(f"    {r['n']:>5}  {r['status']} · {r['reason'][:60]}")

        if dry_run:
            sent = engine.preview()
            print(f"\nСухий прогін: {sent}")
            if sent["складено"]:
                print(f"Листи у файлах: {os.path.join(cfg.out_dir, 'eml')}")
                print("   .txt — щоб прочитати очима, .eml — щоб відкрити поштою.")
                print("У черзі вони лишились — підуть, коли запустите з live=True.")
            rec = {"checked": 0}
        else:
            sent = engine.send()
            print(f"\nВідправка: {sent}")
            rec = engine.reconcile()
            if rec["checked"]:
                print(f"Звірка: {rec}")
        root = os.path.dirname(cfg.out_dir) or cfg.out_dir
        # Спершу ДОКУМЕНТИ, потім картка: картка має знати, які файли вже
        # лежать поруч, щоб посилатися на них, а не в Prozorro.
        case_dir = run_folder(root, engine.run_id)
        # Справи ЦЬОГО прогону, а не вся історія бази. Порожній список
        # означає «сьогодні нічого нового» — і тоді качати нічого не треба.
        tilky, lyshylos = cases_needing_attention(cfg.db_path, root,
                                                  engine.touched_rejections)
        dobrano = len(tilky) - len(engine.touched_rejections)
        if dobrano:
            print(f"\nДоліковую неповні справи: {dobrano}"
                  + (f" (ще {lyshylos} — візьме наступний прогін)"
                     if lyshylos else ""))
        try:
            # Справи, записані старою версією, не мають переліків документів.
            # Спершу підтягуємо їх із Prozorro, потім качаємо.
            reh = _stage("переліки документів", lambda: rehydrate_documents(
                cfg.db_path, legacy, tilky))
            if reh.get("перевірено"):
                print(f"\nПереліки документів підтягнуто з Prozorro: "
                      f"{reh['поповнено']} з {reh['перевірено']}"
                      + (f" · не вдалося {reh['помилок']}"
                         if reh.get("помилок") else ""))
            docs = _stage("документи справ", lambda: download_case_documents(
                cfg.db_path, root, legacy, case_dir=case_dir,
                rejection_ids=tilky))
            print(documents_report(docs, root))
        except EngineError as err:
            print(f"\n! Документи не завантажено: {err.what}")
        except Exception as err:                                # noqa: BLE001
            # Мережа не має зупиняти картки: без файлів вони працюють,
            # просто посилання ведуть у Prozorro.
            print(f"\n! Документи не завантажено: {err}")

        try:
            cards = _stage("службові картки", lambda: build_case_cards(
                cfg.db_path, root, legacy, case_dir=case_dir,
                rejection_ids=tilky))
            print(f"Службові картки: {cards['створено']} нових, "
                  f"{cards['пропущено']} уже були · "
                  f"закупівель {cards['закупівель']}")
            # Раніше тут друкувалась тека прогону — а в ній лише покажчик.
            # Людина відкривала її, не бачила жодного документа і цілком
            # логічно вирішувала, що нічого не завантажилось.
            print(f"Покажчик прогону (лише список справ): {case_dir}")
        except EngineError as err:
            print(f"\n! Картки не сформовано: {err.what}")

        if JOURNAL_ENABLED:
            zhurnal = _stage("журнал у Google Таблиці",
                             lambda: journal_write(cfg.db_path, root, tilky))
            print(f"\nЖурнал: {zhurnal['статус']}"
                  + (f" · додано {zhurnal['додано']}" if zhurnal["додано"] else "")
                  + (f" · оновлено {zhurnal['оновлено']}"
                     if zhurnal["оновлено"] else ""))
            if zhurnal.get("код"):
                print(f"   [{zhurnal['код']}]")
            if zhurnal["чому"]:
                print(f"   {zhurnal['чому']}")
            if zhurnal["посилання"]:
                print(f"   {zhurnal['посилання']}")

        out = _stage("звіт", lambda: engine.export(day))
        print(f"\nЗвіт: {out['csv']}")
        if out["xlsx"]:
            print(f"       {out['xlsx']}")
        print("\nЛійка:")
        for k, v in out["funnel"].items():
            print(f"  {k:<24} {v}")

        # Куди пішов час. Без цього рядка «прогін іде довго» неможливо
        # перевірити — тільки здогадуватись.
        print("\nЧас за стадіями:")
        for k, v in sorted(chas.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<24} {v:7.1f} с"
                  + (f"  ({v/60:.0f} хв)" if v >= 90 else ""))
        print(f"  {'РАЗОМ':<24} {sum(chas.values()):7.1f} с"
              f"  ({sum(chas.values())/60:.0f} хв)")
        return {"ingested": ingested, "queued": queued, "sent": sent,
                "reconciled": rec, "export": out, "run_id": engine.run_id,
                "час": chas}
    finally:
        engine.close()


if __name__ == "__main__":
    # Без argparse: у Colab він ламається об ipykernel, а тут не потрібен.
    run_colab(dry_run=True)


# ============================================================================
#  БЛОК 20. ПЕРЕНЕСЕННЯ ІСТОРІЇ ПОПЕРЕДНІХ РОЗСИЛОК
# ----------------------------------------------------------------------------
#  Це крок 0 перед першим бойовим прогоном. Без нього база не знає нічого
#  про тих, кому вже писали через Snov.io, і напише їм «перший» лист удруге,
#  а тим, хто казав «стоп», — знову.
#
#  Поки перенесення не виконано, preflight блокує бойову відправку.
# ============================================================================
def _read_csv(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def migrate_legacy_history(db_path: str, *, history_csv: str = "",
                           stop_list_csv: str = "",
                           snov_refusals_csv: str = "",
                           dry_run: bool = False) -> dict:
    """
    Переносить у базу:
      history_csv        — istoriya_lystiv.csv  (кому вже писали)
      stop_list_csv      — stop_list.csv        (хто просив не писати)
      snov_refusals_csv  — snov_vidmovy.csv     (відмови з кампаній Snov.io)

    Записи історії створюються зі статусом SENT і mode='LIVE': саме так вони
    займають довічний перший дотик компанії і більше нікого не потурбують.
    """
    db = Db(db_path)
    audit = Audit(db, new_run_id())
    repo = Repository(db, audit)
    stats = {"компаній": 0, "звернень": 0, "стопів": 0, "пропущено_без_ЄДРПОУ": 0}

    def company_of(row: dict) -> Optional[str]:
        edrpou = normalize_edrpou(
            row.get("ЄДРПОУ_або_ІПН") or row.get("ЄДРПОУ") or row.get("edrpou"))
        if not edrpou:
            return None
        name = (row.get("компанія") or row.get("company") or "").strip() or edrpou
        return repo.get_or_create_company(edrpou, name)

    try:
        # 1. Стоп-листи — першими: вони найсильніші
        for path, source in ((stop_list_csv, "stop_list.csv"),
                             (snov_refusals_csv, "snov_vidmovy.csv")):
            for row in _read_csv(path):
                email = (row.get("email") or row.get("Email") or "").strip().lower()
                cid = company_of(row)
                if not (email or cid):
                    continue
                if not dry_run:
                    repo.suppress(state="OPTED_OUT",
                                  reason=(row.get("причина") or "перенесено з "
                                          + source),
                                  source=f"migration:{source}",
                                  company_id=cid, email=email or None,
                                  note=row.get("нотатка") or None)
                stats["стопів"] += 1

        # 2. Історія листів
        for row in _read_csv(history_csv):
            cid = company_of(row)
            if not cid:
                stats["пропущено_без_ЄДРПОУ"] += 1
                continue
            stats["компаній"] += 1
            if repo.has_first_touch(cid) or dry_run:
                continue
            email = (row.get("email") or "").split(",")[0].strip().lower() or None
            when = (row.get("дата") or "")[:10] or now_iso()
            oid = new_id()
            try:
                db.conn.execute(
                    "INSERT INTO outreach_events(outreach_id, company_id,"
                    " outreach_type, mode, status, origin, subject_rendered,"
                    " body_rendered, contact_email_original, delivery_email_actual,"
                    " message_id_header, generated_at, sent_at, reply_state)"
                    " VALUES (?,?, 'FIRST_TOUCH','LIVE','SENT','MIGRATED',"
                    "         ?,?,?,?,?,?,?, 'NONE')",
                    (oid, cid, "[перенесено з попередньої розсилки]",
                     "[текст не зберігався у старій системі]", email,
                     email or "unknown@migrated.local",
                     f"<migrated-{oid}@{GMAIL_DOMAIN}>", when, when))
                stats["звернень"] += 1
            except sqlite3.IntegrityError:
                pass          # компанія вже має перший дотик — саме те, що треба

        if not dry_run:
            repo.mark_history_migrated(
                f"history={history_csv or '-'}, stop={stop_list_csv or '-'}, "
                f"snov={snov_refusals_csv or '-'}")
        return stats
    finally:
        db.close()


# ============================================================================
#  БЛОК 21. ДІАГНОСТИКА ПІДКЛЮЧЕННЯ ДО GMAIL
# ----------------------------------------------------------------------------
#  Запускається ОДИН раз, перед першою відправкою. Відповідає на питання,
#  на які інакше довелося б відповідати вже після невдалої розсилки:
#
#    чи встановлені бібліотеки;
#    чи є ключ і чи він взагалі ключ службового акаунта;
#    чи дозволив адмін домен-делегування і саме ті scope, що нам треба;
#    чи зберігає Gmail НАШ Message-ID — від цього залежить уся звірка
#    після збою (§15), і це єдиний спосіб дізнатися напевно.
#
#  Останній крок надсилає ОДИН лист і тільки на першу адресу з білого списку.
# ============================================================================
def check_gmail_setup(sender_email: str = SENDER_EMAIL,
                      test_recipient: Optional[str] = None,
                      send_probe: bool = True) -> dict:
    """Повертає перелік перевірок. Секрет ніде не друкується і не зберігається."""
    out: dict[str, Any] = {"кроки": [], "підсумок": "", "message_id_зберігається": None}

    def step(name, ok, detail=""):
        out["кроки"].append({"крок": name, "ok": bool(ok), "деталі": detail})
        print(f"  {'OK  ' if ok else 'НІ  '} {name}"
              + (f"\n       {detail}" if detail else ""))
        return ok

    print("ДІАГНОСТИКА GMAIL")
    print("-" * 74)

    # 1. Бібліотеки
    try:
        from google.oauth2 import service_account                   # noqa: PLC0415
        from googleapiclient.discovery import build                 # noqa: PLC0415
        step("бібліотеки google-auth і google-api-python-client", True)
    except ImportError as exc:
        step("бібліотеки google-auth і google-api-python-client", False, str(exc))
        out["підсумок"] = ("Виконайте: pip install google-api-python-client google-auth")
        return out

    # 2. Який спосіб авторизації налаштований
    if _get_secret(GMAIL_OAUTH_ENV):
        return _check_oauth_setup(out, step, sender_email, test_recipient, send_probe)

    if not _get_secret(GMAIL_SA_ENV):
        # Нічого не налаштовано. Не женемо людину в глухий кут зі службовим
        # акаунтом: у Workspace ключі здебільшого заборонені за замовчуванням.
        step(f"секрет {GMAIL_OAUTH_ENV}", False, "ще не створений")
        print()
        print("  ЩО ЗРОБИТИ")
        print("  1. Google Cloud Console -> APIs & Services -> Credentials")
        print("     -> Create credentials -> OAuth client ID -> тип «Desktop app»")
        print("     Це НЕ службовий акаунт. Ключів не завантажується, тому")
        print("     заборона на створення ключів сюди не діє.")
        print("  2. mint_oauth_token(CLIENT_ID, CLIENT_SECRET)  — крок 1")
        print("  3. відкрити посилання, підтвердити, скопіювати адресу з браузера")
        print("  4. mint_oauth_token(..., pasted_url=<адреса>)  — крок 2")
        print(f"  5. надрукований рядок -> Colab Secrets, імʼя {GMAIL_OAUTH_ENV}")
        out["підсумок"] = (f"Немає {GMAIL_OAUTH_ENV}. Зробіть його за кроками вище; "
                           f"ключ службового акаунта для цього не потрібен.")
        return out

    step(f"секрет {GMAIL_OAUTH_ENV}", False,
         f"немає, але знайдено {GMAIL_SA_ENV} — перевіряю запасний шлях")

    raw = _get_secret(GMAIL_SA_ENV)
    if not step(f"секрет {GMAIL_SA_ENV} знайдено", bool(raw),
                "" if raw else "додайте його в Colab Secrets, НЕ в код"):
        out["підсумок"] = f"Немає секрету {GMAIL_SA_ENV}."
        return out

    # 3. Це справді ключ службового акаунта
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        step("ключ читається як JSON", False, f"{exc}. Скопіювали файл цілком?")
        out["підсумок"] = "Вміст секрету не є JSON."
        return out
    need = {"type", "client_email", "client_id", "private_key"}
    missing = need - set(info)
    if not step("ключ має потрібні поля", not missing,
                f"бракує: {sorted(missing)}" if missing else ""):
        out["підсумок"] = "Це не ключ службового акаунта."
        return out
    if not step("тип ключа = service_account", info.get("type") == "service_account",
                f"тип: {info.get('type')}"):
        out["підсумок"] = "Потрібен ключ службового акаунта, не інший тип."
        return out

    # Це не секрети — їх видно в консолі; саме client_id вводиться в Admin console
    out["client_email"] = info["client_email"]
    out["client_id"] = info["client_id"]
    print(f"       службовий акаунт: {info['client_email']}")
    print(f"       Client ID для Admin console: {info['client_id']}")

    # 4. Домен-делегування на відправку
    try:
        cred = service_account.Credentials.from_service_account_info(
            info, scopes=[GMAIL_SEND_SCOPE]).with_subject(sender_email)
        api = build("gmail", "v1", credentials=cred, cache_discovery=False)
        api.users().getProfile(userId="me").execute()
        step(f"домен-делегування працює для {sender_email} (gmail.send)", True)
    except Exception as exc:                                        # noqa: BLE001
        step(f"домен-делегування для {sender_email}", False,
             f"{str(exc)[:180]}\n       Адмін має дозволити Client ID "
             f"{info['client_id']} зі scope {GMAIL_SEND_SCOPE}")
        out["підсумок"] = "Домен-делегування не налаштоване."
        return out

    # 5. Читання — потрібне для звірки після збою
    read_api = None
    try:
        rcred = service_account.Credentials.from_service_account_info(
            info, scopes=[GMAIL_READ_SCOPE]).with_subject(sender_email)
        read_api = build("gmail", "v1", credentials=rcred, cache_discovery=False)
        read_api.users().getProfile(userId="me").execute()
        step("домен-делегування працює для gmail.readonly", True)
    except Exception as exc:                                        # noqa: BLE001
        step("домен-делегування для gmail.readonly", False,
             f"{str(exc)[:160]}\n       Без цього scope звірка після збою "
             f"не працюватиме: додайте {GMAIL_READ_SCOPE}")

    if not send_probe:
        out["підсумок"] = "Підключення справне. Контрольний лист не надсилався."
        return out

    # 6. Контрольний лист і пошук його за нашим Message-ID
    recipient = (test_recipient or (TEST_RECIPIENTS[0] if TEST_RECIPIENTS else None))
    if not recipient:
        step("є куди надіслати контрольний лист", False, "порожній TEST_RECIPIENTS")
        return out
    if recipient not in TEST_RECIPIENTS:
        step("адреса контрольного листа у білому списку", False, recipient)
        out["підсумок"] = "Контрольний лист можна слати лише на власну скриньку."
        return out

    probe_key = f"probe-{uuid.uuid4().hex}"
    mid = build_message_id(probe_key)
    raw_msg = build_rfc822(
        sender_name=SENDER_NAME, sender_email=sender_email, to_email=recipient,
        subject=f"TenderWin · контрольний лист {probe_key[:14]}",
        body=("Це технічна перевірка підключення TenderWin Lead Engine.\n"
              "Клієнтам такі листи не надсилаються.\n"
              f"Мітка: {mid}\n"),
        message_id=mid)
    try:
        res = build("gmail", "v1", credentials=cred, cache_discovery=False) \
            .users().messages().send(
                userId="me",
                body={"raw": base64.urlsafe_b64encode(raw_msg).decode()}).execute()
        step(f"контрольний лист надіслано на {recipient}", True,
             f"gmail id: {res.get('id')}")
    except Exception as exc:                                        # noqa: BLE001
        step("контрольний лист", False, str(exc)[:180])
        out["підсумок"] = "Відправка не працює."
        return out

    if read_api is None:
        out["підсумок"] = ("Відправка працює. Перевірити збереження Message-ID "
                           "не вдалося: немає scope gmail.readonly.")
        return out

    found = None
    for pause in (3, 7, 15):
        time.sleep(pause)
        try:
            r = read_api.users().messages().list(
                userId="me", q=f"rfc822msgid:{probe_key}@{GMAIL_DOMAIN}",
                maxResults=1).execute()
        except Exception:                                           # noqa: BLE001
            r = {}
        if r.get("messages"):
            found = r["messages"][0]
            break

    out["message_id_зберігається"] = bool(found)
    step("Gmail зберігає наш Message-ID (звірка після збою працюватиме)",
         bool(found),
         "" if found else
         "лист надіслано, але знайти його за нашою міткою не вдалося.\n"
         "       Звірка після обриву звʼязку працювати НЕ буде — такі листи\n"
         "       підуть у DELIVERY_UNKNOWN і потребуватимуть ручної перевірки.\n"
         "       Це не блокує роботу, але про це треба знати.")

    out["підсумок"] = ("Підключення справне."
                       + ("" if found else
                          " Увага: звірка за Message-ID недоступна."))
    return out


# ============================================================================
#  БЛОК 22. АВТОРИЗАЦІЯ ЧЕРЕЗ OAUTH КОРИСТУВАЧА
# ----------------------------------------------------------------------------
#  Навіщо, якщо є службовий акаунт.
#
#  Службовий акаунт із домен-делегуванням вимагає Google Workspace і прав
#  суперадміністратора. Крім того, у нових організаціях Google Cloud за
#  замовчуванням увімкнено політику iam.disableServiceAccountKeyCreation —
#  ключ просто не створюється, і зняти це може лише Organization Policy
#  Administrator.
#
#  OAuth користувача обходить обидві перешкоди й водночас БЕЗПЕЧНІШИЙ:
#
#    службовий акаунт + делегування -> доступ до пошти ВСІХ користувачів домену
#    OAuth користувача              -> доступ до ОДНІЄЇ скриньки, вашої власної
#
#  Тому це не тимчасовий обхід, а кращий варіант для одного відправника.
#  Google теж радить OAuth 2.0 для застосунків, що діють від імені людини.
#
#  ЩО ЗБЕРІГАЄТЬСЯ В СЕКРЕТІ GMAIL_OAUTH_JSON
#      {"client_id": "...", "client_secret": "...", "refresh_token": "..."}
#  Refresh-token — це такий самий секрет, як пароль. У код не вставляти.
# ============================================================================
OAUTH_SCOPES = [GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE]
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"


def oauth_scopes() -> list[str]:
    """
    Які дозволи просимо в Google.

    Доступ до таблиць додається ЛИШЕ коли ввімкнено журнал. Причина проста:
    зміна набору дозволів робить наявний токен недостатнім, і поки людина
    не пройде авторизацію заново, пошта не піде. Вимкнений журнал не має
    ламати відправку листів (правило 10: робочий компонент не чіпаємо).
    """
    return list(OAUTH_SCOPES) + ([SHEETS_SCOPE] if JOURNAL_ENABLED else [])


def _oauth_token_info() -> dict:
    """
    Читає і перевіряє вміст секрету. Навмисно НЕ торкається бібліотек
    Google: людині корисніше почути «бракує refresh_token», ніж
    «немає google-auth», коли не так саме з секретом.
    """
    raw = _get_secret(GMAIL_OAUTH_ENV)
    if not raw:
        raise ERR["TRANSPORT_UNAVAILABLE"](
            f"немає секрету {GMAIL_OAUTH_ENV}. Створіть його через "
            f"mint_oauth_token() і покладіть у Colab Secrets.")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ERR["TRANSPORT_UNAVAILABLE"](
            f"{GMAIL_OAUTH_ENV} не читається як JSON") from exc
    missing = {"client_id", "client_secret", "refresh_token"} - set(info)
    if missing:
        raise ERR["TRANSPORT_UNAVAILABLE"](
            f"у {GMAIL_OAUTH_ENV} бракує полів: {sorted(missing)}")
    return info


def _oauth_credentials(scopes: Optional[list[str]] = None):
    """Будує облікові дані. Секрет ніде не друкується і не зберігається."""
    info = _oauth_token_info()
    try:
        from google.oauth2.credentials import Credentials              # noqa: PLC0415
    except ImportError as exc:
        raise ERR["TRANSPORT_UNAVAILABLE"]("немає google-auth") from exc
    return Credentials(
        token=None, refresh_token=info["refresh_token"],
        client_id=info["client_id"], client_secret=info["client_secret"],
        token_uri=info.get("token_uri", OAUTH_TOKEN_URI),
        scopes=scopes or oauth_scopes())


class GmailOAuthTransport:
    """
    Відправка від імені власника скриньки, без службового акаунта.

    Компроміс, який тут свідомо прийнято: один токен несе обидва scope —
    надсилання і читання. Розділити їх можна лише двома окремими
    авторизаціями, а виграш малий: цей токен і так відкриває рівно одну
    скриньку, яка належить самому відправнику. У випадку службового акаунта
    розділення було важливим, бо там доступ був до всього домену.
    """

    name = "GMAIL"

    def __init__(self, sender: str, read_enabled: bool = True):
        try:
            from googleapiclient.discovery import build                # noqa: PLC0415
        except ImportError as exc:
            raise ERR["TRANSPORT_UNAVAILABLE"](
                "немає google-api-python-client") from exc
        cred = _oauth_credentials()
        try:
            self._api = build("gmail", "v1", credentials=cred,
                              cache_discovery=False)
        except Exception as exc:                                       # noqa: BLE001
            raise ERR["TRANSPORT_UNAVAILABLE"](str(exc)) from exc
        self._read_api = self._api if read_enabled else None
        self.sender = sender

    def send(self, raw_rfc822: bytes, message_id_header: str) -> SendResult:
        body = {"raw": base64.urlsafe_b64encode(raw_rfc822).decode()}
        try:
            res = self._api.users().messages().send(userId="me", body=body).execute()
        except Exception as exc:                                       # noqa: BLE001
            text = str(exc)
            if any(w in text.lower() for w in ("timeout", "timed out", "connection",
                                               "ssl", "broken pipe")):
                return SendResult(ok=False, unknown=True, error=text[:200])
            return SendResult(ok=False, error=text[:200])
        return SendResult(ok=True, message_id=res.get("id"),
                          thread_id=res.get("threadId"),
                          provider_timestamp=now_iso())

    def find_by_message_id(self, message_id_header: str) -> Optional[dict]:
        if self._read_api is None:
            return None
        bare = message_id_header.strip("<>")
        try:
            res = self._read_api.users().messages().list(
                userId="me", q=f"rfc822msgid:{bare}", maxResults=1).execute()
        except Exception:                                              # noqa: BLE001
            return None
        msgs = res.get("messages") or []
        return msgs[0] if msgs else None


# ----------------------------------------------------------------------------
#  Отримання refresh-token. Робиться ОДИН раз.
# ----------------------------------------------------------------------------
def build_auth_url(client_id: str, redirect_uri: str = "http://localhost") -> str:
    """Посилання, яке треба відкрити у браузері й підтвердити доступ."""
    from urllib.parse import urlencode                                 # noqa: PLC0415
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(oauth_scopes()),
        "access_type": "offline",     # без цього refresh_token не видадуть
        "prompt": "consent",          # змушує видати новий refresh_token
    })


def extract_code(pasted: str) -> str:
    """
    Дістає code з того, що людина вставила: повного URL або самого коду.

    Після підтвердження браузер спробує піти на http://localhost і покаже
    помилку — це нормально. Потрібен рядок з адресного рядка.
    """
    from urllib.parse import urlparse, parse_qs, unquote               # noqa: PLC0415
    text = (pasted or "").strip()
    if not text:
        raise ValueError("Порожній рядок. Скопіюйте адресу з адресного рядка.")
    if text.startswith("http"):
        qs = parse_qs(urlparse(text).query)
        if "error" in qs:
            raise ValueError(
                f"Google повернув помилку: {qs['error'][0]}. "
                "Найчастіше це означає, що ви натиснули «Скасувати» або що "
                "вашої адреси немає в Test users на консент-екрані.")
        if "code" not in qs:
            raise ValueError(
                "У цій адресі немає параметра code — скопійовано неповну "
                "адресу.\n"
                "   Chrome ховає довгі адреси. Клацніть в адресний рядок "
                "(або Ctrl+L),\n"
                "   виділіть усе (Ctrl+A) і скопіюйте (Ctrl+C). Потрібен "
                "рядок виду\n"
                "   http://localhost/?code=4/0AX4...&scope=...")
        return unquote(qs["code"][0])
    if text.lower().startswith("localhost"):
        raise ValueError(
            "Схоже, скопійовано лише «localhost» без решти адреси.\n"
            "   Клацніть в адресний рядок (Ctrl+L), виділіть усе (Ctrl+A) "
            "і скопіюйте.")
    return text


def mint_oauth_token(client_id: str = "", client_secret: str = "",
                     redirect_uri: str = "http://localhost",
                     pasted_url: Optional[str] = None,
                     client_secret_file: Optional[str] = None,
                     quiet: bool = False) -> Optional[dict]:
    """
    Крок 1: викликати без pasted_url -> друкує посилання.
    Крок 2: відкрити посилання, підтвердити, скопіювати адресу з браузера
            і викликати ще раз, передавши її в pasted_url.

    Замість двох значень можна передати завантажений файл клієнта:
        mint_oauth_token(client_secret_file='/content/client_secret_….json')
    Так менше шансів переплутати поля або взяти не той файл.

    Повертає словник, який треба покласти в секрет GMAIL_OAUTH_JSON.
    """
    if not client_secret_file and not client_id:
        # Нічого не передали — спробуємо знайти файл самі.
        found = find_oauth_client()
        if len(found) == 1:
            client_secret_file = found[0]
            print(f"Знайдено файл клієнта: {client_secret_file}\n")
        elif len(found) > 1:
            print("Знайдено кілька файлів клієнта. Вкажіть потрібний:")
            for f in found:
                print(f"   E.mint_oauth_token(client_secret_file='{f}')")
            return None
        else:
            raise _e("OAUTH_CLIENT_FILE_NOT_FOUND",
                     "Не знайдено файл клієнта OAuth",
                     "Авторизація", False,
                     "Завантажте client_secret_….json у Colab (папка ліворуч -> "
                     "значок завантаження) і викличте ще раз. "
                     "Що саме створити: E.whats_i_need()")

    if client_secret_file:
        info = whats_this_file(client_secret_file)
        if info["вид"] == "FILE_NOT_FOUND":
            raise _e("OAUTH_CLIENT_FILE_NOT_FOUND",
                     f"Файл не знайдено: {client_secret_file}",
                     "Авторизація", False,
                     "Викличте E.mint_oauth_token() без аргументів — "
                     "він знайде файл сам.")
        if info["вид"] == "SERVICE_ACCOUNT_KEY":
            raise _e("WRONG_CREDENTIAL_TYPE",
                     "Це ключ службового акаунта, а потрібен клієнт OAuth",
                     "Авторизація", False,
                     "Створіть OAuth client ID типу «Desktop app». "
                     "Підказка: E.whats_i_need()")
        if not info.get("client_id"):
            raise _e("WRONG_CREDENTIAL_TYPE",
                     f"У файлі немає client_id (розпізнано як {info['вид']})",
                     "Авторизація", False, "Підказка: E.whats_i_need()")
        client_id = info["client_id"]
        client_secret = info.get("client_secret") or ""
        print()

    if not client_id or not client_secret:
        raise _e("MISSING_OAUTH_CLIENT",
                 "Не задано client_id / client_secret",
                 "Авторизація", False,
                 "Передайте client_secret_file=<шлях до завантаженого файлу> "
                 "або обидва значення окремо. Підказка: E.whats_i_need()")

    if not pasted_url:
        print("КРОК 1. Відкрийте це посилання у браузері, увійдіть як власник")
        print("        скриньки і підтвердьте доступ:\n")
        print(build_auth_url(client_id, redirect_uri))
        print("\nКРОК 2. Браузер піде на http://localhost і покаже помилку —")
        print("        так і має бути. Скопіюйте АДРЕСУ з адресного рядка")
        print("        і викличте цю функцію ще раз із pasted_url=<адреса>.")
        return None

    code = extract_code(pasted_url)
    try:
        import requests                                                # noqa: PLC0415
    except ImportError as exc:
        raise ERR["TRANSPORT_UNAVAILABLE"]("немає requests") from exc
    resp = requests.post(OAUTH_TOKEN_URI, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=30)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or "refresh_token" not in data:
        raise _e("OAUTH_EXCHANGE_FAILED",
                 f"Google не видав refresh_token: {data.get('error_description') or data.get('error') or resp.status_code}",
                 "Авторизація", False,
                 "Найчастіші причини: код уже використали (він одноразовий), "
                 "минуло забагато часу, або тип клієнта не «Desktop app». "
                 "Почніть із кроку 1 наново.")
    out = {"client_id": client_id, "client_secret": client_secret,
           "refresh_token": data["refresh_token"], "token_uri": OAUTH_TOKEN_URI,
           # Навіщо: refresh-token не показує, з якими дозволами його видали.
           # Без цього рядка неможливо відрізнити «токен робочий» від
           # «токен робочий, але без доступу до таблиць» — і людина ловить
           # 403 вже посеред роботи.
           "scopes": list(oauth_scopes())}
    if not quiet:
        # quiet=True використовує setup_gmail: там токен іде одразу у файл,
        # і друкувати його у вивід ноутбука не треба — це секрет.
        print("Готово. Покладіть ЦЕ у Colab Secrets під іменем "
              f"{GMAIL_OAUTH_ENV} і більше нікуди не копіюйте:\n")
        print(json.dumps(out, ensure_ascii=False))
    return out


def _check_oauth_setup(out: dict, step, sender_email: str,
                       test_recipient: Optional[str], send_probe: bool) -> dict:
    """Гілка діагностики для OAuth користувача."""
    step(f"секрет {GMAIL_OAUTH_ENV} знайдено", True)
    try:
        cred = _oauth_credentials()
    except EngineError as err:
        step("токен читається", False, err.what)
        out["підсумок"] = err.what
        return out
    step("токен має client_id, client_secret і refresh_token", True)

    try:
        from googleapiclient.discovery import build                    # noqa: PLC0415
        api = build("gmail", "v1", credentials=cred, cache_discovery=False)
        prof = api.users().getProfile(userId="me").execute()
    except Exception as exc:                                           # noqa: BLE001
        step("токен приймається Google", False, str(exc)[:180])
        out["підсумок"] = ("Токен не працює. Найчастіше — його відкликали "
                           "або змінили пароль. Отримайте новий через "
                           "mint_oauth_token().")
        return out

    actual = prof.get("emailAddress", "")
    out["скринька"] = actual
    step(f"токен відкриває скриньку {actual}", True)
    if actual.lower() != sender_email.lower():
        step("скринька збігається з відправником у налаштуваннях", False,
             f"токен для {actual}, а SENDER_EMAIL = {sender_email}. "
             f"Листи підуть від {actual}.")
        out["підсумок"] = "Токен не для тієї скриньки."
        return out
    step("скринька збігається з SENDER_EMAIL", True)

    if not send_probe:
        out["підсумок"] = "Підключення справне. Контрольний лист не надсилався."
        return out

    recipient = test_recipient or (TEST_RECIPIENTS[0] if TEST_RECIPIENTS else None)
    if not recipient or recipient not in TEST_RECIPIENTS:
        step("адреса контрольного листа у білому списку", False, str(recipient))
        out["підсумок"] = "Контрольний лист можна слати лише на власну скриньку."
        return out

    probe_key = f"probe-{uuid.uuid4().hex}"
    mid = build_message_id(probe_key)
    raw_msg = build_rfc822(
        sender_name=SENDER_NAME, sender_email=actual, to_email=recipient,
        subject=f"TenderWin · контрольний лист {probe_key[:14]}",
        body=("Це технічна перевірка підключення TenderWin Lead Engine.\n"
              "Клієнтам такі листи не надсилаються.\n"
              f"Мітка: {mid}\n"),
        message_id=mid)
    try:
        res = api.users().messages().send(
            userId="me",
            body={"raw": base64.urlsafe_b64encode(raw_msg).decode()}).execute()
        step(f"контрольний лист надіслано на {recipient}", True,
             f"gmail id: {res.get('id')}")
    except Exception as exc:                                           # noqa: BLE001
        step("контрольний лист", False, str(exc)[:180])
        out["підсумок"] = "Відправка не працює."
        return out

    found = None
    for pause in (3, 7, 15):
        time.sleep(pause)
        try:
            r = api.users().messages().list(
                userId="me", q=f"rfc822msgid:{probe_key}@{GMAIL_DOMAIN}",
                maxResults=1).execute()
        except Exception:                                              # noqa: BLE001
            r = {}
        if r.get("messages"):
            found = r["messages"][0]
            break
    out["message_id_зберігається"] = bool(found)
    step("Gmail зберігає наш Message-ID (звірка після збою працюватиме)",
         bool(found),
         "" if found else
         "лист пішов, але знайти його за нашою міткою не вдалося.\n"
         "       Після обриву звʼязку такі листи підуть у DELIVERY_UNKNOWN\n"
         "       і потребуватимуть ручної перевірки. Роботу це не блокує.")
    out["підсумок"] = ("Підключення справне."
                       + ("" if found else " Увага: звірка за Message-ID недоступна."))
    return out


# ============================================================================
#  БЛОК 23. ТОНКІ ОБГОРТКИ ДЛЯ COLAB
# ----------------------------------------------------------------------------
#  Логіка живе тут, у файлі .py. У ноутбуку лишаються виклики на один рядок.
#
#  Навіщо: коли ноутбук відкритий у Colab, а файл .ipynb на Диску замінюють,
#  вкладка втрачає можливість зберегтися і починає працювати зі старою
#  версією з памʼяті браузера. Людина запускає не те, що думає, що запускає.
#  Що менше логіки в клітинках — то рідше доводиться міняти ноутбук.
# ============================================================================
def colab_secret(name: str) -> Optional[str]:
    """
    Читає секрет Colab, не падаючи, якщо його ще немає.

    `userdata.get()` кидає SecretNotFoundError за відсутності секрету —
    саме в тому випадку, коли перевірка й потрібна найбільше.
    """
    try:
        from google.colab import userdata                            # noqa: PLC0415
        return userdata.get(name)
    except Exception:                                                # noqa: BLE001
        return None


def colab_check(sender_email: Optional[str] = None,
                send_probe: bool = True) -> dict:
    """Клітинка «перевірка підключення» — один рядок замість восьми."""
    val = colab_secret(GMAIL_OAUTH_ENV)
    if val:
        os.environ[GMAIL_OAUTH_ENV] = val
    else:
        print(f"Секрету {GMAIL_OAUTH_ENV} ще немає. Якщо ви не робили крок із")
        print("токеном — це нормально. Нижче написано, що робити.\n")
    rep = check_gmail_setup(sender_email or SENDER_EMAIL, send_probe=send_probe)
    print()
    print("ПІДСУМОК:", rep["підсумок"])
    return rep


def colab_state(db_path: str, out_dir: str = ".", limit: int = 15) -> None:
    """Клітинка «що зараз у базі». Нічого не змінює, мережі не торкається."""
    eng = LeadEngine(Config(db_path=db_path, out_dir=out_dir, mode="TEST",
                            verbose=False),
                     FixtureSource([]), DryRunTransport(os.path.join(out_dir, "eml")))
    try:
        print("ЛІДИ")
        for k, v in (eng.lead_summary() or {"—": 0}).items():
            print(f"  {v:>5}  {k}")
        print("\nЗВЕРНЕННЯ")
        for k, v in (eng.pending_summary() or {"—": 0}).items():
            print(f"  {v:>5}  {k}")
        reasons = eng.why_not_queued()
        if reasons:
            print("\nЧОМУ ЛІД НЕ ПІШОВ У ЧЕРГУ")
            for r in reasons:
                print(f"  {r['n']:>5}  {r['status']} · {r['reason'][:70]}")
        rows = eng.db.q(
            "SELECT t.ua_id, c.company_name, l.status,"
            "       substr(r.rejection_reason_raw,1,70) reason"
            "  FROM leads l"
            "  JOIN rejections r ON r.rejection_id = l.rejection_id"
            "  JOIN companies c  ON c.company_id = l.company_id"
            "  JOIN tenders t    ON t.tender_id = r.tender_id"
            " ORDER BY r.discovered_at DESC LIMIT ?", (limit,))
        if rows:
            print("\nОСТАННІ ЗНАЙДЕНІ ВІДХИЛЕННЯ")
            for r in rows:
                print(f"  {r['ua_id']}  {r['status']:<22} "
                      f"{(r['company_name'] or '')[:26]}")
                print(f"      {r['reason']}…")
    finally:
        eng.close()


def colab_tests(code_dir: str = ".") -> bool:
    """Клітинка «тести». Усі набори в одному процесі, з локального диска."""
    import subprocess                                                # noqa: PLC0415
    t0 = time.time()
    r = subprocess.run([sys.executable, "check_names.py"], cwd=code_dir,
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    r = subprocess.run(
        [sys.executable, "-m", "unittest",
         "test_smoke", "test_adapter", "test_lead_engine", "test_kartka",
         "test_lyst_v2", "test_spec_2026_09_03", "test_shvydkist",
         "test_zhurnal", "test_dedup_audyt", "test_dokumenty"],
        cwd=code_dir, capture_output=True, text=True)
    print("\n".join([l for l in r.stderr.splitlines() if l.strip()][-3:]))
    print(f"\nчас: {time.time() - t0:.1f} с")
    if r.returncode != 0:
        print("\nЩОСЬ НЕ ПРОЙШЛО — далі не йдіть:")
        print("\n".join(l for l in r.stderr.splitlines()
                        if "FAIL:" in l or "ERROR:" in l)[:2000])
    return r.returncode == 0


def colab_setup(dysk: str = "/content/drive/MyDrive/TENDERWIN",
                kod: str = "/content/tw") -> dict:
    """
    Копіює код із Диска на локальний диск і скидає стару версію з памʼяті.
    Диск — мережева файлова система: імпорт звідти повільний.
    """
    import shutil                                                    # noqa: PLC0415
    if not drive_is_mounted(dysk):
        print("! Google Диск НЕ підключений.")
        print("! Виконайте drive.mount у клітинці 2 і дочекайтесь успіху,")
        print("! інакше все, що ви зробите далі, зникне разом із сеансом.\n")
    import importlib                                                 # noqa: PLC0415
    fajly = ["tenderwin_lead_engine.py", "lead_machine_v1.py", "check_names.py",
             "test_lead_engine.py", "test_adapter.py", "test_smoke.py",
             "test_kartka.py", "test_lyst_v2.py",
             "test_spec_2026_09_03.py", "test_shvydkist.py",
             "test_zhurnal.py", "test_dedup_audyt.py",
             "test_dokumenty.py"]
    os.makedirs(kod, exist_ok=True)
    ok, nema = [], []
    for f in fajly:
        src = os.path.join(dysk, f)
        (ok if os.path.exists(src) else nema).append(f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(kod, f))
    # Прибираємо з памʼяті СТАРУ версію разом із її кешем байткоду. Без цього
    # Python лишає в sys.modules те, що завантажив раніше, і оновлений файл
    # на Диску не має жодного значення.
    for m in list(sys.modules):
        if m.split(".")[0] in {f[:-3] for f in fajly}:
            del sys.modules[m]
    cache = os.path.join(kod, "__pycache__")
    if os.path.isdir(cache):
        shutil.rmtree(cache, ignore_errors=True)
    if kod not in sys.path:
        sys.path.insert(0, kod)
    if sys.path[0] != kod:
        sys.path.remove(kod)
        sys.path.insert(0, kod)          # локальна копія має бути ПЕРШОЮ
    importlib.invalidate_caches()

    print(f"код локально: {kod} · скопійовано {len(ok)} з {len(fajly)}")
    if nema:
        print(f"НЕ ЗНАЙДЕНО: {', '.join(nema)} — покладіть їх у {dysk}")

    # Перевіряємо ТЕ, ЩО СПРАВДІ ЗАВАНТАЖИЛОСЬ, а не те, що ми скопіювали.
    stan = "ok"
    try:
        eng = importlib.import_module("tenderwin_lead_engine")
        ver = getattr(eng, "ENGINE_VERSION", "")
        build = getattr(eng, "ENGINE_BUILD", "")
        brak = [n for n in REQUIRED_API if not hasattr(eng, n)]
        print("движок: "
              + (f"версія {ver} від {build}" if ver and build
                 else f"версія {ver}" if ver
                 else "версія НЕ ВКАЗАНА — файл старіший за 0.7.0")
              + f" · {getattr(eng, '__file__', '?')}")
        if brak:
            stan = "стара_версія"
            print("\n! НА ДИСКУ СТАРА ВЕРСІЯ ДВИЖКА.")
            print(f"! Бракує: {', '.join(brak[:6])}"
                  + (" та інших" if len(brak) > 6 else ""))
            print(f"! Покладіть новий tenderwin_lead_engine.py у {dysk},")
            print("! замінивши старий, і перезапустіть ЦЮ клітинку.")
            print("! Далі не йдіть — решта клітинок впаде AttributeError.")
    except Exception as exc:                                        # noqa: BLE001
        stan = "не_імпортується"
        print(f"\n! Движок не імпортується: {exc}")
    return {"скопійовано": ok, "немає": nema, "kod": kod, "стан": stan}


# ============================================================================
#  БЛОК 24. «ЩО ЦЕ ЗА ФАЙЛ» — РОЗПІЗНАВАННЯ ОБЛІКОВИХ ДАНИХ GOOGLE
# ----------------------------------------------------------------------------
#  У консолі Google лежать три різні речі, і всі три завантажуються як .json.
#  Переплутати їх легко, а помилка коштує години:
#
#    ключ службового акаунта   {"type": "service_account", ...}
#      імʼя: <проєкт>-<хеш>.json        НАМ НЕ ПОТРІБЕН
#
#    клієнт OAuth              {"installed": {"client_id": ..., ...}}
#      імʼя: client_secret_....apps.googleusercontent.com.json    ЦЕ ПОТРІБНО
#
#    наш токен                 {"client_id","client_secret","refresh_token"}
#      його друкує mint_oauth_token()   ЦЕ ЙДЕ В СЕКРЕТ GMAIL_OAUTH_JSON
#
#  Ця функція дивиться у файл і каже, що це і що з ним робити.
#  Секретів вона не друкує.
# ============================================================================
def find_oauth_client(dirs: Optional[list[str]] = None) -> list[str]:
    """
    Шукає завантажений файл клієнта OAuth у типових місцях.
    Google називає його client_secret_….apps.googleusercontent.com.json —
    за цим і шукаємо, щоб не змушувати людину вписувати шлях.
    """
    import glob as _glob                                              # noqa: PLC0415
    places = dirs or ["/content", "/content/drive/MyDrive/TENDERWIN",
                      "/content/drive/MyDrive", os.getcwd(), "."]
    out: list[str] = []
    for d in places:
        for pat in ("client_secret*.json", "*apps.googleusercontent.com.json"):
            for f in _glob.glob(os.path.join(d, pat)):
                if f not in out:
                    out.append(f)
    return out


def whats_this_file(path_or_text: str) -> dict:
    """Розпізнає файл облікових даних Google і пояснює наступний крок."""
    raw = str(path_or_text)
    looks_like_path = raw.endswith(".json") or raw.startswith("/") or "\\" in raw
    if os.path.exists(raw):
        with open(raw, encoding="utf-8") as fh:
            raw = fh.read()
    elif looks_like_path:
        # Найчастіша помилка: у змінній лишився приклад шляху з крапками.
        # Сказати «це не JSON» тут — збити людину зі сліду.
        print(f"ФАЙЛ НЕ ЗНАЙДЕНО за шляхом:\n   {path_or_text}")
        found = find_oauth_client()
        if found:
            print("\n   А ось що знайшлося поруч — схоже, це воно:")
            for f in found:
                print(f"     {f}")
            print("\n   Підставте цей шлях або просто викличте")
            print("   E.mint_oauth_token()  — він знайде файл сам.")
        else:
            print("\n   Завантажте файл у Colab: папка ліворуч -> значок")
            print("   завантаження. Потім E.mint_oauth_token() знайде його сам.")
        return {"вид": "FILE_NOT_FOUND", "придатний": False, "знайдено": found}

    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print("Це не JSON. Відкрийте файл у Блокноті й скопіюйте вміст цілком.")
        return {"вид": "NOT_JSON", "придатний": False}

    # 1. Ключ службового акаунта
    if d.get("type") == "service_account":
        print("ЦЕ КЛЮЧ СЛУЖБОВОГО АКАУНТА — нам він НЕ потрібен.")
        print(f"   акаунт : {d.get('client_email', '?')}")
        print(f"   проєкт : {d.get('project_id', '?')}")
        print()
        print("   Чому не підходить: щоб надсилати від імені людини, службовому")
        print("   акаунту потрібне домен-делегування, а воно є лише в Google")
        print("   Workspace і дає доступ до пошти ВСІХ користувачів домену.")
        print()
        print("   ЩО ЗРОБИТИ: видаліть цей ключ (IAM & Admin -> Service Accounts")
        print("   -> Keys -> видалити) і створіть OAuth client ID типу")
        print("   «Desktop app» — див. whats_i_need().")
        return {"вид": "SERVICE_ACCOUNT_KEY", "придатний": False}

    # 2. Клієнт OAuth — саме те, що треба
    for key in ("installed", "web"):
        if isinstance(d.get(key), dict) and "client_id" in d[key]:
            block = d[key]
            tip = ("Desktop app" if key == "installed" else "Web application")
            print(f"ЦЕ КЛІЄНТ OAUTH ({tip}) — саме те, що потрібно.")
            print(f"   client_id: …{str(block.get('client_id'))[-28:]}")
            if key == "web":
                print()
                print("   УВАГА: тип «Web application» вимагає точного redirect URI.")
                print("   Для Colab простіше створити новий клієнт типу «Desktop app».")
            print()
            print("   ЩО ЗРОБИТИ ДАЛІ — два кроки:")
            print("     E.mint_oauth_token(client_secret_file='<шлях до цього файлу>')")
            print("     потім те саме з pasted_url=<адреса з браузера>")
            return {"вид": "OAUTH_CLIENT", "придатний": key == "installed",
                    "client_id": block.get("client_id"),
                    "client_secret": block.get("client_secret")}

    # 3. Наш готовий токен
    if {"client_id", "client_secret", "refresh_token"} <= set(d):
        print("ЦЕ ВЖЕ ГОТОВИЙ ТОКЕН TENDERWIN.")
        print(f"   ЩО ЗРОБИТИ: покладіть його вміст у Colab Secrets під іменем")
        print(f"   {GMAIL_OAUTH_ENV} і увімкніть Notebook access.")
        return {"вид": "TENDERWIN_TOKEN", "придатний": True}

    if "client_id" in d and "client_secret" in d:
        print("ЦЕ КЛІЄНТ OAUTH без refresh_token.")
        print("   ЩО ЗРОБИТИ: E.mint_oauth_token(client_secret_file=<цей файл>)")
        return {"вид": "OAUTH_CLIENT_FLAT", "придатний": True,
                "client_id": d["client_id"], "client_secret": d["client_secret"]}

    print("Не впізнаю цей файл. Ключі верхнього рівня:", sorted(d)[:8])
    return {"вид": "UNKNOWN", "придатний": False}


def whats_i_need() -> None:
    """Друкує, що саме створити в консолі Google. Без жодних секретів."""
    print("ЩО ПОТРІБНО ДЛЯ ВІДПРАВКИ ПОШТИ")
    print("=" * 70)
    print()
    print("У консолі Google під кнопкою «Create credentials» є ТРИ пункти.")
    print("Потрібен СЕРЕДНІЙ. Два інші ведуть у глухий кут:")
    print()
    print("   API key           -> НІ, це не для пошти")
    print("   OAuth client ID   -> ТАК, тип «Desktop app»          <<<<<<")
    print("   Service account   -> НІ, потребує Workspace-делегування,")
    print("                        а ключі до нього Workspace забороняє")
    print()
    print("КРОКИ")
    print("  1. console.cloud.google.com -> APIs & Services -> Library")
    print("     -> Gmail API -> Enable")
    print("  2. APIs & Services -> OAuth consent screen (Google Auth Platform)")
    print("     -> заповнити назву й пошту")
    print("     -> якщо домен на Workspace: Internal")
    print("        якщо звичайний Gmail:    External + додати свою адресу")
    print("        у розділ Test users")
    print("  3. APIs & Services -> Credentials -> Create credentials")
    print("     -> OAuth client ID -> Application type: Desktop app -> Create")
    print("  4. Завантажити JSON. Правильний файл називається приблизно")
    print("     client_secret_123-abc.apps.googleusercontent.com.json")
    print("     і всередині починається з {\"installed\": {...")
    print()
    print("ЯК ПЕРЕВІРИТИ, ЩО ФАЙЛ ТОЙ")
    print("     E.whats_this_file('/шлях/до/файлу.json')")
    print()
    print("ЩО ПОТІМ КЛАСТИ В СЕКРЕТ")
    print(f"  У {GMAIL_OAUTH_ENV} йде НЕ завантажений файл, а рядок, який")
    print("  надрукує mint_oauth_token() на другому кроці. Він містить")
    print("  refresh_token — саме він дає право надсилати.")


# ============================================================================
#  БЛОК 25. ОДНА КОМАНДА НА ВСЕ
# ----------------------------------------------------------------------------
#  Що тут прибрано порівняно з попереднім шляхом:
#
#    було: завантажити файл -> вписати шлях -> викликати -> відкрити посилання
#          -> скопіювати адресу -> вставити в змінну -> викликати ще раз
#          -> скопіювати рядок -> створити секрет -> назвати без помилки
#          -> увімкнути доступ -> запустити прогін
#
#    стало: E.go()  -> відкрити посилання -> вставити адресу у віконце
#
#  Токен зберігається у файл на Диску поруч із базою і читається сам.
#  Colab Secrets більше не обовʼязкові: якщо секрет є — беремо його, ні —
#  беремо файл. Це трохи слабший захист, ніж Secrets, зате робочий:
#  токен, який людина не може встановити, не захищає нічого.
# ============================================================================
TOKEN_FILE_NAME = ".gmail_token.json"


#: Винесено в константи, щоб тести могли перевіряти саму логіку, не
#: залежачи від того, чи змонтований Диск на машині, де вони біжать.
DRIVE_PREFIX = "/content/drive"
DRIVE_MOUNT_ROOT = "/content/drive/MyDrive"


def drive_is_mounted(path: str) -> bool:
    """
    Чи справді змонтований Диск під цим шляхом.

    Дивимось на ТОЧКУ МОНТУВАННЯ, а не на існування папок. Перша версія
    перевіряла, чи є /content/drive/MyDrive — і її можна було обдурити:
    `os.makedirs` на неіснуючому Диску створює весь ланцюжок, включно з
    MyDrive, і наступного разу перевірка сказала б «усе гаразд».
    Звичайний каталог точкою монтування не є, тому os.path.ismount
    відрізняє їх надійно.
    """
    if not str(path).startswith(DRIVE_PREFIX):
        return True                     # не шлях Диска — питання не стоїть
    try:
        if os.path.ismount(DRIVE_PREFIX):
            return True
    except OSError:
        pass
    # Запасна ознака: у деяких середовищах Диск монтується глибше.
    try:
        return os.path.ismount(DRIVE_MOUNT_ROOT)
    except OSError:
        return False


def ensure_drive(path: str, try_remount: bool = True) -> bool:
    """
    Гарантує, що шлях на Диску справді доступний.

    Тихо працювати з неіснуючим Диском не можна: `os.makedirs` створить
    звичайну локальну папку з тією самою назвою, і людина буде впевнена,
    що база в безпеці. Наступного дня її не буде.
    """
    if drive_is_mounted(path):
        return True
    if not try_remount:
        return False
    print("Google Диск не підключений. Пробую підключити ще раз…")
    try:
        from google.colab import drive                              # noqa: PLC0415
        drive.mount("/content/drive", force_remount=True)
    except Exception as exc:                                        # noqa: BLE001
        print(f"   не вдалося: {str(exc)[:120]}")
    return drive_is_mounted(path)


def storage_warning(path: str) -> None:
    print("=" * 74)
    print("  ДИСК НЕ ПІДКЛЮЧЕНО — РОБОТУ ЗУПИНЕНО")
    print("=" * 74)
    print(f"  Шлях {path} зараз указує в нікуди.")
    print("  Якби ми пішли далі, база і токен лягли б у тимчасову папку")
    print("  і зникли разом із сеансом Colab. Завтра листи пішли б")
    print("  повторно тим самим людям.")
    print()
    print("  ЩО ЗРОБИТИ")
    print("  1. Перезапустіть клітинку 2. У вікні дозволу натисніть")
    print("     «Дозволити» і виберіть акаунт, на Диску якого лежить")
    print("     папка TENDERWIN.")
    print("  2. Якщо вікно не зʼявилось — його заблокував браузер.")
    print("     Дозвольте спливні вікна для colab.research.google.com")
    print("     і спробуйте ще раз.")
    print("  3. Не допомогло — Середовище виконання -> Перезапустити")
    print("     середовище, і з клітинки 1.")
    print()
    print("  Якщо треба просто спробувати ЗАРАЗ, без збереження між")
    print("  сеансами — запустіть так, і база лишиться в Colab:")
    print("     E.go(live=True, dysk='/content/TENDERWIN')")
    print("=" * 74)

#: Куди класти токен. Ставиться в colab_setup / go, щоб не хардкодити шлях.
TOKEN_DIR = ""


def _token_path() -> str:
    return os.path.join(TOKEN_DIR or os.getcwd(), TOKEN_FILE_NAME)


def save_token(data: dict, directory: Optional[str] = None) -> str:
    """Зберігає токен поруч із базою. Права 600, у Git такому не місце."""
    global TOKEN_DIR
    if directory:
        TOKEN_DIR = directory
    path = _token_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass                      # на змонтованому Диску прав може не бути
    return path


def load_token() -> Optional[str]:
    path = _token_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def token_scopes() -> Optional[list]:
    """Дозволи наявного токена або None, якщо вони не записані."""
    raw = _provider_with_file(GMAIL_OAUTH_ENV)
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except (ValueError, TypeError):
        return None
    scopes = info.get("scopes")
    return list(scopes) if isinstance(scopes, list) else None


def missing_scopes() -> list:
    """Яких дозволів бракує наявному токену. Порожньо = все гаразд."""
    have = token_scopes()
    if have is None:
        return []                  # старий токен без переліку — не гадаємо
    return [x for x in oauth_scopes() if x not in have]


def reset_gmail_token(dysk: str = "/content/drive/MyDrive/TENDERWIN") -> dict:
    """
    Прибирає збережений токен, щоб авторизуватись наново.

    Потрібно рівно тоді, коли змінився набір дозволів: наявний токен
    ЗАЛИШАЄТЬСЯ робочим для пошти, тому сам він не оновиться ніколи.
    """
    global TOKEN_DIR
    TOKEN_DIR = dysk
    path = _token_path()
    was = os.path.exists(path)
    if was:
        try:
            os.remove(path)
        except OSError as exc:
            return {"видалено": False, "чому": str(exc), "файл": path}
    secret = _default_secret_provider(GMAIL_OAUTH_ENV)
    print("Токен видалено." if was else "Збереженого токена не було.")
    if secret:
        print(f"! У Colab Secrets лишається {GMAIL_OAUTH_ENV} — його движок")
        print("! бере ПЕРШИМ. Видаліть цей секрет, інакше авторизація наново")
        print("! нічого не змінить.")
    print("Далі: E.setup_gmail(DYSK)")
    return {"видалено": was, "файл": path, "секрет_лишився": bool(secret)}


def _provider_with_file(name: str) -> Optional[str]:
    """
    Порядок пошуку: змінна середовища -> Colab Secrets -> файл на Диску.
    Файл останній: якщо людина налаштувала секрет, поважаємо її вибір.
    """
    val = _default_secret_provider(name)
    if val:
        return val
    if name == GMAIL_OAUTH_ENV:
        return load_token()
    return None


SECRET_PROVIDER = _provider_with_file


def setup_gmail(state_dir: Optional[str] = None,
                client_secret_file: Optional[str] = None) -> bool:
    """
    Уся авторизація в одному виклику.

    Питає адресу з браузера через input() — у Colab це звичайне віконце
    під клітинкою. Токен зберігає сам. Повторний виклик, коли токен уже
    робочий, нічого не робить.
    """
    global TOKEN_DIR
    if state_dir:
        if not ensure_drive(state_dir):
            storage_warning(state_dir)
            return False
        TOKEN_DIR = state_dir

    if load_token() or _default_secret_provider(GMAIL_OAUTH_ENV):
        print("Токен уже є. Перевіряю…")
        brakuye = missing_scopes()
        if brakuye:
            # Токен робочий для пошти, але без нових дозволів. Сам він
            # не оновиться: перевірка пошти проходить, і раніше движок
            # радісно повертав True, а 403 прилітав уже в журналі.
            print("! Токен випущено БЕЗ таких дозволів:")
            for x in brakuye:
                print(f"!   {x}")
            print("! Потрібна одноразова повторна авторизація.\n")
        try:
            rep = check_gmail_setup(SENDER_EMAIL, send_probe=False)
            if not brakuye and rep.get("кроки") \
                    and all(k["ok"] for k in rep["кроки"]):
                return True
        except EngineError as err:
            print(err)
        print("Токен не працює — робимо новий.\n")

    found = ([client_secret_file] if client_secret_file else find_oauth_client())
    found = [f for f in found if f and os.path.exists(f)]
    if not found:
        print("НЕ ЗНАЙДЕНО ФАЙЛ КЛІЄНТА OAUTH")
        print()
        whats_i_need()
        return False

    import io as _io                                                  # noqa: PLC0415
    import contextlib as _ctx                                          # noqa: PLC0415
    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        info = whats_this_file(found[0])
    if not info.get("client_id"):
        print(_buf.getvalue())
        return False
    print(f"Файл клієнта: {os.path.basename(found[0])}")
    cid, csec = info["client_id"], info.get("client_secret") or ""

    print()
    print("=" * 74)
    print("  КРОК 1. Відкрийте посилання нижче у новій вкладці.")
    print(f"  Увійдіть як {SENDER_EMAIL} — саме з цієї скриньки підуть листи.")
    print("  Натисніть «Дозволити».")
    print("=" * 74)
    print()
    print(build_auth_url(cid))
    print()
    print("  КРОК 2. Браузер піде на http://localhost і покаже")
    print("  «Немає звʼязку із сайтом» — ЦЕ НОРМАЛЬНО, так і має бути.")
    print()
    print("  Клацніть в АДРЕСНИЙ РЯДОК браузера (або Ctrl+L),")
    print("  виділіть усе (Ctrl+A), скопіюйте (Ctrl+C).")
    print("  Chrome показує адресу скорочено — треба саме повний рядок,")
    print("  у ньому має бути «code=».")
    print()
    print("  Вставте його у віконце нижче і натисніть Enter.")
    print()

    token = None
    for sproba in (1, 2, 3):
        try:
            pasted = input("Адреса з браузера: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nСкасовано.")
            return False
        if not pasted:
            print("   Порожньо. Вставте адресу і натисніть Enter.")
            continue
        try:
            token = mint_oauth_token(cid, csec, pasted_url=pasted, quiet=True)
            break
        except ValueError as err:
            # Помилка копіювання — даємо ще спробу, не змушуючи все повторювати
            print(f"\n   {err}")
            if sproba < 3:
                print("\n   Спробуйте ще раз.")
        except EngineError as err:
            print(f"\n{err}")
            return False
    if not token:
        print("\nНе вдалося. Запустіть клітинку ще раз — посилання буде нове.")
        return False

    path = save_token(token)
    print()
    print(f"Токен збережено: {path}")
    print("Створювати секрет у Colab не треба — далі він читається сам.")
    print()
    rep = check_gmail_setup(SENDER_EMAIL, send_probe=False)
    ok = bool(rep.get("кроки")) and all(k["ok"] for k in rep["кроки"])
    return ok


def go(day: Optional[str] = None, live: bool = False,
       dysk: str = "/content/drive/MyDrive/TENDERWIN") -> Optional[dict]:
    """
    Одна команда: авторизація (якщо треба) -> пошук -> листи -> звіт.

        E.go()              сухий прогін, нічого не надсилає
        E.go(live=True)     тестова відправка на власні скриньки

    day=None -> сьогодні. Формат '2026-08-28'.
    """
    global TOKEN_DIR
    if not ensure_drive(dysk):
        storage_warning(dysk)
        return None

    TOKEN_DIR = dysk
    day = day or now().strftime("%Y-%m-%d")
    vyhid = os.path.join(dysk, "vyhid")
    baza = os.path.join(dysk, "leads.db")
    os.makedirs(vyhid, exist_ok=True)
    if not dysk.startswith("/content/drive"):
        print("! Стан зберігається НЕ на Диску — він зникне разом із сеансом.")
        print(f"! Наприкінці скачайте {baza}, інакше завтра листи підуть повторно.\n")

    if live:
        print("Перевіряю доступ до пошти…\n")
        if not setup_gmail(dysk):
            print("\nБез робочого доступу до пошти відправка неможлива.")
            print("Сухий прогін працює і без неї: E.go()")
            return None
        print()

    return run_colab(day=day, dry_run=not live, db_path=baza, out_dir=vyhid)


def reset_dry_run_sends(db_path: str) -> dict:
    """
    Ремонт баз, зіпсованих старим сухим прогоном.

    До цього виправлення сухий прогін позначав листи як надіслані
    (з ідентифікатором виду `dryrun-…`). Такі записи повертаємо в чергу,
    щоб листи справді пішли. Справжніх відправок це не чіпає: у них
    ідентифікатор від Gmail.
    """
    db = Db(db_path)
    audit = Audit(db, new_run_id())
    repo = Repository(db, audit)
    try:
        rows = db.q("SELECT outreach_id, company_id FROM outreach_events"
                    " WHERE gmail_message_id LIKE 'dryrun-%' AND status='SENT'")
        for r in rows:
            repo.set_outreach_status(
                r["outreach_id"], "QUEUED", sent_at=None,
                gmail_message_id=None, gmail_thread_id=None,
                provider_timestamp=None, error_code=None,
                message_id_header=build_message_id(uuid.uuid4().hex))
        return {"повернуто_в_чергу": len(rows)}
    finally:
        db.close()


def why_no_letters(db_path: str) -> None:
    """
    Відповідає на питання «чому листи не пішли» словами, а не таблицями.
    Нічого не змінює.
    """
    if not os.path.exists(db_path):
        print("Бази ще немає. Спершу зробіть сухий прогін: E.go()")
        return
    db = Db(db_path)
    try:
        def n(sql, args=()):
            row = db.one(sql, args)
            return int(row["n"]) if row else 0

        queued = n("SELECT COUNT(*) n FROM outreach_events WHERE status='QUEUED'")
        dry = n("SELECT COUNT(*) n FROM outreach_events"
                " WHERE gmail_message_id LIKE 'dryrun-%'")
        real = n("SELECT COUNT(*) n FROM outreach_events WHERE status='SENT'"
                 " AND gmail_message_id NOT LIKE 'dryrun-%'"
                 " AND gmail_message_id IS NOT NULL")
        failed = n("SELECT COUNT(*) n FROM outreach_events WHERE status='SEND_FAILED'")
        unknown = n("SELECT COUNT(*) n FROM outreach_events"
                    " WHERE status='DELIVERY_UNKNOWN'")
        manual = n("SELECT COUNT(*) n FROM leads WHERE status='MANUAL_REVIEW_REQUIRED'")
        suppressed = n("SELECT COUNT(*) n FROM leads WHERE status='SUPPRESSED'")
        leads = n("SELECT COUNT(*) n FROM leads")

        print("ЧОМУ ЛИСТИ НЕ ПІШЛИ")
        print("=" * 70)
        if leads == 0:
            print("  Лідів у базі немає. Зробіть прогін: E.go()")
            return
        if real:
            print(f"  {real} листів УЖЕ НАДІСЛАНО по-справжньому.")
            print("  Перевірте пошту — вони мали дійти.")
        if dry:
            print(f"  {dry} листів позначені як надіслані СУХИМ прогоном.")
            print("  Це дефект старої версії: сухий прогін спорожняв чергу,")
            print("  і реальна відправка потім не знаходила нічого.")
            print()
            print("  ЩО ЗРОБИТИ:")
            print(f"     E.reset_dry_run_sends('{db_path}')")
            print("     потім E.go(live=True)")
        if queued:
            print(f"  {queued} листів чекають у черзі.")
            print("  Щоб надіслати: E.go(live=True)")
        if failed:
            print(f"  {failed} не пішли через помилку — повтор буде автоматично.")
        if unknown:
            print(f"  {unknown} у стані «невідомо, чи пішли» — потрібна звірка.")
        if manual:
            print(f"  {manual} лідів на ручному перегляді (немає підстави, "
                  f"адреси або ЄДРПОУ).")
        if suppressed:
            print(f"  {suppressed} лідів у стоп-листі.")
        if not any((real, dry, queued, failed, unknown)):
            print("  Жодного звернення не сформовано.")
            print("  Подивіться причини: E.colab_state(<база>, <вихід>)")
    finally:
        db.close()


# ============================================================================
#  БЛОК 26. СЛУЖБОВА КАРТКА ПО ЗАКУПІВЛІ
# ----------------------------------------------------------------------------
#  На кожну закупівлю з відхиленням створюється тека з назвою UA-ID, а в ній
#  картка у Word. Це внутрішній документ для підготовки до розмови — клієнту
#  він не надсилається, і це написано першим рядком.
#
#  Правило 5 діє і тут: жодне число не подається як встановлене, якщо ми його
#  не встановили. Порожня клітинка чесніша за вигадане значення, тому поля,
#  яких немає в даних, лишаються під ручне заповнення і позначені як такі.
# ============================================================================
PROZORRO_TENDER_URL = "https://prozorro.gov.ua/tender/{ua_id}"

#: Поля, які людина заповнює сама. У картці вони позначені явно.
MANUAL_FIELDS = {
    "Перспектива оскарження",
    "Що пропонуємо",
    "Що показав аналіз",
    "З чого починати розмову",
}


def _docx_hyperlink(paragraph, url: str, text: str, bold: bool = True):
    """
    Гіперпосилання у python-docx. Штатного API немає, тому збираємо
    елемент w:hyperlink вручну — це стандартний спосіб.
    """
    from docx.oxml.ns import qn                                     # noqa: PLC0415
    from docx.oxml import OxmlElement                               # noqa: PLC0415
    from docx.shared import RGBColor                                # noqa: PLC0415

    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rPr.append(underline)
    if bold:
        rPr.append(OxmlElement("w:b"))
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)
    return link


NOT_ESTABLISHED = "НЕ ВСТАНОВЛЕНО"

#: Поля, яких у даних немає в принципі — їх заповнює людина.
MANUAL_HINT = "заповнити вручну"


def _fee_rows(lot_amount: Optional[float], bid_amount: Optional[float],
              legacy: Optional[Any]) -> dict:
    """
    Дві РІЗНІ плати з двома РІЗНИМИ базами. Плутати їх не можна.

    * плата до АМКУ за скаргу      — % від очікуваної вартості ЛОТУ;
    * плата за подання пропозиції  — % від ЦІНИ ПРОПОЗИЦІЇ учасника.

    Показуємо не тільки суму, а й підставу: ставку і від чого рахували.
    Неперевірене число в юридичному документі гірше за порожню клітинку,
    тому якщо бази немає — пишемо НЕ ВСТАНОВЛЕНО, а не рахуємо від чогось
    іншого (правило 5).
    """
    money = (getattr(legacy, "money", None)
             or (lambda x: f"{float(x):,.0f}".replace(",", " ")))
    out = {"плата": NOT_ESTABLISHED + " — немає вартості лоту",
           "втрачено": NOT_ESTABLISHED + " — немає ціни пропозиції"}
    if legacy is None:
        return {k: NOT_ESTABLISHED + " — модуль ставок недоступний" for k in out}

    if lot_amount and hasattr(legacy, "fee_amcu"):
        try:
            suma, stavka = legacy.fee_amcu(lot_amount, "рішення замовника")
            out["плата"] = (
                f"{money(suma)} грн  (ставка {stavka} від вартості ЛОТУ "
                f"{money(lot_amount)} грн, ПКМУ № 292 — звірити чинність)")
        except Exception:                                           # noqa: BLE001
            out["плата"] = NOT_ESTABLISHED + " — помилка розрахунку"

    if bid_amount and hasattr(legacy, "plata_za_podannya"):
        try:
            povna, bezpov = legacy.plata_za_podannya(bid_amount)
            # Роздільники тисяч міняємо ТІЛЬКИ в числах: раніше заміна
            # йшла по всьому рядку і з'їдала кому після «ПКМУ № 565».
            bez = f"{bezpov:,.2f}".replace(",", " ")
            pov = f"{povna:,.2f}".replace(",", " ")
            out["втрачено"] = (
                f"{bez} грн безповоротно (з {pov} грн; "
                f"ПКМУ № 565, дві третини повертає майданчик) — "
                f"від ціни пропозиції {money(bid_amount)} грн")
        except Exception:                                           # noqa: BLE001
            out["втрачено"] = NOT_ESTABLISHED + " — помилка розрахунку"
    return out


def _deadline_row(iso: Optional[str], legacy: Optional[Any]) -> str:
    """
    Строк на скаргу словами + скільки лишилось. Порожній рядок означав би,
    що строку немає; насправді це означає, що ми його не встановили.
    """
    if not iso:
        return NOT_ESTABLISHED + " — перевірити період оскарження в Prozorro"
    try:
        end = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return f"{iso} (не вдалося розібрати дату)"
    end_k = end.astimezone(KYIV)
    words = ""
    if legacy is not None and hasattr(legacy, "ua_date"):
        try:
            words = legacy.ua_date(end_k) + " · "
        except Exception:                                           # noqa: BLE001
            words = ""
    left = (end_k - now()).total_seconds() / 3600.0
    tail = (f"лишилось {left:.0f} год ({left / 24:.1f} дн.)" if left > 0
            else f"МИНУВ {abs(left):.0f} год тому")
    return f"{words}до {end_k:%d.%m.%Y %H:%M} за Києвом\n{tail}"


def _amcu_block(row) -> str:
    """Розрахунок плати з базою, ставкою і правовою підставою (§29)."""
    def val(name):
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return None

    status = val("amcu_fee_status") or FEE_STATUS_NO_BASE
    rule = AMCU_FEE_RULE
    if status != FEE_STATUS_CALCULATED:
        reason = {"RELEVANT_LOT_NOT_VERIFIED":
                  "релевантний лот не встановлено — суму всього тендера "
                  "як базу брати не можна",
                  "NO_EXPECTED_VALUE":
                  "очікуваної вартості немає в даних Prozorro"}.get(
                      val("amcu_fee_reason") or "", val("amcu_fee_reason") or "")
        return (f"{NOT_ESTABLISHED}\nстатус: {status}"
                + (f"\nпричина: {reason}" if reason else "")
                + f"\nправило: {rule['legal_source']}")
    base_type = {FEE_BASE_LOT: "очікувана вартість лота",
                 FEE_BASE_TENDER: "очікувана вартість закупівлі"}.get(
                     val("amcu_fee_base_type") or "", val("amcu_fee_base_type"))
    lines = [
        f"{money_uah(val('amcu_fee_calculated'))}",
        f"тип: {rule['complaint_type']}",
        f"база: {base_type}"
        + (f" № {val('amcu_fee_source_object_id')}"
           if val("amcu_fee_source_object_id") else ""),
        f"вартість бази: {money_uah(val('amcu_fee_base_amount'))}",
        f"ставка: {Decimal(val('amcu_fee_rate') or '0') * 100:g}%",
        f"розрахунок: {money_uah(val('amcu_fee_base_amount'))}"
        f" × {val('amcu_fee_rate')} = {money_uah(val('amcu_fee_raw'))}",
    ]
    if val("amcu_fee_min_applied"):
        lines.append(f"застосовано мінімум {rule['minimum_uah']} грн")
    if val("amcu_fee_max_applied"):
        lines.append(f"застосовано максимум {rule['maximum_uah']} грн")
    lines += [f"округлення: {val('amcu_fee_rounding_applied')}",
              f"правова база: {rule['legal_source']}",
              f"статус: {status} · правило {val('amcu_fee_rule_id')}",
              "це розрахунок TenderWin для рішення, а не остаточна сума до "
              "сплати: її обчислює електронна система при поданні скарги"]
    return "\n".join(lines)


def _checklist(claim: "Claim") -> list:
    """
    §16 словника — що людина перевіряє САМА перед розмовою.
    Це питання, а не висновки: скрипт не має права відповідати на них.
    """
    doc = claim.documents[0] if claim.documents else "цей документ"
    items = [f"чи вимагала ТД саме «{doc}» — і в якому пункті",
             "чи є цей документ в іншому файлі пропозиції",
             "чи стосується вимога предмета закупівлі"]
    codes = {c for c, _, _ in claim.codes}
    if "R04" in codes or "X06_CORRECTABILITY" in claim.cross:
        items.append("чи було повідомлення про усунення і чи названо в ньому "
                     "конкретний документ")
        items.append("чи підлягала ця невідповідність усуненню взагалі")
    if "R03" in codes:
        items.append("звірити числа й одиниці виміру вручну (Python рахує, "
                     "але порівнює з ТД людина)")
    if "R06" in codes:
        items.append("звірити суму, строк дії та форму гарантії з вимогою ТД")
    if "R01" in codes:
        items.append("чи підтверджує поданий документ саме те, що вимагала ТД")
    if not claim.citations:
        items.append("рішення не називає пункт ТД — перевірити, чи є така "
                     "вимога в документації взагалі")
    if "X03_EQUAL_TREATMENT" in claim.cross:
        items.append("чи має переможець ту саму ваду")
    items.append("чи не змінилася норма від дати закупівлі")
    return [f"• {x}" for x in items]


def _match_docs(claim: "Claim", documents: list) -> list:
    """
    Файли пропозиції, назви яких відповідають документам із ЦІЄЇ претензії.

    Зіставляємо за словником DOC_VOCAB, а не за словами навмання: назва файла
    в Prozorro пишеться як завгодно («Довідка МТБ.pdf», «дов_прац.pdf»), і
    збіг за окремим словом дав би випадкові файли.
    """
    if not claim.documents:
        return []
    out = []
    for it in documents or []:
        title = str(it.get("назва") or "")
        if not title:
            continue
        for docname in claim.documents:
            rx = DOC_VOCAB.get(docname)
            if rx and re.search(rx, title, re.I):
                out.append(it)
                break
    return out


def _shared_defect(a: Optional[str], b: Optional[str]) -> bool:
    """Чи є в двох формулюваннях спільний документ і спільний дефект."""
    if not a or not b:
        return False
    da = {k for k, rx in DOC_VOCAB.items() if re.search(rx, a, re.I)}
    db_ = {k for k, rx in DOC_VOCAB.items() if re.search(rx, b, re.I)}
    fa = {k for k, rx in DEFECT_VOCAB.items() if re.search(rx, a, re.I)}
    fb = {k for k, rx in DEFECT_VOCAB.items() if re.search(rx, b, re.I)}
    return bool(da & db_) and bool(fa & fb)


def _same_day_rejections(rows: list, r) -> str:
    """
    Скільки відхилень у ЦЬОГО учасника того самого дня. Саме цей рядок
    пояснює, чому лист один, а карток кілька (вимога про дублі за ЄДРПОУ).
    """
    day = (r["rejection_date"] or "")[:10]
    same = [x["ua_id"] for x in rows
            if x["edrpou_norm"] == r["edrpou_norm"]
            and (x["rejection_date"] or "")[:10] == day]
    uniq = sorted(set(same))
    if len(uniq) <= 1:
        return "1 — тільки ця закупівля"
    return (f"{len(uniq)} — {'; '.join(uniq)}\n"
            f"лист надіслано ОДИН на ЄДРПОУ {r['edrpou_norm']}")


def build_case_cards(db_path: str, root_dir: str,
                     legacy: Optional[Any] = None,
                     only_new: bool = True,
                     case_dir: Optional[str] = None,
                     rejection_ids=None) -> dict:
    """
    Службова картка лягає в АРХІВ справи, поруч із її документами:
    <root_dir>/spravy/_arhiv/<UA-ID>/00_KARTKA_<ЄДРПОУ>.docx

    Картка одна на справу і лежить ТАМ, ДЕ ДОКУМЕНТИ. Копії в теках прогонів
    не робимо свідомо: людина вписує в картку свої висновки, а дві копії
    означають, що одна з них рано чи пізно затре іншу (правило 36).

    case_dir — тека цього прогону. У неї кладеться ПОКАЖЧИК: перелік справ
    прогону з посиланнями на їхні картки і теки.

    rejection_ids — опрацювати лише ці відхилення. None означає «усі справи
    в базі» і потрібен для ручної перебудови, а не для щоденного прогону.

    only_new=True — не перезаписувати наявні картки (правило 36).
    """
    try:
        import docx                                                 # noqa: PLC0415
        from docx.shared import Pt, Cm                              # noqa: PLC0415
        from docx.enum.text import WD_ALIGN_PARAGRAPH               # noqa: PLC0415
    except ImportError as exc:
        raise ERR["TRANSPORT_UNAVAILABLE"](
            "немає python-docx: pip install python-docx") from exc

    if legacy is None:
        try:
            legacy = importlib.import_module(LEGACY_MODULE)
        except Exception:                                           # noqa: BLE001
            legacy = None

    # Наявні справи зі старою розкладкою піднімаємо ДО побудови: інакше
    # картка з нотатками лишиться під старим імʼям, а поруч зʼявиться друга.
    migrate_case_layout(root_dir)

    db = Db(db_path)
    os.makedirs(os.path.join(root_dir, "spravy", ARCHIVE_DIR), exist_ok=True)
    stats = {"створено": 0, "пропущено": 0, "закупівель": 0, "нарядів": 0,
             "суперечності": 0, "заблоковано": 0}
    pokazhchyk: list = []            # (UA-ID, ЄДРПОУ, компанія, шлях до картки)
    where, args = _rejection_filter(rejection_ids)
    try:
        rows = db.q(
            "SELECT t.ua_id, t.tender_title, t.buyer_name, t.buyer_edrpou,"
            "       r.rejection_id, r.rejection_date, r.rejection_reason_raw,"
            "       r.rejection_reason_short, r.reason_code, r.lot_amount,"
            "       r.bid_amount, r.complaint_deadline, r.stage, r.lot_id,"
            "       r.tender_value_amount, r.lot_value_amount, r.lots_total,"
            "       r.lot_resolved, r.amcu_fee_status, r.amcu_fee_base_type,"
            "       r.amcu_fee_base_amount, r.amcu_fee_rate, r.amcu_fee_raw,"
            "       r.amcu_fee_min_applied, r.amcu_fee_max_applied,"
            "       r.amcu_fee_rounding_applied, r.amcu_fee_calculated,"
            "       r.amcu_fee_rule_id, r.amcu_fee_reason,"
            "       o.mode, o.template_id, o.template_version,"
            "       o.gmail_message_id, o.subject_rendered AS subj,"
            "       r.winner_amount, r.winner_name, r.protocol_url,"
            "       r.protocol_title, r.protocol_published, r.notice_24h,"
            "       r.notice_24h_url, r.notice_24h_title,"
            "       r.td_documents, r.bid_documents, r.cpv,"
            "       c.company_name, c.edrpou_norm,"
            "       ct.contact_name_raw, ct.contact_phone, ct.contact_region,"
            "       o.sent_at, o.variant, o.delivery_email_actual,"
            "       o.contact_email_original, o.status,"
            "       o.subject_rendered, o.body_rendered"
            "  FROM rejections r"
            "  JOIN tenders   t  ON t.tender_id = r.tender_id"
            "  JOIN companies c  ON c.company_id = r.company_id"
            "  LEFT JOIN leads l ON l.rejection_id = r.rejection_id"
            "  LEFT JOIN contacts ct ON ct.contact_id = l.contact_id"
            "  LEFT JOIN outreach_events o ON o.lead_id = l.lead_id"
            + where +
            " ORDER BY t.ua_id, c.company_name", args)

        # Скільки відхилень у цій же закупівлі — потрібно для рядка картки
        per_tender: dict[str, list[str]] = {}
        for r in rows:
            per_tender.setdefault(r["ua_id"], []).append(r["company_name"] or "?")

        seen_dirs = set()
        for r in rows:
            ua = r["ua_id"]
            folder = case_archive(root_dir, ua)
            os.makedirs(folder, exist_ok=True)
            if ua not in seen_dirs:
                seen_dirs.add(ua)
                stats["закупівель"] += 1

            edrpou = r["edrpou_norm"] or "bez-kodu"
            path = os.path.join(folder, f"{CARD_PREFIX}{edrpou}.docx")
            pokazhchyk.append((ua, edrpou, r["company_name"] or "", path))
            if only_new and os.path.exists(path):
                stats["пропущено"] += 1
                continue

            # §42-44: суперечлива картка не формується мовчки.
            problems = validate_service_card_consistency(r)
            if problems:
                stats["суперечності"] = stats.get("суперечності", 0) + 1
                critical = [p for p in problems if p["критична"]]
                print(f"   ! {CONTRADICTION} у {ua} / {r['edrpou_norm']}:")
                for prob in problems:
                    print(f"     {'КРИТИЧНО ' if prob['критична'] else ''}"
                          f"{prob['код']}: {prob['що']}")
                if critical or not ALLOW_CARD_WITH_CONTRADICTION:
                    print("     картку не сформовано — спершу усунути причину")
                    stats["заблоковано"] = stats.get("заблоковано", 0) + 1
                    continue

            fees = _fee_rows(r["lot_amount"], r["bid_amount"], legacy)
            money = (getattr(legacy, "money", None)
                     or (lambda x: f"{float(x):,.0f}".replace(",", " ")))
            others = [c for c in per_tender.get(ua, []) if c != r["company_name"]]
            # X03 (рівне ставлення): чи відхилили ще когось із ТІЄЮ Ж підставою.
            # Порівнюємо коди, а не тексти: формулювання в кожного своє.
            my_codes = set(re.findall(r"R\d\d", str(r["reason_code"] or "")))
            same_reason = []
            for other in rows:
                if other["ua_id"] != ua or other["company_name"] == r["company_name"]:
                    continue
                if my_codes and my_codes & set(re.findall(
                        r"R\d\d", str(other["reason_code"] or ""))):
                    same_reason.append(other["company_name"] or "?")
                elif not my_codes and _shared_defect(
                        r["rejection_reason_raw"], other["rejection_reason_raw"]):
                    same_reason.append(other["company_name"] or "?")
            same_reason = sorted(set(same_reason))

            d = docx.Document()
            for sec in d.sections:
                sec.left_margin = sec.right_margin = Cm(1.6)
                sec.top_margin = sec.bottom_margin = Cm(1.4)

            h = d.add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = h.add_run("СЛУЖБОВА КАРТКА — КЛІЄНТУ НЕ НАДСИЛАТИ")
            run.bold = True
            run.font.size = Pt(13)

            link_p = d.add_paragraph()
            link_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _docx_hyperlink(link_p, PROZORRO_TENDER_URL.format(ua_id=ua), ua)

            sub = d.add_paragraph()
            sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
            srun = sub.add_run(f"Картку сформовано {now():%d.%m.%Y %H:%M}")
            srun.font.size = Pt(9)

            # ------- РОЗБІР ПІДСТАВ ------------------------------------
            claims = analyze_protocol(
                r["rejection_reason_raw"] or "", cpv=r["cpv"] or "",
                legacy=legacy,
                facts={"same_reason_others": bool(same_reason)})
            order = build_search_order(
                claims, cpv=r["cpv"] or "", title=r["tender_title"] or "",
                buyer_edrpou=r["buyer_edrpou"] or "")
            pcl = procurement_class(r["cpv"] or "", r["tender_title"] or "")

            # різниця з переможцем — найсильніше число для розмови
            gap = NOT_ESTABLISHED
            if r["bid_amount"] and r["winner_amount"]:
                delta = float(r["winner_amount"]) - float(r["bid_amount"])
                who = "ДЕШЕВШЕ за переможця" if delta > 0 else "дорожче за переможця"
                gap = (f"{money(abs(delta))} грн {who}"
                       f" ({money(r['winner_amount'])} грн у "
                       f"{r['winner_name'] or 'переможця'})")
            elif r["winner_amount"]:
                gap = (f"переможець {money(r['winner_amount'])} грн; "
                       f"ціни клієнта немає — {NOT_ESTABLISHED}")

            notice = {"YES": "так — документ про усунення невідповідностей знайдено",
                      "MENTIONED_IN_DECISION": "згадано в рішенні, окремого "
                                               "документа не знайдено",
                      "NOT_ESTABLISHED": NOT_ESTABLISHED + " — перевірити в Prozorro"
                      }.get(r["notice_24h"] or "NOT_ESTABLISHED",
                            NOT_ESTABLISHED)

            no_cite = [c.claim_id for c in claims if not c.citations]
            motiv = ("усі підстави мають посилання на норму або пункт"
                     if not no_cite else
                     f"БЕЗ посилання на конкретний пункт: {', '.join(no_cite)}"
                     f" — аргумент про мотивованість рішення")

            # Локальні копії документів, якщо їх уже завантажено поруч.
            local = _local_files(folder)

            def doc_links(items, limit=12):
                """
                [(підпис, ціль, локальний?, url_prozorro)].

                Якщо файл лежить поруч — основне посилання веде на нього
                (відкривається миттєво, зберігати нічого не треба). Поруч
                завжди лишається посилання на Prozorro: у переглядачі
                Google Диска локальний шлях не спрацює, а першоджерело
                має бути доступне завжди (правило 6).
                """
                out = []
                # Дві редакції того самого документа мають відрізнятись у
                # картці, а не виглядати як два однакові рядки.
                titles = [str(it.get("назва") or "").lower() for it in items]
                for it in items:
                    if len(out) >= limit:
                        break
                    url = it.get("url") or ""
                    label = it.get("назва") or "документ"
                    if label.lower().endswith(".p7s"):
                        continue          # електронний підпис, людині не треба
                    date = str(it.get("опубліковано") or it.get("змінено") or "")
                    if titles.count(label.lower()) > 1 and date:
                        label = f"{label} (від {date[:10]})"
                    fname, pdf = local.get(url, ("", ""))
                    if fname:
                        out.append((label, fname, True, url, pdf))
                    elif url:
                        out.append((label, url, False, url, ""))
                return out

            try:
                td_list = json.loads(r["td_documents"] or "[]")
            except (ValueError, TypeError):
                td_list = []
            try:
                bid_list = json.loads(r["bid_documents"] or "[]")
            except (ValueError, TypeError):
                bid_list = []

            # Документи учасника, НАЗВАНІ в рішенні: показуємо їх окремо і
            # першими — саме їх замовник вважає невідповідними.
            per_claim_docs = {cl.claim_id: _match_docs(cl, bid_list)
                              for cl in claims}
            named_urls = {it.get("url") for docs in per_claim_docs.values()
                          for it in docs}
            named = [it for it in bid_list if it.get("url") in named_urls]
            rest = [it for it in bid_list if it.get("url") not in named_urls]

            data = [
                ("Закупівля", ua),
                ("Предмет", r["tender_title"] or ""),
                ("Класифікація предмета", pcl),
                ("Замовник",
                 (r["buyer_name"] or "") + (f" (ЄДРПОУ {r['buyer_edrpou']})"
                                            if r["buyer_edrpou"] else "")),
                ("Стадія", {"awards": "розгляд пропозиції (awards)",
                            "qualifications": "прекваліфікація"}.get(
                                r["stage"] or "", r["stage"] or "")),
                ("Лист відправлено учаснику",
                 (r["sent_at"] or "").replace("T", " ")[:16] or "ще не надіслано"),
                ("Email учасника з джерела", r["contact_email_original"] or ""),
                ("Режим доставки", r["mode"] or NOT_ESTABLISHED),
                ("Кому фактично доставлено", delivery_display(r)[0]),
                ("Gmail Message ID", r["gmail_message_id"] or
                 ("—" if r["status"] in (None, "", "QUEUED") else NOT_ESTABLISHED)),
                ("Шаблон листа",
                 f"{r['template_id']} {r['template_version']}"
                 if r["template_id"] else "лист не формувався"),
                ("УЧАСНИК", r["company_name"] or ""),
                ("ЄДРПОУ учасника", r["edrpou_norm"] or ""),
                ("Контактна особа", r["contact_name_raw"] or ""),
                ("Телефон", r["contact_phone"] or ""),
                ("E-mail", r["contact_email_original"] or ""),
                ("Регіон", r["contact_region"] or ""),
                ("Дата відхилення",
                 (r["rejection_date"] or "").replace("T", " ")[:16]),
                ("ПРОТОКОЛ / рішення", ("__LINKS__", doc_links(
                    [{"назва": r["protocol_title"] or "відкрити протокол",
                      "url": r["protocol_url"]}]) if r["protocol_url"] else [],
                    NOT_ESTABLISHED + " — документа рішення в Prozorro не знайдено")),
                ("ВИМОГА про усунення за 24 год",
                 ("__LINKS__",
                  doc_links([{"назва": r["notice_24h_title"] or "вимога 24 год",
                              "url": r["notice_24h_url"]}])
                  if r["notice_24h_url"] else [], notice)),
                ("ДОКУМЕНТИ УЧАСНИКА, названі в рішенні",
                 ("__LINKS__", doc_links(named),
                  NOT_ESTABLISHED + " — у пропозиції не знайдено файлів із "
                  "такими назвами; перевірити вручну")),
                ("Решта документів пропозиції",
                 ("__LINKS__", doc_links(rest, limit=8),
                  "немає або приховані до кваліфікації")),
                ("ДОКУМЕНТИ ТД — де шукати вимогу",
                 ("__LINKS__", doc_links(td_list),
                  NOT_ESTABLISHED + " — документи ТД не завантажені")),
                ("Мотивованість рішення", motiv),
                ("Строк на скаргу", _deadline_row(r["complaint_deadline"], legacy)),
                # ---- ПЛАТА ДО АМКУ (§29, §48) ----------------------------
                ("Очікувана вартість закупівлі",
                 money_uah(r["tender_value_amount"])),
                ("Очікувана вартість релевантного лота",
                 money_uah(r["lot_value_amount"]) if r["lot_value_amount"]
                 else (NOT_ESTABLISHED + " — закупівля без лотів"
                       if r["lot_resolved"] == "NO_LOTS"
                       else NOT_ESTABLISHED + " — релевантний лот не визначено")),
                ("Лот", r["lot_id"] or ("без лотів"
                                        if r["lot_resolved"] == "NO_LOTS"
                                        else NOT_ESTABLISHED)),
                ("РОЗРАХУНКОВА ПЛАТА за подання скарги до АМКУ",
                 _amcu_block(r)),
                ("Ціна пропозиції учасника", money_uah(r["bid_amount"])),
                ("Різниця з переможцем", gap),
                ("Уже втрачено на платі за подання пропозиції", fees["втрачено"]),
                ("Підстав у рішенні", f"{len(claims)} — "
                 + ", ".join(f"{c.claim_id} {c.codes[0][0]}" for c in claims)),
                ("Варіант листа", r["variant"] or "лист не формувався"),
                ("Статус звернення", r["status"] or "лист не формувався"),
                ("Відхилень цього учасника за день",
                 _same_day_rejections(rows, r)),
                ("Інші відхилені в цій закупівлі",
                 f"{len(others)} — {'; '.join(others[:6])}" if others else "немає"),
                ("Та сама підстава в інших учасників",
                 f"{len(same_reason)} — {'; '.join(same_reason[:4])}"
                 if same_reason else "не виявлено"),
            ]

            # ------- ПІДСТАВИ: кожна окремо, рядками ТІЄЇ Ж таблиці --------
            # Правило 32: висновок неможливий, доки не розібрано ВСІ підстави.
            if not claims:
                data.append(("ПІДСТАВИ ВІДХИЛЕННЯ",
                             NOT_ESTABLISHED + " — текст рішення порожній, "
                             "відкрити протокол за посиланням вище"))
            for cl in claims:
                head = (f"{cl.claim_id} · "
                        + " · ".join(f"{c}/{s_}" for c, s_, _ in cl.codes))
                data += [
                    (f"━━ {head}", "«" + cl.quote + "»"),
                    (f"{cl.claim_id} · теми (усі, не одна)",
                     "; ".join(f"{c} {name}" for c, _, name in cl.codes)),
                    (f"{cl.claim_id} · норми в цій підставі",
                     ", ".join(x["як"] for x in cl.citations)
                     or NOT_ESTABLISHED + " — посилання на пункт відсутнє"),
                    (f"{cl.claim_id} · документ, про який мова",
                     ", ".join(cl.documents) or NOT_ESTABLISHED),
                    # Посилання саме на ті файли пропозиції, які замовник
                    # у ЦІЙ підставі вважає невідповідними.
                    (f"{cl.claim_id} · ФАЙЛ УЧАСНИКА",
                     ("__LINKS__", doc_links(per_claim_docs[cl.claim_id], limit=6),
                      NOT_ESTABLISHED + " — файла з такою назвою в пропозиції "
                      "не знайдено; шукати у списку вище")),
                    # Куди дивитись у ТД. Який саме файл містить цей пункт —
                    # видно лише всередині документа, тому називаємо пункт,
                    # а не вгадуємо файл.
                    # Закон і ПКМУ в тендерній документації не шукають —
                    # лишаємо тільки посилання на структуру самої ТД.
                    (f"{cl.claim_id} · де шукати вимогу в ТД",
                     (", ".join(x["як"] for x in cl.citations
                                if x["вид"] in TD_CITATION_KINDS)
                      + " — у документах ТД вище")
                     if any(x["вид"] in TD_CITATION_KINDS for x in cl.citations)
                     else NOT_ESTABLISHED + " — рішення не називає пункт ТД, "
                          "лише норму закону"),
                    (f"{cl.claim_id} · дефект за словами замовника",
                     ", ".join(cl.defects) or NOT_ESTABLISHED),
                    (f"{cl.claim_id} · наскрізні теми",
                     ", ".join(cl.cross) or "—"),
                    (f"{cl.claim_id} · ЩО ПЕРЕВІРИТИ ПЕРЕД РОЗМОВОЮ",
                     "\n".join(_checklist(cl))),
                ]
                if cl.notes:
                    data.append((f"{cl.claim_id} · позначки скрипта",
                                 "\n".join(cl.notes)))

            data += [
                ("Перспектива оскарження", ""),
                ("Що пропонуємо", ""),
                ("Що показав аналіз", ""),
                ("З чого починати розмову", ""),
            ]

            # Ключові слова йдуть у ДРУГУ таблицю. Ті, що не потрапили в
            # наряд, добираємо зі словника, щоб вийшло не менше 22.
            in_order = {_norm_q(x.query_text) for x in order}
            extra_keywords = [k for k in
                              (line.split(". ", 1)[-1]
                               for line in keywords_line(claims, order).splitlines())
                              if _norm_q(k) not in in_order]

            tbl = d.add_table(rows=0, cols=2)
            tbl.style = "Table Grid"
            for name, value in data:
                cells = tbl.add_row().cells
                cells[0].text = ""
                cells[0].paragraphs[0].add_run(name).bold = True
                if isinstance(value, tuple) and value and value[0] == "__LINKS__":
                    links, empty_text = value[1], value[2]
                    if links:
                        for k, (label, target, is_local, src, pdf) in \
                                enumerate(links):
                            para = (cells[1].paragraphs[0] if k == 0
                                    else cells[1].add_paragraph())
                            _docx_hyperlink(para, target, label[:95], bold=False)
                            if pdf:
                                # PDF-копія для NotebookLM: оригінал він не
                                # прочитає, тому посилання має бути видно.
                                para.add_run("  ").font.size = Pt(7)
                                _docx_hyperlink(para, pdf, "PDF-копія",
                                                bold=False)
                            if is_local:
                                # Файл лежить поруч. Друге посилання —
                                # на першоджерело: у переглядачі Google Диска
                                # локальний шлях не відкриється.
                                para.add_run("  ").font.size = Pt(7)
                                _docx_hyperlink(para, src, "Prozorro", bold=False)
                            else:
                                mark = para.add_run("  (Prozorro)")
                                mark.font.size = Pt(7)
                                mark.italic = True
                    else:
                        run = cells[1].paragraphs[0].add_run(empty_text)
                        run.italic = True
                    for para in cells[1].paragraphs:
                        for run in para.runs:
                            if not run.font.size:
                                run.font.size = Pt(9)
                    cells[0].width = Cm(6.2)
                    cells[1].width = Cm(11.6)
                    continue
                text = str(value).strip()
                if not text:
                    # Порожня клітинка читається як «нічого немає».
                    # Насправді це або ручне поле, або невстановлений факт.
                    run = cells[1].paragraphs[0].add_run(
                        MANUAL_HINT if name in MANUAL_FIELDS else NOT_ESTABLISHED)
                    run.italic = True
                else:
                    lines = text.split("\n")
                    cells[1].text = lines[0]
                    for extra in lines[1:]:
                        cells[1].add_paragraph(extra)
                    if name.startswith("━━"):
                        for para in cells[1].paragraphs:
                            for run in para.runs:
                                run.italic = True
                # Ширини задаємо на КОЖНІЙ клітинці: інакше Word і Google Docs
                # показують таблицю по-різному.
                cells[0].width = Cm(6.2)
                cells[1].width = Cm(11.6)

            # ТАБЛИЦЯ 2 (ключові слова Clarity) прибрана на вимогу власника
            # продукту 2026-09-02. Генератор лишається в коді: щоб повернути
            # її, достатньо CLARITY_KEYWORDS_IN_CARD = True.
            if CLARITY_KEYWORDS_IN_CARD:
                _clarity_keywords_table(d, order, extra_keywords, Pt, Cm,
                                        WD_ALIGN_PARAGRAPH)

            # Текст листа — у картці, щоб перед дзвінком було видно,
            # що саме людина прочитала. Це та сама копія, що в базі.
            d.add_paragraph()
            sep = d.add_paragraph()
            sep.alignment = WD_ALIGN_PARAGRAPH.CENTER
            srun2 = sep.add_run("── ТЕКСТ НАДІСЛАНОГО ЛИСТА ──")
            srun2.bold = True
            srun2.font.size = Pt(10)
            if r["body_rendered"]:
                subj = d.add_paragraph()
                subj.add_run("Тема: ").bold = True
                subj.add_run(r["subject_rendered"] or "")
                for para in str(r["body_rendered"]).split("\n"):
                    bp = d.add_paragraph(para)
                    bp.paragraph_format.space_after = Pt(0)
            else:
                nolet = d.add_paragraph()
                nrun = nolet.add_run(
                    "Лист цьому учаснику не формувався. Найчастіша причина — "
                    "перший дотик уже був за цим ЄДРПОУ (одна компанія — "
                    "один лист).")
                nrun.italic = True

            d.add_paragraph()
            foot = d.add_paragraph()
            frun = foot.add_run(
                "Плата до АМКУ рахується від вартості ЛОТУ, плата за подання "
                "пропозиції — від ЦІНИ ПРОПОЗИЦІЇ. Ставки взяті з коду "
                "(ПКМУ № 292, ПКМУ № 565): перед розмовою з клієнтом звірити "
                "їх чинність і строки.")
            frun.italic = True
            frun.font.size = Pt(8)

            d.save(path)
            stats["створено"] += 1

            # Окремий файл запитів пишемо лише разом із таблицею Clarity.
            if CLARITY_KEYWORDS_IN_CARD:
                try:
                    with open(os.path.join(folder,
                                           f"{QUERIES_PREFIX}{edrpou}.md"),
                              "w", encoding="utf-8") as fh:
                        fh.write(search_order_markdown(order, claims, ua))
                    stats["нарядів"] = stats.get("нарядів", 0) + 1
                except OSError:
                    pass                   # картка важливіша за допоміжний файл

        if case_dir and pokazhchyk:
            stats["покажчик"] = write_run_index(case_dir, pokazhchyk)
        return stats
    finally:
        db.close()


def write_run_index(case_dir: str, cases) -> str:
    """
    Покажчик прогону: що знайшов цей запуск і де воно лежить.

    Тека за датою більше не тримає копій документів — вона тримає ЦЕЙ файл.
    Кожен рядок — гіперпосилання просто на картку справи в архіві.
    """
    try:
        import docx                                                 # noqa: PLC0415
        from docx.shared import Pt                                  # noqa: PLC0415
        from docx.enum.text import WD_ALIGN_PARAGRAPH               # noqa: PLC0415
    except ImportError:
        return ""
    os.makedirs(case_dir, exist_ok=True)
    path = os.path.join(case_dir, RUN_INDEX_NAME)
    d = docx.Document()
    head = d.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = head.add_run("СПРАВИ ЦЬОГО ПРОГОНУ")
    run.bold = True
    run.font.size = Pt(15)
    sub = d.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note = sub.add_run(
        f"сформовано {now().strftime('%d.%m.%Y %H:%M')} · "
        f"закупівель: {len({c[0] for c in cases})}")
    note.font.size = Pt(9)

    tbl = d.add_table(rows=0, cols=3)
    tbl.style = "Table Grid"
    hdr = tbl.add_row().cells
    for cell, name in zip(hdr, ("Закупівля", "Учасник", "Картка і документи")):
        cell.paragraphs[0].add_run(name).bold = True
    for ua, edrpou, company, card_path in cases:
        cells = tbl.add_row().cells
        _docx_hyperlink(cells[0].paragraphs[0],
                        PROZORRO_TENDER_URL.format(ua_id=ua), ua, bold=False)
        cells[1].text = f"{company}\nЄДРПОУ {edrpou}"
        # Відносний шлях від теки прогону до картки в архіві: так посилання
        # переживає перенесення всієї теки TENDERWIN в інше місце.
        try:
            rel = os.path.relpath(card_path, case_dir)
        except ValueError:
            rel = card_path
        _docx_hyperlink(cells[2].paragraphs[0], rel,
                        "відкрити службову картку", bold=False)
        para = cells[2].add_paragraph()
        _docx_hyperlink(para, os.path.dirname(rel), "тека з документами",
                        bold=False)
    foot = d.add_paragraph()
    tail = foot.add_run(
        "Документи кожної справи лежать в архіві і не дублюються по теках "
        "прогонів. Якщо ту саму закупівлю чіпав інший прогін — це та сама "
        "тека, з тими самими файлами і тими самими Вашими нотатками.")
    tail.italic = True
    tail.font.size = Pt(8)
    try:
        d.save(path)
    except OSError:
        return ""
    return path


# ============================================================================
#  БЛОК 27. СЛОВНИК ПОШУКУ ПРАКТИКИ (Clarity APP)
# ----------------------------------------------------------------------------
#  Джерело: «TenderWin — Повний словник і технічне завдання для формування
#  пошукових запитів у Clarity APP», версія 1.0 від 30.08.2026.
#
#  Це ДАНІ, не логіка. Логіка — у блоці 28. Розділено навмисно: словник
#  поповнюється після кожної реальної справи, і для цього не треба розуміти код.
#
#  §2 словника: Clarity НЕ автоматизується. Тут немає і не може бути жодного
#  мережевого виклику — генератор лише готує запити для ручного пошуку.
# ============================================================================

#: §7.4 — шаблонні звороти, які треба вирізати з цитати перед пошуком.
CLARITY_NOISE_PHRASES = [
    "відповідно до вимог тендерної документації",
    "відповідно до пункту 44 особливостей",
    "на підставі викладеного",
    "тендерна пропозиція учасника",
    "уповноваженою особою замовника",
    "прийнято рішення про відхилення",
    "не відповідає вимогам тендерної документації",
    "згідно з чинним законодавством",
    "учасник процедури закупівлі",
    "керуючись пунктом",
    "розглянувши тендерну пропозицію",
]

#: §7.4 — окремі слова-шум. Вирізаються ТІЛЬКИ якщо поруч немає змістовної
#: назви документа: «додатки до аналогічного договору» лишається цілим.
CLARITY_NOISE_TOKENS = {
    "замовник", "учасник", "пропозиція", "тендерний", "закупівля",
    "відповідно", "згідно", "вимога", "документація", "рішення",
    "протокол", "особливості", "закон",
}
CLARITY_STRUCT_TOKENS = {"пункт", "розділ", "додаток"}

#: §8.1 — формули пошуку практики НА КОРИСТЬ УЧАСНИКА.
PARTICIPANT_FORMULAS = [
    "замовник не довів та документально не підтвердив",
    "неправомірно відхилена з наведеної підстави",
    "пропозиція скаржника була неправомірно відхилена",
    "зобов'язати замовника скасувати рішення про відхилення",
    "вимога тендерної документації не містить",
    "замовник застосував вимогу вибірково",
    "аналогічна невідповідність у пропозиції переможця",
    "невідповідність могла бути усунена протягом 24 годин",
    "документ надано у складі тендерної пропозиції",
    "зміст документа відповідає вимозі",
]

#: §8.2 — КОНТРПОШУК: практика на користь замовника. Обов'язковий (§7.5).
BUYER_COUNTER_FORMULAS = [
    "скаржник не довів та документально не підтвердив",
    "відсутні підстави для задоволення скарги в цій частині",
    "пропозиція скаржника не відповідала вимогам документації",
    "документ відсутній у складі тендерної пропозиції",
    "невідповідність стосується технічних та якісних характеристик",
    "невідповідність не підлягала усуненню протягом 24 годин",
    "замовник правомірно відхилив пропозицію",
    "скаржник погодився з умовами документації та не оскаржив їх",
]

#: §8.3 — наскрізні теми. Спрацьовують незалежно від коду підстави.
CROSS_CUTTING_TOPICS = {
    "X01_MOTIVATION": [
        "не конкретизовано у чому полягає невідповідність",
        "рішення про відхилення не містить конкретного пункту документації",
        "загальне посилання на тендерну документацію",
    ],
    "X02_REQUIREMENT_NOT_IN_TD": [
        "тендерна документація не містить такої вимоги",
        "відхилення за вимогою не передбаченою документацією",
        "замовник розширив вимоги тендерної документації",
    ],
    "X03_EQUAL_TREATMENT": [
        "аналогічна невідповідність у пропозиції переможця",
        "вибіркове застосування вимог до учасників",
        "різний підхід до оцінки тендерних пропозицій",
    ],
    "X04_DOCUMENT_EXISTS": [
        "документ надано у складі тендерної пропозиції",
        "інформація міститься в іншому документі пропозиції",
        "зміст документа підтверджує вимогу",
    ],
    "X05_FORMAL_ERROR": [
        "формальна помилка наказ 710 відхилення",
        "помилка не впливає на зміст тендерної пропозиції",
        "невідповідність назви документа за правильного змісту",
    ],
    "X06_CORRECTABILITY": [
        "невідповідність могла бути усунена протягом 24 годин",
        "замовник не надав можливість усунути невідповідність",
        "повідомлення про усунення невідповідностей не містить конкретного переліку",
    ],
    "X07_CONDITIONS_NOT_CHALLENGED": [
        "скаржник не оскаржив умови тендерної документації",
        "учасник погодився з умовами документації подавши пропозицію",
    ],
}

#: Коли вмикати наскрізну тему. Ключ -> регулярний вираз по тексту претензії.
#: X01 і X03 вмикаються не текстом, а фактами справи (блок 28).
CROSS_CUTTING_TRIGGERS = {
    "X02_REQUIREMENT_NOT_IN_TD": r"розшир\w+\s+вимог|не\s+передбачен\w+\s+документац",
    "X04_DOCUMENT_EXISTS": r"надан\w+\s+у\s+складі|міститься\s+в\s+інш|інш\w+\s+документ",
    "X05_FORMAL_ERROR": r"формальн\w+\s+помилк|описк|друкарськ\w+\s+помилк|назв\w+\s+документа",
    "X06_CORRECTABILITY": r"24\s*годин|усунен\w+\s+невідповідн|виправ\w+",
    "X07_CONDITIONS_NOT_CHALLENGED": r"не\s+оскарж\w+\s+умов|погодивс\w+\s+з\s+умовами",
}

#: §10 — галузеві терміни. Додаються ЛИШЕ якщо тема справді про це.
SECTOR_TERMS = {
    "construction_estimate": [
        "локальний кошторис", "договірна ціна", "відомість обсягів робіт",
        "підсумкова відомість ресурсів", "дефектний акт", "кошторисна норма",
        "шифр норми", "загальновиробничі витрати", "кошторисний прибуток",
    ],
    "construction_execution": [
        "календарний графік", "дозвіл Держпраці", "роботи підвищеної небезпеки",
        "декларація відповідності матеріально технічної бази",
        "будівельна ліцензія", "клас наслідків", "машини і механізми",
    ],
    "design": [
        "завдання на проєктування", "інженер проєктувальник",
        "кваліфікаційний сертифікат", "головний інженер проєкту",
        "проєктна документація", "експертний звіт", "авторський нагляд",
    ],
    "technical_supervision": [
        "інженер технічного нагляду", "сертифікат технічного нагляду",
        "клас наслідків", "технічний нагляд", "акт виконаних робіт",
    ],
    "goods_equipment": [
        "технічний паспорт", "каталог виробника", "торговельна марка",
        "еквівалент", "сертифікат відповідності", "гарантійний строк",
        "авторизаційний лист виробника",
    ],
}

#: Який сектор вмикати за текстом претензії.
SECTOR_TRIGGERS = {
    "construction_estimate":
        r"кошторис|договірн\w+\s+цін|обсяг\w+\s+робіт|відомост\w+\s+обсяг"
        r"|дефектн\w+\s+акт|шифр\w*\s+норм|загальновиробнич|ресурс",
    "construction_execution":
        r"дозвіл|Держпрац|підвищен\w+\s+небезпек|ліценз\w+|клас\w*\s+наслідк"
        r"|календарн\w+\s+графік|машин\w+\s+(?:і|та)\s+механізм",
    "design":
        r"проєктуванн|проектуванн|проєктувальник|головн\w+\s+інженер\w*\s+проєкт"
        r"|експертн\w+\s+звіт|авторськ\w+\s+нагляд|проєктн\w+\s+документац",
    "technical_supervision":
        r"технічн\w+\s+нагляд|інженер\w*\s+технічного\s+нагляду",
    "goods_equipment":
        r"технічн\w+\s+паспорт|торговельн\w+\s+марк|еквівалент|виробник"
        r"|артикул|модель|гарантійн\w+\s+строк",
}

#: §10 — історичні й орфографічні варіанти. Окремими короткими запитами.
TERM_VARIANTS = {
    "проєкт": ["проєкт", "проект"],
    "матеріально-технічна": ["матеріально технічна", "МТБ"],
    "цивільно-правовий": ["цивільно правовий", "ЦПД"],
    "підпис": ["КЕП", "УЕП", "електронний підпис", "ЕЦП"],
    "відомість обсягів робіт": ["ВОР", "обсяги робіт"],
    "технічна специфікація": ["технічні вимоги", "технічне завдання"],
}

#: §11.2 — порядок пріоритету тем. Менше число = точніша тема.
#: R10 і R99 не мають поглинати R06/R09/R01/R03 (§15 п. 4).
CODE_PRIORITY = {
    "R04": 1,
    "R06": 2, "R07": 2, "R08": 2, "R09": 2, "R11": 2, "R12": 2, "R13": 2, "R14": 2,
    "R03": 3,
    "R01": 4,
    "R02": 5,
    "R05": 6,
    "R10": 7,
    "R99": 8,
}

CODE_HUMAN_FULL = {
    "R01": "Кваліфікаційні критерії",
    "R02": "Підстави для відмови в участі / документи переможця",
    "R03": "Технічна специфікація, обсяги робіт і кошторис",
    "R04": "Усунення невідповідностей протягом 24 годин",
    "R05": "Недостовірна інформація",
    "R06": "Забезпечення тендерної пропозиції / банківська гарантія",
    "R07": "Аномально низька ціна",
    "R08": "Конфіденційна інформація",
    "R09": "Ліцензії, дозволи, декларації та сертифікати",
    "R10": "Ненадання документа та формальні невідповідності",
    "R11": "Мова документів і переклад",
    "R12": "Строк дії тендерної пропозиції",
    "R13": "Відмова переможця від договору / документи після перемоги",
    "R14": "Забезпечення виконання договору",
    "R99": "Інші та не класифіковані підстави",
}

# ---------------------------------------------------------------------------
#  §9. Тематичний словник: код -> підтема -> {назва, тригер, запити}
#  «trigger» — коли підтема вмикається. «q» — пошукові фрази, НЕ висновки.
# ---------------------------------------------------------------------------
CLARITY_SUBTOPICS = {
"R01": {
 "R01_EXPERIENCE_SUBJECT": {
  "name": "предмет аналогічного договору",
  "trigger": r"аналогічн\w+\s+(?:договор|за\s+предмет)|предмет\w*\s+договор|вид\w*\s+робіт",
  "q": ["аналогічний договір не відповідає предмету закупівлі",
        "предмет аналогічного договору не є аналогічним",
        "аналогічний договір за кодом CPV",
        "аналогічний договір за видом робіт",
        "вимога щодо конкретного предмета аналогічного договору"]},
 "R01_EXPERIENCE_PROOF": {
  "name": "підтвердження виконання договору",
  "trigger": r"акт\w*\s+виконан|лист[- ]?відгук|відгук|додатк\w+\s+до\s+(?:аналогічн|договор)"
             r"|виконанн\w+\s+аналогічн",
  "q": ["не надав додатки до аналогічного договору",
        "відсутній акт виконаних робіт за аналогічним договором",
        "акти не підтверджують повне виконання аналогічного договору",
        "лист відгук до аналогічного договору",
        "відгук не містить номер і дату договору",
        "сума виконання аналогічного договору"]},
 "R01_STAFF_AVAILABILITY": {
  "name": "наявність працівників",
  "trigger": r"працівник|персонал|штатн\w+\s+розпис|трудов\w+\s+книжк|наказ\w*\s+про\s+прийн"
             r"|цивільно[- ]правов",
  "q": ["довідка про наявність працівників відповідної кваліфікації",
        "не підтверджено наявність працівника",
        "наказ про прийняття працівника відсутній",
        "трудова книжка працівника тендерна пропозиція",
        "цивільно правовий договір з працівником",
        "працівник залучений за договором надання послуг"]},
 "R01_STAFF_QUALIFICATION": {
  "name": "освіта, досвід і сертифікати працівників",
  "trigger": r"диплом|стаж\b|кваліфікаційн\w+\s+сертифікат|підвищенн\w+\s+кваліфікац"
             r"|освіт\w+\s+працівник",
  "q": ["диплом працівника не відповідає вимогам",
        "стаж роботи працівника не підтверджено",
        "кваліфікаційний сертифікат інженера проєктувальника",
        "сертифікат інженера технічного нагляду",
        "сертифікат архітектора тендерна пропозиція",
        "підвищення кваліфікації працівника документ"]},
 "R01_EQUIPMENT": {
  "name": "обладнання та матеріально-технічна база",
  "trigger": r"обладнанн|матеріально[- ]технічн|устаткуванн|орендн?\w*\s+обладнан"
             r"|транспортн\w+\s+засоб|машин\w+\s+(?:і|та)\s+механізм",
  "q": ["довідка про матеріально технічну базу",
        "не підтверджено право користування обладнанням",
        "договір оренди обладнання тендерна пропозиція",
        "обладнання відсутнє у довідці учасника",
        "техніка залучена за договором надання послуг",
        "свідоцтво про реєстрацію транспортного засобу учасника"]},
 "R01_FINANCIAL_CAPACITY": {
  "name": "фінансова спроможність",
  "trigger": r"фінансов\w+\s+(?:спроможн|звітн)|річн\w+\s+дохід|чист\w+\s+дохід|обсяг\w*\s+доход",
  "q": ["фінансова спроможність учасника річний дохід",
        "обсяг річного доходу не підтверджено",
        "фінансова звітність учасника відсутня",
        "чистий дохід менший очікуваної вартості",
        "звітний період фінансової звітності"]},
 "R01_SUBCONTRACTORS": {
  "name": "субпідрядники та залучені потужності",
  "trigger": r"субпідряд|залучен\w+\s+потужност|співвиконав",
  "q": ["інформація про субпідрядника відсутня",
        "залучення потужностей субпідрядника кваліфікаційні критерії",
        "договір із субпідрядником не надано",
        "працівники субпідрядника підтвердження кваліфікації",
        "обладнання субпідрядника підтвердження"]},
 "_counter": ["скаржник не підтвердив кваліфікаційний критерій",
              "документ відсутній у пропозиції",
              "умова документації не була оскаржена"]},

"R02": {
 "R02_SELF_DECLARATION": {
  "name": "самодекларування та підтвердні документи",
  "trigger": r"самостійн\w+\s+декларув|декларуванн|підстав\w+\s+для\s+відмов"
             r"|пункт\w*\s*47|п\.?\s*47\s+Особлив|стат\w*\s*17",
  "q": ["самостійне декларування відсутності підстав для відмови",
        "переможець не підтвердив відсутність підстав",
        "документи переможця пункт 47 Особливостей",
        "документи переможця стаття 17 Закону",
        "інформація в електронній системі про відсутність підстав"]},
 "R02_TAX_DEBT": {
  "name": "податкова заборгованість",
  "trigger": r"заборгован\w+\s+(?:зі|з)\s+сплат|податков\w+\s+заборгован|розстроченн",
  "q": ["податкова заборгованість учасника відхилення",
        "заборгованість зі сплати податків і зборів",
        "розстрочення податкової заборгованості учасника",
        "довідка податкової про відсутність заборгованості"]},
 "R02_CORRUPTION_CONVICTION": {
  "name": "корупція, судимість, відповідальність",
  "trigger": r"корупц|судим|реєстр\w*\s+корупціонер",
  "q": ["корупційне правопорушення службової особи учасника",
        "судимість керівника учасника тендер",
        "реєстр корупціонерів підстава для відмови",
        "притягнення до відповідальності за корупційне правопорушення"]},
 "R02_BANKRUPTCY_SANCTIONS": {
  "name": "банкрутство, припинення, санкції",
  "trigger": r"банкрут|припиненн\w+\s+юридичн|санкц|російськ|білорус",
  "q": ["учасник перебуває у процедурі банкрутства",
        "припинення юридичної особи підстава відмови",
        "застосування санкцій до учасника закупівлі",
        "учасник пов'язаний з російською федерацією відхилення"]},
 "_counter": ["інформація реєстру підтверджує підставу для відмови",
              "переможець не надав підтвердні документи",
              "скаржник не спростував наявність підстави"]},

"R03": {
 "R03_CHARACTERISTICS": {
  "name": "технічні та якісні характеристики",
  "trigger": r"технічн\w+\s+(?:специфікац|характеристик|вимог)|якісн\w+\s+характеристик"
             r"|технічн\w+\s+паспорт|порівняльн\w+\s+таблиц",
  "q": ["товар не відповідає технічній специфікації",
        "технічні характеристики запропонованого товару не підтверджено",
        "технічний паспорт не підтверджує характеристику",
        "модель обладнання не відповідає технічним вимогам",
        "порівняльна таблиця технічних характеристик"]},
 "R03_BRAND_EQUIVALENT": {
  "name": "марка, виробник, еквівалент",
  "trigger": r"еквівалент|торговельн\w+\s+марк|виробник|авторизаційн\w+\s+лист",
  "q": ["запропоновано іншу торговельну марку еквівалент",
        "технічні характеристики еквівалента",
        "виробник товару не підтверджений",
        "лист виробника тендерна пропозиція",
        "авторизаційний лист виробника відхилення"]},
 "R03_BOQ": {
  "name": "відомість обсягів робіт",
  "trigger": r"відомост\w+\s+обсяг|обсяг\w+\s+робіт|\bВОР\b|пропущен\w+\s+позиц",
  "q": ["відомість обсягів робіт не відповідає технічному завданню",
        "локальний кошторис не відповідає відомості обсягів робіт",
        "розбіжність обсягів робіт у кошторисі",
        "пропущена позиція відомості обсягів робіт",
        "змінено найменування робіт у локальному кошторисі"]},
 "R03_ESTIMATE": {
  "name": "договірна ціна і кошторисні документи",
  "trigger": r"кошторис|договірн\w+\s+цін|підсумков\w+\s+відомост\w+\s+ресурс"
             r"|загальновиробнич|кошторисн\w+\s+прибут|пояснювальн\w+\s+записк",
  "q": ["договірна ціна не відповідає вимогам документації",
        "локальний кошторис відсутній у пропозиції",
        "підсумкова відомість ресурсів відсутня",
        "розрахунок загальновиробничих витрат відсутній",
        "розрахунок кошторисного прибутку відсутній",
        "пояснювальна записка до договірної ціни",
        "кошторисний розрахунок містить розбіжності"]},
 "R03_NORM_CODE": {
  "name": "шифр норми, ресурс і коефіцієнт",
  "trigger": r"шифр\w*\s+норм|кошторисн\w+\s+норм|коефіцієнт|замін\w+\s+(?:будівельн|матеріал)"
             r"|ресурсн\w+\s+елементн",
  "q": ["шифр кошторисної норми не відповідає відомості обсягів",
        "змінено ресурс у локальному кошторисі",
        "коефіцієнт у локальному кошторисі відрізняється",
        "заміна будівельного матеріалу в кошторисі",
        "ресурсна елементна кошторисна норма розбіжність"]},
 "R03_QUANTITY_UNIT": {
  "name": "кількість та одиниця виміру",
  "trigger": r"одиниц\w+\s+вимір|кількіст\w+\s+товар|множник|розбіжніст\w+\s+кількост",
  "q": ["одиниця виміру не відповідає технічному завданню",
        "кількість товару не відповідає специфікації",
        "обсяг робіт зазначено з помилкою",
        "множник обсягу робіт у кошторисі",
        "розбіжність кількості між специфікацією і пропозицією"]},
 "R03_TASK_PROJECT": {
  "name": "технічне завдання, дефектний акт, проєкт",
  "trigger": r"дефектн\w+\s+акт|технічн\w+\s+завданн|завданн\w+\s+на\s+проєктув"
             r"|клас\w*\s+наслідк|вихідн\w+\s+дан",
  "q": ["дефектний акт не відповідає технічному завданню",
        "технічне завдання не підписано учасником",
        "проєктне рішення не відповідає завданню на проєктування",
        "клас наслідків об'єкта не підтверджено",
        "вихідні дані для проєктування відсутні"]},
 "R03_SCHEDULE": {
  "name": "календарний графік і строк виконання",
  "trigger": r"календарн\w+\s+графік|графік\w*\s+(?:виконанн|фінансув)|етап\w+\s+виконанн",
  "q": ["календарний графік виконання робіт не відповідає строку",
        "строк виконання робіт у графіку",
        "графік фінансування робіт відсутній",
        "етапи виконання проєктних робіт графік"]},
 "R03_STANDARDS": {
  "name": "ДБН, ДСТУ, технічні регламенти",
  "trigger": r"\bДБН\b|\bДСТУ\b|технічн\w+\s+регламент|нормативн\w+\s+документ",
  "q": ["невідповідність вимогам ДБН тендерна пропозиція",
        "невідповідність ДСТУ технічна специфікація",
        "посилання на застарілий ДБН у пропозиції",
        "сертифікат відповідності технічному регламенту",
        "нормативний документ зазначено неправильно"]},
 "_counter": ["невідповідність стосується технічних та якісних характеристик",
              "технічна невідповідність не підлягала усуненню",
              "скаржник запропонував інший товар",
              "обсяг робіт змінено учасником"]},

"R04": {
 "R04_NO_NOTICE": {
  "name": "замовник не надав можливості виправити",
  "trigger": r"не\s+надав\w*\s+можливіст|без\s+повідомленн|не\s+розміщ\w+\s+вимог"
             r"|не\s+повідом\w+\s+про\s+невідповідн",
  "q": ["замовник не надав можливість усунути невідповідність",
        "невідповідність могла бути усунена протягом 24 годин",
        "відхилення без повідомлення про усунення невідповідностей",
        "не розміщено вимогу про усунення невідповідностей"]},
 "R04_UNCLEAR_NOTICE": {
  "name": "неконкретне повідомлення",
  "trigger": r"не\s+зазнач\w+\s+як\w+\s+документ|неконкретн|не\s+містить\s+переліку"
             r"|повідомленн\w+[^.;]{0,60}не\s+містить",
  "q": ["повідомлення про усунення невідповідностей не містить конкретного переліку",
        "замовник не зазначив які документи потрібно подати",
        "неконкретна вимога про усунення невідповідностей",
        "повідомлення не містить посилання на вимогу документації"]},
 "R04_FIXED_BUT_REJECTED": {
  "name": "учасник виправив, але його відхилили",
  "trigger": r"(?<!не )усунув|(?<!не )виправив|завантажив\w*\s+на\s+виконанн"
             r"|повторно\s+відхил|виправлен\w+\s+документ",
  "q": ["учасник усунув невідповідності протягом 24 годин",
        "виправлені документи не враховані замовником",
        "замовник повторно відхилив після усунення невідповідностей",
        "документ завантажено на виконання повідомлення про усунення"]},
 "R04_TIME": {
  "name": "строк і момент виправлення",
  "trigger": r"спли\w+\s+строк|в\s+межах\s+24|час\w*\s+розміщенн|обчисл\w+\s+24",
  "q": ["неправильно обчислено 24 години для усунення невідповідностей",
        "документ завантажено в межах 24 годин",
        "строк на усунення невідповідностей сплив",
        "час розміщення повідомлення про усунення невідповідностей"]},
 "R04_NOT_CORRECTABLE": {
  "name": "межі механізму виправлення",
  "trigger": r"не\s+підляга\w+\s+усуненн|змін\w+\s+предмет|технічн\w+\s+та\s+якісн",
  "q": ["невідповідність не підлягала усуненню протягом 24 годин",
        "виправлення змінює предмет закупівлі запропонований учасником",
        "зміна технічних характеристик під час усунення невідповідностей",
        "відсутність забезпечення тендерної пропозиції 24 години"]},
 "_counter": ["невідповідність не підлягала усуненню протягом 24 годин",
              "механізм 24 годин не поширюється на цю невідповідність",
              "учасник не усунув невідповідність у встановлений строк"]},

"R05": {
 "R05_FALSE_INFORMATION": {
  "name": "твердження про недостовірність",
  "trigger": r"недостовірн|неправдив",
  "q": ["учасник зазначив недостовірну інформацію",
        "недостовірна інформація суттєва для визначення результату",
        "інформація в довідці не відповідає документам",
        "неправдиві відомості про працівників",
        "недостовірні відомості про аналогічний договір"]},
 "R05_REGISTER_CONFLICT": {
  "name": "розбіжність із реєстром або відповіддю третьої особи",
  "trigger": r"реєстр|відповід\w+\s+контрагент|перевір\w+\s+у\s+відкрит",
  "q": ["інформація учасника не відповідає даним відкритого реєстру",
        "замовник перевірив інформацію у відкритому реєстрі",
        "відповідь контрагента спростовує виконання договору",
        "недостовірність інформації не доведена замовником",
        "джерело підтвердження недостовірної інформації"]},
 "_counter": ["замовник документально підтвердив недостовірність",
              "скаржник не спростував дані реєстру",
              "недостовірна інформація вплинула на результат"]},

"R06": {
 "R06_ABSENT": {
  "name": "забезпечення відсутнє",
  "trigger": r"не\s+надан\w+\s+забезпеченн|гаранті\w+\s+відсутн|не\s+заванта\w+\s+гарант",
  "q": ["не надано забезпечення тендерної пропозиції",
        "банківська гарантія відсутня у пропозиції",
        "електронна гарантія не завантажена"]},
 "R06_AMOUNT_VALIDITY": {
  "name": "сума і строк дії",
  "trigger": r"сум\w+\s+(?:банківськ\w+\s+)?гаранті|строк\w*\s+ді[її]\s+(?:банківськ|гаранті)"
             r"|валют\w+\s+гаранті|дат\w+\s+початку\s+ді",
  "q": ["сума банківської гарантії менша вимоги",
        "строк дії банківської гарантії недостатній",
        "дата початку дії гарантії не відповідає документації",
        "валюта банківської гарантії не відповідає вимозі"]},
 "R06_TEXT_FORM": {
  "name": "зміст і форма гарантії",
  "trigger": r"форм\w+\s+гаранті|безумовн|безвідклич|бенефіціар|умов\w+\s+сплат",
  "q": ["банківська гарантія не відповідає формі тендерної документації",
        "безумовна та безвідклична банківська гарантія",
        "умови сплати за банківською гарантією",
        "текст гарантії містить додаткові умови",
        "бенефіціар у банківській гарантії зазначений неправильно"]},
 "R06_BANK_KEP_COVERAGE": {
  "name": "підпис банку і грошове покриття",
  "trigger": r"підпис\w*\s+банк|КЕП\s+банк|грошов\w+\s+покритт|повноваженн\w+\s+підписант\w*\s+банк",
  "q": ["кваліфікований електронний підпис банку гарантія",
        "банківська гарантія не підписана КЕП",
        "довідка про грошове покриття банківської гарантії",
        "підтвердження повноважень підписанта банку"]},
 "_counter": ["забезпечення не підлягає виправленню 24 години",
              "гарантія містить умови що обмежують сплату",
              "строк гарантії не відповідає вимозі"]},

"R07": {
 "R07_JUSTIFICATION": {
  "name": "обґрунтування ціни",
  "trigger": r"обґрунтуванн|обгрунтуванн",
  "q": ["обґрунтування аномально низької ціни",
        "учасник не надав обґрунтування аномально низької ціни",
        "обґрунтування аномально низької ціни є належним",
        "замовник відхилив обґрунтування аномально низької ціни",
        "рішення про неприйняття обґрунтування не мотивоване"]},
 "R07_CALCULATION_TIME": {
  "name": "розрахунок і строк",
  "trigger": r"строк\w*\s+поданн|економі\w+\s+технологічн|сприятлив\w+\s+умов"
             r"|державн\w+\s+допомог|визначен\w+\s+аномальн",
  "q": ["неправильно визначено аномально низьку ціну",
        "строк подання обґрунтування аномально низької ціни",
        "економія технологічного процесу обґрунтування ціни",
        "сприятливі умови постачання обґрунтування ціни",
        "державна допомога обґрунтування аномально низької ціни"]},
 "_counter": ["обґрунтування містить загальні фрази",
              "учасник не підтвердив економічні фактори",
              "обґрунтування подано з порушенням строку"]},

"R08": {
 "R08_UNLAWFUL_CONFIDENTIALITY": {
  "name": "неправомірне обмеження доступу",
  "trigger": r"конфіденцій",
  "q": ["учасник визначив конфіденційною всю пропозицію",
        "технічні характеристики визначено конфіденційними",
        "ціна тендерної пропозиції конфіденційна інформація",
        "документи кваліфікації визначено конфіденційними",
        "неправомірне визначення інформації конфіденційною"]},
 "R08_JUSTIFIED_CONFIDENTIALITY": {
  "name": "допустимість обмеження",
  "trigger": r"комерційн\w+\s+таємниц|персональн\w+\s+дан|обґрунтуванн\w+\s+конфіденц",
  "q": ["обґрунтування конфіденційності інформації учасником",
        "комерційна таємниця у тендерній пропозиції",
        "персональні дані працівників конфіденційність",
        "конфіденційна інформація не впливає на оцінку пропозиції"]},
 "_counter": ["інформація не може бути визначена конфіденційною",
              "учасник не обґрунтував конфіденційність",
              "обмеження доступу перешкоджає перевірці пропозиції"]},

"R09": {
 "R09_CONSTRUCTION_LICENSE": {
  "name": "право виконувати будівельні роботи",
  "trigger": r"ліценз\w+|право\s+виконанн\w+\s+будівельн|ліцензійн\w+\s+умов",
  "q": ["ліцензія на будівельні роботи тендерна пропозиція",
        "право виконання будівельних робіт у період воєнного стану",
        "ліцензійні умови будівництво відхилення",
        "клас наслідків будівельна ліцензія"]},
 "R09_HAZARDOUS_WORKS": {
  "name": "роботи підвищеної небезпеки",
  "trigger": r"підвищен\w+\s+небезпек|Держпрац|деклараці\w+\s+відповідност",
  "q": ["дозвіл на виконання робіт підвищеної небезпеки",
        "декларація відповідності матеріально технічної бази",
        "перелік робіт у дозволі не охоплює предмет закупівлі",
        "дозвіл Держпраці строк дії"]},
 "R09_PROFESSIONAL_CERTIFICATE": {
  "name": "сертифікат відповідального виконавця",
  "trigger": r"кваліфікаційн\w+\s+сертифікат|сертифікат\w*\s+(?:інженер|архітектор)"
             r"|відповідальн\w+\s+виконав",
  "q": ["кваліфікаційний сертифікат відповідального виконавця",
        "сертифікат інженера технічного нагляду",
        "сертифікат інженера проєктувальника",
        "сертифікат архітектора строк дії",
        "напрям кваліфікаційного сертифіката не відповідає роботам"]},
 "R09_ISO_CONFORMITY": {
  "name": "ISO та сертифікати відповідності",
  "trigger": r"\bISO\b|сертифікат\w*\s+відповідност|сфер\w+\s+сертифікац|сертифікат\w*\s+якост",
  "q": ["сертифікат ISO не відповідає предмету закупівлі",
        "сертифікат відповідності відсутній у пропозиції",
        "сертифікат виданий не на учасника",
        "сфера сертифікації не охоплює предмет закупівлі",
        "сертифікат якості виробника"]},
 "R09_TIME_OF_AVAILABILITY": {
  "name": "коли документ повинен існувати",
  "trigger": r"на\s+момент\s+поданн|перед\s+укладенн|після\s+визначенн\w+\s+переможц",
  "q": ["ліцензія має бути наявна на момент подання пропозиції",
        "дозвіл подається переможцем перед укладенням договору",
        "вимога надати дозвіл після визначення переможця",
        "документ можна отримати після укладення договору"]},
 "_counter": ["вид діяльності потребує ліцензії",
              "дозвіл не охоплює заявлені роботи",
              "сертифікат нечинний на дату подання"]},

"R10": {
 "R10_MISSING_DOCUMENT": {
  "name": "документ нібито відсутній",
  "trigger": r"не\s+надав|не\s+нада(?:но|ла|ли)|ненаданн|відсутн\w+\s+документ|не\s+заванта",
  "q": ["документ відсутній у складі тендерної пропозиції",
        "учасник не надав документ передбачений документацією",
        "інформація міститься в іншому документі пропозиції",
        "замовник не врахував поданий документ",
        "файл надано під іншою назвою"]},
 "R10_WRONG_FORM_TITLE": {
  "name": "назва, форма та шаблон",
  "trigger": r"не\s+за\s+форм|форм\w+\s+замовник|змінив\s+форм|не\s+заповнен\w+\s+граф"
             r"|довільн\w+\s+форм|назв\w+\s+документ",
  "q": ["невідповідність назви документа за правильного змісту",
        "довідка надана не за формою замовника",
        "учасник змінив форму таблиці тендерної документації",
        "не заповнено окрему графу форми",
        "довільна форма документа за відсутності обов'язкового шаблону"]},
 "R10_SIGNATURE_SEAL_KEP": {
  "name": "підпис, печатка та КЕП",
  "trigger": r"\bКЕП\b|\bЕЦП\b|\bУЕП\b|електронн\w+\s+підпис|печатк|не\s+підписан",
  "q": ["документ не підписаний учасником",
        "відсутня печатка на документі тендерної пропозиції",
        "тендерна пропозиція не підписана КЕП",
        "накладено удосконалений електронний підпис замість КЕП",
        "власноручний підпис відсутній за наявності КЕП",
        "підпис на окремій сторінці документа відсутній"]},
 "R10_SCAN_COPY_FORMAT": {
  "name": "скан, копія та формат файла",
  "trigger": r"скан|копі\w+\s+документ|формат\w*\s+файл|не\s+засвідчен",
  "q": ["надано скановану копію замість оригіналу",
        "копія документа не засвідчена учасником",
        "формат файла не відповідає вимозі документації",
        "файл відкривається але має інший формат",
        "неякісна сканована копія документа"]},
 "R10_NUMBER_DATE_TYPO": {
  "name": "номер, дата та описка",
  "trigger": r"помилк\w+\s+(?:в|у)\s+(?:номер|дат)|описк|друкарськ|прописом"
             r"|вихідн\w+\s+номер",
  "q": ["помилка в номері документа тендерна пропозиція",
        "помилка в даті документа тендерна пропозиція",
        "друкарська помилка у довідці учасника",
        "документ довільної форми без вихідного номера",
        "розбіжність цифр і суми прописом"]},
 "R10_AUTHORITY": {
  "name": "повноваження підписанта",
  "trigger": r"повноваженн|довіреніст|наказ\w*\s+про\s+призначенн|протокол\w*\s+загальн\w+\s+збор",
  "q": ["не підтверджено повноваження підписанта тендерної пропозиції",
        "наказ про призначення директора відсутній",
        "протокол загальних зборів повноваження директора",
        "довіреність на підписання тендерної пропозиції",
        "підпис особи повноваження якої не підтверджені"]},
 "R10_DUPLICATE_EVIDENCE": {
  "name": "інформація підтверджена іншим документом",
  "trigger": r"інш\w+\s+документ|у\s+сукупност|вже\s+міститься",
  "q": ["інформація підтверджена іншим документом пропозиції",
        "зміст поданих документів у сукупності підтверджує вимогу",
        "повторне ненадання інформації яка вже міститься в системі",
        "замовник оцінив лише назву а не зміст документа"]},
 "_counter": ["документація прямо вимагала окремий документ",
              "інформація в інших документах відсутня",
              "помилка впливає на зміст пропозиції",
              "формальна помилка не охоплює цю невідповідність"]},

"R11": {
 "R11_LANGUAGE": {
  "name": "українська мова документів",
  "trigger": r"українськ\w+\s+мов|інш\w+\s+мов|іноземн\w+\s+мов|переклад",
  "q": ["документ тендерної пропозиції викладений не українською мовою",
        "відсутній переклад документа українською мовою",
        "переклад документа не засвідчений",
        "автентичним є текст українською мовою",
        "технічна документація виробника іноземною мовою",
        "назва торговельної марки іноземною мовою"]},
 "R11_TRANSLATION_SCOPE": {
  "name": "обсяг і винятки перекладу",
  "trigger": r"частин\w+\s+документ|нерезидент|загальновживан\w+\s+термін",
  "q": ["переклад лише частини документа тендерна пропозиція",
        "сертифікат іноземною мовою без перекладу",
        "загальновживані технічні терміни іноземною мовою",
        "документ виданий нерезидентом переклад"]},
 "_counter": ["документація прямо вимагала переклад",
              "відсутність перекладу перешкоджає встановленню змісту",
              "виняток щодо торговельної марки не застосовується"]},

"R12": {
 "R12_VALIDITY": {
  "name": "строк дії пропозиції та продовження",
  "trigger": r"строк\w*\s+ді[її]\s+(?:тендерн\w+\s+)?пропозиц|продовженн\w+\s+строк",
  "q": ["строк дії тендерної пропозиції не відповідає вимозі",
        "строк дії пропозиції закінчився",
        "учасник не погодив продовження строку дії пропозиції",
        "лист згода на продовження строку дії пропозиції",
        "строк дії банківської гарантії та тендерної пропозиції",
        "неправильне обчислення строку дії пропозиції"]},
 "_counter": ["строк дії пропозиції сплив до визначення переможця",
              "учасник відмовився продовжити строк",
              "поданий документ встановлює коротший строк"]},

"R13": {
 "R13_CONTRACT_REFUSAL": {
  "name": "відмова від підписання договору",
  "trigger": r"відмов\w+\s+від\s+підписанн|не\s+підписа\w+\s+договор|істотн\w+\s+умов\w+\s+договор",
  "q": ["переможець відмовився від підписання договору",
        "переможець не підписав договір у встановлений строк",
        "неподання підписаного договору переможцем",
        "зміна істотних умов договору переможцем"]},
 "R13_WINNER_DOCUMENTS": {
  "name": "документи переможця",
  "trigger": r"документ\w+\s+переможц|після\s+визначенн\w+\s+переможц",
  "q": ["переможець не надав документи після визначення переможця",
        "документи переможця подано з порушенням строку",
        "довідка про відсутність підстав переможець",
        "ліцензія переможця перед укладенням договору",
        "документи переможця вже містяться у пропозиції"]},
 "R13_PRICE_CONFIRMATION": {
  "name": "підтвердження ціни перед договором",
  "trigger": r"договірн\w+\s+цін\w+\s+після\s+аукціон|розрахунок\w*\s+договірн\w+\s+цін",
  "q": ["переможець не надав розрахунок договірної ціни",
        "договірна ціна після аукціону не відповідає пропозиції",
        "переможець не надав документи для укладення договору"]},
 "_counter": ["обов'язок переможця прямо передбачений документацією",
              "документи подано після встановленого строку",
              "непідписання договору підтверджено системою"]},

"R14": {
 "R14_PERFORMANCE_SECURITY": {
  "name": "забезпечення виконання договору",
  "trigger": r"забезпеченн\w+\s+виконанн\w+\s+договор",
  "q": ["переможець не надав забезпечення виконання договору",
        "забезпечення виконання договору надано з порушенням строку",
        "сума забезпечення виконання договору не відповідає вимозі",
        "банківська гарантія забезпечення виконання договору",
        "форма забезпечення виконання договору не відповідає документації",
        "строк дії забезпечення виконання договору недостатній"]},
 "R14_STAGE": {
  "name": "етап: пропозиція чи укладення договору",
  "trigger": r"у\s+складі\s+пропозиц\w+[^.;]{0,40}забезпеченн\w+\s+виконанн"
             r"|до\s+укладенн\w+\s+договор",
  "q": ["забезпечення виконання договору вимагалось у складі пропозиції",
        "забезпечення виконання договору надається переможцем",
        "ненадання забезпечення до укладення договору"]},
 "_counter": ["переможець порушив строк надання забезпечення",
              "гарантія не відповідає обов'язковій формі",
              "забезпечення не покриває строк договору"]},

"R99": {
 "R99_PRICE_VAT": {
  "name": "ціна, ПДВ та арифметика",
  "trigger": r"\bПДВ\b|арифметичн\w+\s+помилк|прописом|після\s+аукціон\w+\s+не\s+відповіда",
  "q": ["ціна тендерної пропозиції з ПДВ без ПДВ",
        "арифметична помилка у тендерній пропозиції",
        "розбіжність ціни цифрами і прописом",
        "ціна після аукціону не відповідає ціновій пропозиції",
        "учасник неплатник ПДВ оцінка пропозиції"]},
 "R99_LOCALIZATION_ORIGIN": {
  "name": "локалізація і походження товару",
  "trigger": r"локалізац|походженн\w+\s+товар|країн\w+\s+походженн",
  "q": ["ступінь локалізації виробництва товару",
        "товар відсутній у переліку локалізованих товарів",
        "сертифікат походження товару відсутній",
        "країна походження товару не підтверджена"]},
 "R99_JOINT_VENTURE": {
  "name": "об'єднання учасників",
  "trigger": r"об'єднанн\w+\s+учасник|консорціум|спільн\w+\s+діяльніст",
  "q": ["об'єднання учасників підтвердження кваліфікаційних критеріїв",
        "спільна діяльність учасників тендерна пропозиція",
        "консорціум документи тендерної пропозиції"]},
 "R99_RELATIONSHIP_CONFLICT": {
  "name": "пов'язаність і конфлікт інтересів",
  "trigger": r"пов'язан\w+\s+особ|конфлікт\w*\s+інтерес",
  "q": ["пов'язані особи учасники процедури закупівлі",
        "конфлікт інтересів замовника та учасника",
        "учасники подали пропозиції пов'язані між собою"]},
 "R99_ACCESS_TECHNICAL": {
  "name": "файл не відкривається або технічна помилка",
  "trigger": r"не\s+відкрива|пошкоджен\w+\s+файл|не\s+зміг\w*\s+перегля|за\s+посиланн",
  "q": ["файл тендерної пропозиції не відкривається",
        "пошкоджений файл у складі тендерної пропозиції",
        "замовник не зміг переглянути електронний документ",
        "документ доступний за посиланням у пропозиції"]},
 "R99_UNMOTIVATED": {
  "name": "неконкретне рішення",
  "trigger": r"не\s+конкретизован|загальн\w+\s+посиланн|без\s+обґрунтуванн",
  "q": ["не конкретизовано у чому полягає невідповідність",
        "рішення про відхилення не містить конкретної вимоги документації",
        "загальне посилання на невідповідність тендерній документації",
        "відсутнє належне обґрунтування рішення про відхилення"]},
 "_counter": ["скаржник не довів обставини на які посилається",
              "рішення замовника є достатньо мотивованим"]},
}

#: §6.1 — базові фільтри Clarity для оскарження ВЛАСНОГО відхилення.
DEFAULT_FILTERS_REJECTION = {
    "джерело": "Prozorro",
    "статус скарги": "вирішені; відхилені",
    "предмет оскарження": "результат кваліфікації",
    "класифікація": "",                       # заповнюється з фактичного типу
    "дата": "спочатку актуальна практика, далі обережне розширення",
    "ЄДРПОУ замовника": "",                   # тільки для окремого проходу
}

#: §6.3 — фактичний тип закупівлі за CPV.
def procurement_class(cpv: str, title: str = "") -> str:
    """роботи / послуги / товари. Береться з CPV, назва — лише підказка."""
    code = re.sub(r"\D", "", str(cpv or ""))[:2]
    t = (title or "").lower()
    if re.search(r"проєктуванн|проектуванн|технічн\w+\s+нагляд|авторськ\w+\s+нагляд"
                 r"|інженерн\w+[- ]консультац", t):
        return "послуги"
    if code == "45":
        return "роботи"
    if code in {"71", "72", "79", "80", "85", "90", "98", "50", "60", "63", "66", "73"}:
        return "послуги"
    if code:
        return "товари"
    return "не встановлено"


# ============================================================================
#  БЛОК 28. РОЗБІР ПРЕТЕНЗІЙ І ПОШУКОВИЙ НАРЯД
# ----------------------------------------------------------------------------
#  Тут — уся логіка; словник у блоці 27, тут його тільки читають.
#
#  Що робить:
#    1. ріже рішення на ОКРЕМІ претензії (§4: не весь протокол одним рядком);
#    2. для кожної повертає ВСІ релевантні коди й підтеми (§11.1 multi-label),
#       а не перший збіг;
#    3. витягує норми, документ, дефект, сектор;
#    4. будує пошуковий наряд для РУЧНОГО пошуку в Clarity (§2: не звертатися).
#
#  Правило 5 діє повністю: нічого не встановлене — пишемо «не встановлено».
#  §14 словника: жодних автоматичних висновків про перспективу.
# ============================================================================

#: §14 — фрази, які скрипт не має права писати. Перевіряється тестом.
FORBIDDEN_CONCLUSIONS = [
    "завжди стає на бік", "% шансів", "автоматично означає високу",
    "обов'язковим прецедентом", "гарантує результат",
]

# --- норми ------------------------------------------------------------------
CITATION_PATTERNS = [
    ("стаття",      r"(?:ст\.|стат(?:тя|ті|тею|тi))\s*(\d+(?:[-–]\d+)?)"),
    ("частина",     r"(?:ч\.|частин(?:а|и|ою|і))\s*(\d+)"),
    ("пункт",       r"(?<![пП])(?:п\.|пункт(?:у|ом|і|и|ів)?)\s*(\d+(?:\.\d+)*)"),
    ("підпункт",    r"(?:пп\.|підпункт(?:у|ом|і|и)?)\s*([\d.]+|[а-яїієґ]\))"),
    ("додаток",     r"[Дд]одатк?(?:ом|ок|ку|ка|у|и|ів)\s*№?\s*(\d+)"),
    ("розділ",      r"[Рр]озділ(?:у|і|ом)?\s*([IVXLІ]+|\d+)"),
    ("таблиця",     r"[Тт]аблиц(?:я|і|ю|ею)\s*№?\s*(\d+)"),
    ("Особливості", r"(?:п\.\s*)?(\d+)\s*Особливост"),
    ("ПКМУ",        r"№\s*(\d{2,4})"),
    ("закон",       r"(Закон\w*\s+України\s+«[^»]{5,90}»)"),
    ("ДБН",         r"(ДБН\s*[А-ЯA-Z]?\.?[\d.\-]+)"),
    ("ДСТУ",        r"(ДСТУ\s*[\w\-.:]+)"),
    ("наказ",       r"наказ\w*[^.;]{0,40}№\s*(\d{2,4})"),
]

#: Посилання, які шукають У САМІЙ тендерній документації. Стаття закону чи
#: ПКМУ в ТД не міститься, тому в рядок «де шукати вимогу» вони не йдуть.
TD_CITATION_KINDS = {"пункт", "підпункт", "розділ", "додаток", "таблиця"}

#: Документи, назви яких мають юридичне значення. Керований словник:
#: впізнаємо те, що вже описали, а не «витягуємо слова» (правило 5).
DOC_VOCAB = {
    "довідка": r"довідк\w+",
    "лист-відгук": r"лист[- ]?відгук\w*|відгук\w*",
    "аналогічний договір": r"аналогічн\w+\s+договор\w*|аналогічн\w+\s+догов\w+",
    "договір": r"(?<!аналогічний )договор\w+|договір",
    "акт виконаних робіт": r"акт\w*\s+(?:виконан|прийман)\w*",
    "сертифікат": r"сертифікат\w*",
    "ліцензія": r"ліцензі\w+|ліцензія",
    "дозвіл": r"дозвол\w+|дозвіл",
    "декларація": r"деклараці\w+",
    "банківська гарантія": r"(?:банківськ\w+\s+)?гаранті\w+",
    "локальний кошторис": r"(?:локальн\w+\s+)?кошторис\w*",
    "договірна ціна": r"договірн\w+\s+цін\w+",
    "відомість обсягів робіт": r"відомост\w+\s+обсяг\w+|\bВОР\b",
    "дефектний акт": r"дефектн\w+\s+акт\w*",
    "технічне завдання": r"технічн\w+\s+завданн\w+",
    "технічна специфікація": r"технічн\w+\s+специфікаці\w+",
    "календарний графік": r"календарн\w+\s+графік\w*",
    "трудова книжка": r"трудов\w+\s+книжк\w+",
    "наказ": r"наказ\w*",
    "штатний розпис": r"штатн\w+\s+розпис\w*",
    "диплом": r"диплом\w*",
    "довіреність": r"довіреніст\w*|довіренност\w+",
    "фінансова звітність": r"фінансов\w+\s+звітніст\w*|звітніст\w+",
    "технічний паспорт": r"технічн\w+\s+паспорт\w*",
    "гарантійний лист": r"гарантійн\w+\s+лист\w*",
    "переклад": r"переклад\w*",
}

#: Дефект — що саме, за словами замовника, не так.
DEFECT_VOCAB = {
    "не надано": r"не\s+нада(?:в|но|ла|ли)\w*|ненаданн\w*|не\s+пода(?:в|но|ла|ли)\w*"
                 r"|не\s+заванта\w+",
    "відсутній": r"відсутн\w+",
    "не відповідає": r"не\s+відповіда\w+|невідповідніст\w+|не\s+у\s+відповідност",
    "не містить": r"не\s+містить|не\s+зазначен\w+|не\s+вказан\w+",
    "не підтверджено": r"не\s+підтверд\w+|не\s+довед\w+",
    "недостовірно": r"недостовірн\w+|неправдив\w+",
    "не усунуто за 24 год": r"не\s+усун\w+|не\s+виправ\w+",
    "сплив строк": r"сплив|закінчивс\w+|проміну\w+|пропущен\w+\s+строк",
    "менше ніж вимагалось": r"менш\w+\s+(?:ніж|за|від)|коротш\w+\s+(?:ніж|за)"
                            r"|нижч\w+\s+(?:ніж|за)",
    "більше ніж дозволено": r"більш\w+\s+(?:ніж|за)|перевищ\w+",
    "не заповнено": r"не\s+заповнен\w*|порожн\w+\s+граф",
    "порушено вимогу": r"порушен\w+\s+вимог|чим\s+порушен",
}


@dataclass
class Claim:
    """Одна окрема претензія замовника. Сира цитата не змінюється НІКОЛИ."""
    claim_id: str
    quote: str
    citations: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    defects: list = field(default_factory=list)
    codes: list = field(default_factory=list)        # [(code, subtopic_id, name)]
    cross: list = field(default_factory=list)        # X01..X07
    sectors: list = field(default_factory=list)
    exact_query: str = ""
    exact_confidence: str = "LOW"                    # HIGH | LOW
    sector_confirmed: bool = False                   # сектор видно в ТЕКСТІ
    notes: list = field(default_factory=list)


def _cite_text(kind: str, value: str) -> str:
    short = {"стаття": "ст.", "частина": "ч.", "пункт": "п.", "підпункт": "пп.",
             "додаток": "Додаток", "розділ": "Розділ", "таблиця": "Таблиця",
             "Особливості": "п. %s Особливостей", "ПКМУ": "ПКМУ № %s",
             "наказ": "наказ № %s"}
    if kind in ("закон", "ДБН", "ДСТУ"):
        return value
    tpl = short.get(kind, kind + " ")
    return (tpl % value) if "%s" in tpl else f"{tpl} {value}"


#: Ознака, що речення взагалі містить претензію, а не цитату норми.
_DEFECT_ANY = (r"не\s+нада|не\s+пода|не\s+заванта|відсутн|не\s+відповіда"
               r"|не\s+містить|не\s+підтверд|недостовірн|не\s+усун|не\s+виправ"
               r"|менш\w+\s+ніж|більш\w+\s+ніж|сплив|не\s+зазначен|не\s+підписан"
               r"|порушен\w+|невідповідн")


def _sentences(text: str) -> list[str]:
    """Речення з урахуванням скорочень: «п. 5» не є кінцем речення."""
    mark = "\x00"
    t = re.sub(r"\b([а-яїієґa-z]{1,3})\.", lambda m: m.group(1) + mark, text)
    t = re.sub(r"(\d)\.(?=\d)", lambda m: m.group(1) + mark, t)
    out = []
    for part in re.split(r"(?<=[.;])\s+(?=[А-ЯІЇЄҐ])", t):
        s = part.replace(mark, ".").strip()
        if s:
            out.append(s)
    return out


def split_protocol(text: str, legacy: Optional[Any] = None,
                   max_claims: int = 25) -> list[str]:
    """
    Ріже рішення на окремі претензії. Спершу пробуємо розділювач монолiта
    (він уже виміряний на реальних протоколах), інакше — власний.
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return []
    parts: list[str] = []
    if legacy is not None and hasattr(legacy, "split_claims"):
        try:
            parts = list(legacy.split_claims(raw, max_claims=max_claims))
        except Exception:                                           # noqa: BLE001
            parts = []
    if parts:
        # ПЕРЕВІРКА ПОКРИТТЯ. Розділювач монолiта відсіює речення за власними
        # фільтрами і може викинути цілу підставу (перевірено: речення про
        # строк банківської гарантії зникало повністю). Втрата підстави —
        # найгірший дефект у цьому модулі: правило 32 забороняє висновок,
        # доки не розібрано ВСІ підстави. Тому дописуємо загублене.
        covered = " ".join(parts).lower()
        for sent in _sentences(raw):
            if len(sent) < 25:
                continue
            if not re.search(_DEFECT_ANY, sent, re.I):
                continue
            head = re.sub(r"\W+", " ", sent.lower())[:60].strip()
            if head and head not in re.sub(r"\W+", " ", covered):
                parts.append(sent)
        return parts[:max_claims]
    # Запасний шлях: крапка перед великою літерою, нумерація, марковані списки.
    mark = "\x00"
    protected = re.sub(r"\b([а-яїієґa-z]{1,3})\.",
                       lambda m: m.group(1) + mark, raw)
    protected = re.sub(r"(\d)\.(?=\d)", lambda m: m.group(1) + mark, protected)
    parts = re.split(r"(?<=[.;])\s+(?=[А-ЯІЇЄҐ])|\s+(?=\d{1,2}\)\s)|\s*[•‒–—]\s+",
                     protected)
    out, seen = [], set()
    for p in parts:
        s = p.replace(mark, ".").strip()
        if len(s) < 25 or len(s) > 900:
            continue
        key = s.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_claims:
            break
    return out or [raw[:900]]


#: Сполучники і вставні слова: у пошуковому запиті вони лише шум.
_LINKING_WORDS = {
    "крім", "того", "також", "оскільки", "чим", "що", "які", "який", "яка",
    "проте", "однак", "разом", "тим", "при", "цьому", "тобто", "саме", "адже",
    "було", "буде", "мають", "має", "був", "були", "тощо", "тому", "через",
    "для", "від", "над", "під", "про", "але", "або", "чи", "як", "так",
    "протягом", "зокрема", "вище", "нижче", "надано", "надана", "наданий",
}


def _strip_noise(text: str) -> str:
    """§7.4 — прибирає шаблонні звороти, сторони, ЄДРПОУ, UA-ID і дати."""
    t = " " + re.sub(r"\s+", " ", text).strip().lower() + " "
    for phrase in CLARITY_NOISE_PHRASES:
        t = t.replace(phrase, " ")
    t = re.sub(r"ua-\d{4}-\d{2}-\d{2}-\d{6}-[a-z]", " ", t)
    t = re.sub(r"\b\d{8,10}\b", " ", t)                      # ЄДРПОУ/ІПН
    # §7.2: назви сторін у запит не йдуть — інакше пошук знайде лише цю саму
    # справу. Ріжемо організаційну форму разом із назвою в лапках або великими.
    t = re.sub(r"[«»\"'']", " ", t)                  # лапки прибираємо ДО назв
    t = re.sub(r"\b(?:тзов|тов|пп|фоп|дп|кп|пат|прат|ат|нкп|кнп|тдв)\b"
               r"(?:\s+[\wʼ’\-]+){0,4}", " ", t)
    t = re.sub(r"\b(?:єдрпоу|ідентифікаційн\w+|код)\b", " ", t)
    t = re.sub(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b", " ", t)
    t = re.sub(r"[«»\"'()]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def build_exact_query(claim_text: str, documents: list,
                      defects: list) -> tuple[str, str]:
    """
    §11.3 — дефект + документ + характеристика, 4–9 змістовних слів.

    Повертає (запит, впевненість). LOW означає, що двох змістовних
    складників виділити не вдалося — за §11.3 таке йде на ручну перевірку.
    """
    cleaned = _strip_noise(claim_text)
    words = [w for w in re.findall(r"[\w'’\-]+", cleaned)
             if len(w) > 2 and w not in CLARITY_NOISE_TOKENS
             and w not in _LINKING_WORDS]
    # структурні слова лишаємо тільки поруч зі змістовною назвою
    keep = []
    for i, w in enumerate(words):
        if w in CLARITY_STRUCT_TOKENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if not nxt or nxt.isdigit():
                continue
        keep.append(w)
    have = (1 if documents else 0) + (1 if defects else 0)
    if documents and defects:
        # §11.3: дія або дефект + назва документа + конкретна характеристика.
        # Два документи в одній претензії — це і є характеристика:
        # «не надав акти виконаних робіт за аналогічним договором».
        head = [defects[0]] + list(documents[:2])
        # Дедуплікація за основою слова: «банківська гарантія» і
        # «банківської гарантії» — одне й те саме, у запиті двічі не потрібні.
        stems = {w[:5] for part in head for w in part.split() if len(w) > 2}
        tail = []
        for w in keep:
            if len(w) < 5 or w[:5] in stems:
                continue
            if re.search(r"(?:ув|ла|ли|ло|ти|ся)$", w):   # дієслівні залишки
                continue
            stems.add(w[:5])
            tail.append(w)
            if len(tail) >= 2:
                break
        core = " ".join(head + tail).strip()
    else:
        core = " ".join(keep[:9]).strip()
    if not core:
        return "не встановлено", "LOW"
    words = core.split()
    if len(words) > 9:
        core = " ".join(words[:9])
    return core[:120], ("HIGH" if have >= 2 and len(words) >= 4 else "LOW")


def analyze_claim(text: str, claim_id: str, cpv: str = "",
                  facts: Optional[dict] = None) -> Claim:
    """Повний розбір ОДНІЄЇ претензії. Multi-label, з пріоритетом (§11.2)."""
    facts = facts or {}
    c = Claim(claim_id=claim_id, quote=re.sub(r"\s+", " ", text).strip())
    low = c.quote

    seen = set()
    for kind, rx in CITATION_PATTERNS:
        for m in re.finditer(rx, low):
            val = m.group(1)
            if (kind, val) in seen:
                continue
            seen.add((kind, val))
            c.citations.append({"вид": kind, "значення": val,
                                "як": _cite_text(kind, val)})
    found = []
    for k, rx in DOC_VOCAB.items():
        m = re.search(rx, low, re.I)
        if m:
            found.append((m.start(), k))
    c.documents = [k for _, k in sorted(found)]
    if "аналогічний договір" in c.documents and "договір" in c.documents:
        c.documents.remove("договір")            # точніша назва поглинає загальну
    c.defects = [k for k, rx in DEFECT_VOCAB.items() if re.search(rx, low, re.I)]

    # --- multi-label: ВСІ теми, що спрацювали, а не перша (§11.1) -----------
    hits = []
    for code, subs in CLARITY_SUBTOPICS.items():
        for sid, d in subs.items():
            if sid == "_counter":
                continue
            if re.search(d["trigger"], low, re.I):
                hits.append((CODE_PRIORITY.get(code, 9), code, sid, d["name"]))
    # R04 звужено навмисно: цитата норми про 24 години — не претензія (§11.2 п.1)
    has_24 = re.search(r"24\s*(?:-х|-ти)?\s*годин|усуненн\w+\s+невідповідн", low, re.I)
    real_24 = re.search(
        r"не\s+усун|не\s+виправ|не\s+надав\w*\s+можлив|не\s+розміщ|повторно\s+відхил"
        r"|(?<!не )усунув|(?<!не )виправив|не\s+підляга", low, re.I)
    if any(h[1] == "R04" for h in hits) and not real_24:
        hits = [h for h in hits if h[1] != "R04"]
        c.notes.append("R04 знято: у тексті лише цитата норми про 24 години")
    elif has_24 and real_24 and not any(h[1] == "R04" for h in hits):
        # Замовник каже «учасник не усунув». Позиція КЛІЄНТА тут — чи була
        # взагалі надана можливість і чи було повідомлення конкретним.
        # Це вибір напряму пошуку, а не юридичний висновок.
        for sid in ("R04_NO_NOTICE", "R04_UNCLEAR_NOTICE"):
            hits.append((CODE_PRIORITY["R04"], "R04", sid,
                         CLARITY_SUBTOPICS["R04"][sid]["name"]))
        c.notes.append("замовник стверджує, що невідповідність не усунуто — "
                       "перевіряти зміст і конкретність повідомлення")
    if hits:
        best = min(h[0] for h in hits)
        # R10/R99 не поглинають точніші теми, але й не зникають, якщо вони єдині
        hits = [h for h in hits if h[0] <= max(best, 6)] or hits
    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    c.codes = [(code, sid, name) for _, code, sid, name in hits]
    if not c.codes:
        c.codes = [("R99", "R99_UNMOTIVATED", "тему не встановлено")]
        c.notes.append("REQUIRES_MANUAL_TOPIC — точнішу тему визначити вручну")

    # --- наскрізні теми ----------------------------------------------------
    for xid, rx in CROSS_CUTTING_TRIGGERS.items():
        if re.search(rx, low, re.I):
            c.cross.append(xid)
    if not c.citations:
        # Відсутність посилання на пункт ТД — це встановлений факт, не здогад.
        c.cross.append("X01_MOTIVATION")
        c.notes.append("у претензії немає посилання на конкретний пункт "
                       "документації — аргумент про мотивованість рішення")
    if facts.get("same_reason_others"):
        c.cross.append("X03_EQUAL_TREATMENT")
    c.cross = sorted(set(c.cross))

    for sid, rx in SECTOR_TRIGGERS.items():
        if re.search(rx, low, re.I):
            c.sectors.append(sid)
    # Сектор з CPV — лише підказка, у пошуковий запит він не йде: §10 забороняє
    # додавати кошторисну лексику до спору про гарантію чи мову документа.
    c.sector_confirmed = bool(c.sectors)
    if not c.sectors:
        pcl = procurement_class(cpv)
        if pcl == "роботи":
            c.sectors.append("construction_execution")

    c.exact_query, c.exact_confidence = build_exact_query(
        c.quote, c.documents, c.defects)
    if c.exact_confidence == "LOW":
        c.notes.append("LOW_CONFIDENCE_EXACT_QUERY — дослівний запит перевірити")
    return c


def analyze_protocol(text: str, cpv: str = "", legacy: Optional[Any] = None,
                     facts: Optional[dict] = None) -> list[Claim]:
    """Рішення -> список розібраних претензій C01, C02, …"""
    parts = split_protocol(text, legacy=legacy)
    return [analyze_claim(p, f"C{i:02d}", cpv=cpv, facts=facts)
            for i, p in enumerate(parts, 1)]


# --- пошуковий наряд --------------------------------------------------------
MAX_QUERIES_PER_CLAIM = 8          # §7.5
MAX_QUERIES_PER_TENDER = 20        # §7.5


@dataclass
class SearchRow:
    """Один рядок пошукового наряду. Нічого нікуди не надсилає."""
    claim_id: str
    reason_code: str
    subtopic_id: str
    priority: int
    query_type: str        # exact_fact|topic|procedure|amcu_formula|counter|sector|buyer
    polarity: str          # neutral|participant|buyer
    query_text: str
    filters: dict
    why: str
    source_fragment: str
    manual_action: str = "відібрати рішення по суті й перевірити першоджерело"


def _norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", str(q or "").lower()).strip()


def _filters_for(claim: "Claim", pcl: str, buyer_edrpou: str = "",
                 by_buyer: bool = False) -> dict:
    """§6 — рекомендовані значення фільтрів. Скрипт їх не натискає."""
    f = dict(DEFAULT_FILTERS_REJECTION)
    f["класифікація"] = pcl or "не встановлено"
    if by_buyer:
        f["ЄДРПОУ замовника"] = buyer_edrpou or "не встановлено"
        f["примітка"] = "окремий додатковий прохід (§6.5), не основний"
    else:
        f.pop("ЄДРПОУ замовника", None)
    if any(code == "R13" for code, _, _ in claim.codes):
        f["предмет оскарження"] = "визначення переможця"
    return f


def build_search_order(claims: list, *, cpv: str = "", title: str = "",
                       buyer_edrpou: str = "") -> list:
    """
    §11 — пошуковий наряд для РУЧНОЇ роботи в Clarity APP.

    Обов'язково для кожної претензії: нейтральний запит, запит на користь
    учасника і КОНТРПОШУК на користь замовника (§7.5, §15 п. 5).
    Жодного звернення до Clarity — тільки текст (§2).
    """
    pcl = procurement_class(cpv, title)
    per_claim: list[list] = []
    seen_global: set[str] = set()

    for claim in claims:
        local: list[SearchRow] = []
        base = _filters_for(claim, pcl, buyer_edrpou)
        code, sid, _ = claim.codes[0]
        prio = CODE_PRIORITY.get(code, 9)
        frag = claim.quote[:180]

        def add(qtype, polarity, text, why, filters=None, c=code, s=sid,
                p=prio, fr=frag, cid=claim.claim_id):
            key = _norm_q(text)
            if not key or key == "не встановлено":
                return
            if key in seen_global or any(_norm_q(r.query_text) == key for r in local):
                return
            if len(local) >= MAX_QUERIES_PER_CLAIM:
                return
            local.append(SearchRow(cid, c, s, p, qtype, polarity, text,
                                   filters or base, why, fr))

        # 1. дослівний факт
        add("exact_fact", "neutral", claim.exact_query,
            "та сама фактична підстава відхилення"
            + ("" if claim.exact_confidence == "HIGH"
               else " · LOW_CONFIDENCE — перевірити формулювання"))

        # 2. тематичні запити з підтем (по одному з кожної, до трьох)
        for code_i, sid_i, name_i in claim.codes[:3]:
            qs = CLARITY_SUBTOPICS.get(code_i, {}).get(sid_i, {}).get("q", [])
            if qs:
                add("topic", "neutral", qs[0], f"підтема: {name_i}",
                    c=code_i, s=sid_i, p=CODE_PRIORITY.get(code_i, 9))

        # 3. процедурні / наскрізні теми
        for xid in claim.cross[:2]:
            phrases = CROSS_CUTTING_TOPICS.get(xid, [])
            if phrases:
                add("procedure", "participant", phrases[0],
                    f"наскрізна тема {xid}")

        # 4. формула на користь учасника + ключовий об'єкт спору
        obj = (claim.documents[0] if claim.documents else "")
        if obj:
            add("amcu_formula", "participant",
                f"{obj} {PARTICIPANT_FORMULAS[8]}",
                "практика, де документ визнали поданим")
        else:
            add("amcu_formula", "participant", PARTICIPANT_FORMULAS[0],
                "практика, де замовник не довів підставу")

        # 5. КОНТРПОШУК — обов'язковий (§7.5, §15 п. 5)
        counters = CLARITY_SUBTOPICS.get(code, {}).get("_counter") or \
            BUYER_COUNTER_FORMULAS
        add("counter", "buyer", counters[0],
            "протилежна практика: чим відповість замовник")
        # Контрпошук піднімаємо на друге місце: якщо бюджет закупівлі
        # обріже хвіст, обидві сторони питання все одно лишаться в наряді.
        for i, r in enumerate(local):
            if r.query_type == "counter" and i > 1:
                local.insert(1, local.pop(i))
                break

        # 6. галузевий варіант — ЛИШЕ якщо сектор підтверджений текстом (§10),
        #    і лише тим терміном, який у цьому тексті справді є.
        if claim.sector_confirmed:
            low_q = claim.quote.lower()
            for sec in claim.sectors[:1]:
                hit = next((t for t in SECTOR_TERMS.get(sec, [])
                            if t.split()[0][:6].lower() in low_q), None)
                if hit and obj and hit not in obj:
                    add("sector", "neutral", f"{hit} {obj}",
                        f"галузевий контекст: {sec}")

        # 7. один найсильніший запит із фільтром за замовником (§6.5).
        #    Текст той самий, що в exact_fact — це ІНШИЙ прохід з іншими
        #    фільтрами, тому дедуплікація за текстом його зняти не має.
        if buyer_edrpou and claim.exact_query != "не встановлено" \
                and len(local) < MAX_QUERIES_PER_CLAIM:
            local.append(SearchRow(
                claim.claim_id, code, sid, prio, "buyer", "neutral",
                claim.exact_query,
                _filters_for(claim, pcl, buyer_edrpou, by_buyer=True),
                "окремий прохід: поведінка цього замовника (§6.5)", frag))

        for r in local:
            seen_global.add(_norm_q(r.query_text))
        per_claim.append(local)

    # Д-36: ліміт 20 запитів на закупівлю, застосований «хто перший», з'їдав
    # останні підстави цілком — у справі з чотирма підставами четверта не
    # отримувала жодного запиту. Це та сама втрата підстави, що й у розборі.
    # Тому бюджет розподіляється ПО КОЛУ: спершу кожна претензія отримує
    # обов'язковий мінімум (§15 п. 5), і тільки потім добираємо решту.
    rows = []
    depth = 0
    while len(rows) < MAX_QUERIES_PER_TENDER and any(
            len(lst) > depth for lst in per_claim):
        for lst in per_claim:
            if depth < len(lst) and len(rows) < MAX_QUERIES_PER_TENDER:
                rows.append(lst[depth])
        depth += 1
    return rows


def keywords_line(claims: list, rows: list, minimum: int = 22,
                  maximum: int = 26) -> str:
    """
    Рядок «Ключові слова для Clarity APP» у картці.

    Це не хмара слів: це впорядкований набір готових коротких запитів
    від найточнішого до найзагальнішого, як вимагає картка.
    """
    out, seen = [], set()

    def push(x):
        k = _norm_q(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)

    for r in rows:                     # спершу все, що вже в наряді
        push(r.query_text)
    for claim in claims:               # далі — решта фраз тем
        for code, sid, _ in claim.codes:
            for q in CLARITY_SUBTOPICS.get(code, {}).get(sid, {}).get("q", []):
                push(q)
        for xid in claim.cross:
            for q in CROSS_CUTTING_TOPICS.get(xid, []):
                push(q)
        # §10: галузеві слова — лише за ПІДТВЕРДЖЕНИМ текстом сектором.
        # Інакше до спору про довідку чіплялося «дозвіл Держпраці».
        if claim.sector_confirmed:
            low_q = claim.quote.lower()
            for sec in claim.sectors:
                for t in SECTOR_TERMS.get(sec, []):
                    if t.split()[0][:6].lower() in low_q and claim.documents:
                        push(f"{t} {claim.documents[0]}")
    for f in PARTICIPANT_FORMULAS:     # добираємо до мінімуму загальними
        push(f)
    for f in BUYER_COUNTER_FORMULAS:
        push(f)
    if len(out) < minimum:
        out.append(f"(зібрано {len(out)} із {minimum} — доповнити вручну)")
    # Нумеруємо: суцільний рядок із 26 фраз очима не читається, а людині
    # треба брати їх по черзі й нести в Clarity.
    return "\n".join(f"{i}. {q}" for i, q in enumerate(out[:maximum], 1))


def search_order_markdown(rows: list, claims: list, ua_id: str) -> str:
    """Файл для копіювання запитів у Clarity вручну."""
    pol = {"neutral": "нейтральний", "participant": "за учасника",
           "buyer": "за замовника"}
    lines = [f"# ПОШУКОВИЙ НАРЯД CLARITY — {ua_id}", "",
             "Запити копіювати в Clarity APP **вручну**. Скрипт до Clarity "
             "не звертається (§2 словника, ліцензійний договір).", ""]
    by_claim: dict = {}
    for r in rows:
        by_claim.setdefault(r.claim_id, []).append(r)
    for claim in claims:
        rs = by_claim.get(claim.claim_id, [])
        lines += [f"## {claim.claim_id} — "
                  + ", ".join(f"{c}/{s}" for c, s, _ in claim.codes), "",
                  "> " + claim.quote, ""]
        if not rs:
            lines += ["_запитів не сформовано_", ""]
            continue
        lines += ["| № | Тип | Полярність | Запит | Навіщо |",
                  "|---:|---|---|---|---|"]
        for i, r in enumerate(rs, 1):
            lines.append(f"| {i} | {r.query_type} | {pol.get(r.polarity, r.polarity)} "
                         f"| `{r.query_text}` | {r.why} |")
        f = rs[0].filters
        lines += ["", "**Фільтри:** "
                  + " · ".join(f"{k}: {v}" for k, v in f.items() if v), ""]
    lines += ["---", "",
              "Після відбору: перевірити рішення в Prozorro/АМКУ, прочитати "
              "мотивувальну і резолютивну частини, звірити редакцію норми на "
              "дату закупівлі. Кількість знайдених рішень не є оцінкою "
              "перспективи скарги."]
    return "\n".join(lines)


# ============================================================================
#  БЛОК 29. ДОКУМЕНТИ СПРАВИ ПОРУЧ ІЗ КАРТКОЮ
# ----------------------------------------------------------------------------
#  Гіперпосилання у Word не може керувати тим, куди браузер збереже файл —
#  це налаштування браузера, а не документа. Тому файли кладуться в теку
#  справи ЗАЗДАЛЕГІДЬ, а картка посилається на локальний файл. Зберігати
#  нічого не треба: воно вже там.
#
#  Правило 17: кожен знайдений документ лишається видимим, навіть якщо його
#  не вдалося завантажити. Правило 44: повторний запуск не качає вдруге.
# ============================================================================
#: Одна тека на справу. Картка, запити і ВСІ документи лежать разом —
#: людина відкриває теку закупівлі й бачить усе, що стосується справи.
#: Підпапку для документів прибрано: картка опинялась рівнем вище, і її
#: там просто не знаходили.
#: Друга таблиця з пошуковими запитами Clarity. Вимкнена рішенням власника
#: продукту; словник і генератор лишаються в коді (блоки 27-28).
CLARITY_KEYWORDS_IN_CARD = False

#: Чи формувати картку, у якій знайдено НЕкритичну суперечність даних.
#: За замовчуванням — ні: картці, що суперечить сама собі, вірити не можна.
ALLOW_CARD_WITH_CONTRADICTION = False

CARD_PREFIX = "00_KARTKA_"
QUERIES_PREFIX = "00_ZAPYTY_CLARITY_"
MANIFEST_NAME = "_manifest.json"

#: Канонічний архів справ. Документи закупівлі лежать ТУТ і тільки тут.
#:
#: Чому не в теці прогону: маніфест «цей документ уже завантажено» лежить
#: поруч із документами. Коли тека справи опинялась усередині теки прогону,
#: кожен новий прогін бачив порожній маніфест і качав УСЕ заново — час
#: прогону ріс лінійно з кожним днем роботи (звіт 26).
ARCHIVE_DIR = "_arhiv"

#: Покажчик прогону: що саме знайшов цей запуск і де воно лежить.
RUN_INDEX_NAME = "00_SPRAVY_PROHONU.docx"

#: Маркер разової міграції розкладки. Обхід усіх тек справ на Google Диску
#: коштує сотні мережевих звернень; робити його щопрогону немає причини.
MIGRATION_MARKER = "_rozkladka_perenesena.json"

#: Стара розкладка — щоб перенести наявні справи, а не загубити їх.
LEGACY_DOC_SUBDIR = "dokumenty"
LEGACY_CARD_PREFIX = "kartka_"
LEGACY_QUERIES_PREFIX = "zapyty_"
#: Один файл. NotebookLM приймає до 200 МБ на джерело; більший файл
#: там однаково не відкриється. Раніше межа була 25 МБ, і скановані
#: кошториси та креслення МОВЧКИ не потрапляли в справу.
MAX_DOC_BYTES = 150 * 1024 * 1024
#: Уся тека справи. Перевищення не ховається: документ лишається в
#: маніфесті зі статусом ПРОПУЩЕНО_ЛІМІТ.
MAX_CASE_BYTES = 1024 * 1024 * 1024
#: Ліміту КІЛЬКОСТІ документів немає: раніше межа 250 різала пропозицію
#: великої будівельної закупівлі, бо вона йшла після ТД (правило 17).

#: Формати, які NotebookLM читає сам, і ті, що перетворює LibreOffice.
#: Визначені нижче, у блоці 34, за офіційним переліком Google.
NOTEBOOKLM_OK: set = set()
CONVERTIBLE: set = set()

PDF_COPY_SUFFIX = "__PDF-копія.pdf"


def _pdf_copy_path(path: str) -> str:
    """
    Імʼя PDF-копії ЗБЕРІГАЄ розширення оригіналу.

    Було: «Кошторис.xlsx» і «Кошторис.docx» -> обидва «Кошторис__PDF-копія.pdf».
    Другий файл мовчки отримував статус «ВЖЕ_БУЛА», а в справі лежала копія
    ЧУЖОГО документа. У будівельних закупівлях пара .xlsx/.docx з однаковою
    назвою — звичайна річ, тому це була втрата доказу, а не косметика
    (правила 5 і 14).
    """
    stem, ext = os.path.splitext(path)
    return f"{stem}{ext}{PDF_COPY_SUFFIX}"


def _soffice_bin() -> Optional[str]:
    """Шлях до LibreOffice або None. Перевіряємо, а не припускаємо."""
    import shutil as _sh                                            # noqa: PLC0415
    for name in ("soffice", "libreoffice", "soffice.bin"):
        found = _sh.which(name)
        if found:
            return found
    return None


MAX_COL_WIDTH = 70          # символів; ширше — і сторінка стає нечитабельною


def _prepare_spreadsheet(path: str, work_dir: str) -> Optional[str]:
    """
    Копія книги з розширеними колонками і посторінковим вписуванням.
    Значення комірок НЕ змінюються — лише ширина і параметри друку.
    """
    if os.path.splitext(path)[1].lower() not in (".xlsx", ".xlsm"):
        return None
    try:
        import openpyxl                                             # noqa: PLC0415
        from openpyxl.utils import get_column_letter                # noqa: PLC0415
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(path)
    except Exception:                                               # noqa: BLE001
        return None                     # пошкоджена книга — хай пробує LibreOffice
    try:
        for ws in wb.worksheets:
            widths: dict = {}
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    longest = max((len(part) for part in
                                   str(cell.value).split("\n")), default=0)
                    col = cell.column_letter
                    if longest > widths.get(col, 0):
                        widths[col] = longest
            for col, width in widths.items():
                ws.column_dimensions[col].width = min(MAX_COL_WIDTH, width + 2)
            ws.page_setup.orientation = "landscape"
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr.fitToPage = True
        out = os.path.join(work_dir, "prepared_" + os.path.basename(path))
        wb.save(out)
        return out
    except Exception:                                               # noqa: BLE001
        return None


def convert_to_pdf(path: str, timeout_s: int = 180) -> tuple[Optional[str], str]:
    """
    Точна PDF-копія файла поруч з оригіналом.

    Конвертує LibreOffice: він зберігає структуру таблиць і ЗНАЧЕННЯ комірок,
    а не переказує їх. Це критично — у кошторисах кількісні показники і є
    предметом спору (правило 19).

    Повертає (шлях_до_pdf | None, статус).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in NOTEBOOKLM_OK:
        return None, "НЕ_ПОТРІБНО"
    if ext not in CONVERTIBLE:
        return None, "ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ"
    target = _pdf_copy_path(path)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target, "ВЖЕ_БУЛА"
    binary = _soffice_bin()
    if not binary:
        return None, "НЕМАЄ_LIBREOFFICE"

    import subprocess                                               # noqa: PLC0415
    import tempfile as _tf                                          # noqa: PLC0415
    import shutil as _sh                                            # noqa: PLC0415
    with _tf.TemporaryDirectory() as work:
        # Таблиці друкуються по ВИДИМІЙ ширині колонки: вузька колонка ріже
        # текст, і в PDF замість «Розбирання покриття підлоги» лишається
        # «Розбиранн». Для NotebookLM це втрата даних, тому перед
        # конвертацією розширюємо колонки в ТИМЧАСОВІЙ копії.
        # Оригінал не чіпаємо ніколи (правило 14).
        source = _prepare_spreadsheet(path, work) or path
        # Профіль у тимчасовій теці: без нього паралельні запуски LibreOffice
        # чіпляються за один профіль і мовчки нічого не роблять.
        cmd = [binary, "--headless", "--norestore",
               f"-env:UserInstallation=file://{work}/profile",
               "--convert-to", "pdf", "--outdir", work, source]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None, "ТАЙМАУТ_КОНВЕРТАЦІЇ"
        except OSError as exc:
            return None, f"ПОМИЛКА_ЗАПУСКУ:{exc}"
        made = [f for f in os.listdir(work) if f.lower().endswith(".pdf")]
        if not made:
            tail = (res.stderr or b"")[-200:].decode("utf-8", "replace")
            return None, f"КОНВЕРТАЦІЯ_НЕ_ВДАЛАСЬ:{tail.strip()[:120]}"
        try:
            _sh.move(os.path.join(work, made[0]), target)
        except OSError as exc:
            return None, f"НЕ_ЗАПИСАНО:{exc}"
    return target, "СТВОРЕНО"


def convert_many_to_pdf(paths, timeout_s: int = 600) -> dict:
    """
    PDF-копії для пачки файлів ОДНИМ запуском LibreOffice.

    Запуск LibreOffice коштує ~1,5 с сам по собі — це майже вся вартість
    конвертації невеликого документа. На теці з 30 файлів окремі запуски
    дають ~50 с, один пакетний — ~8 с.

    Повертає {шлях: (шлях_до_pdf | None, статус)} для КОЖНОГО вхідного шляху.
    Якщо пакет упав або дав менше файлів, ніж очікувалось, — решта
    доробляється поодинці. Тиші не буде: статус повертається на кожен файл.
    """
    out: dict = {}
    todo: list = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in NOTEBOOKLM_OK:
            out[path] = (None, "НЕ_ПОТРІБНО")
        elif ext not in CONVERTIBLE:
            out[path] = (None, "ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ")
        else:
            target = _pdf_copy_path(path)
            if os.path.exists(target) and os.path.getsize(target) > 0:
                out[path] = (target, "ВЖЕ_БУЛА")
            else:
                todo.append(path)
    if not todo:
        return out
    binary = _soffice_bin()
    if not binary:
        for path in todo:
            out[path] = (None, "НЕМАЄ_LIBREOFFICE")
        return out

    import subprocess                                                # noqa: PLC0415
    import tempfile as _tf                                           # noqa: PLC0415
    import shutil as _sh                                             # noqa: PLC0415
    with _tf.TemporaryDirectory() as work:
        src_dir = os.path.join(work, "vhid")
        os.makedirs(src_dir, exist_ok=True)
        # Імена в роботі — порядкові: два документи різних справ можуть
        # називатись однаково, і LibreOffice затер би один одним.
        plan = []
        for i, path in enumerate(todo):
            ext = os.path.splitext(path)[1].lower()
            prepared = _prepare_spreadsheet(path, work) or path
            staged = os.path.join(src_dir, f"{i:04d}{ext}")
            try:
                _sh.copy2(prepared, staged)
            except OSError as exc:
                out[path] = (None, f"НЕ_ПРОЧИТАНО:{exc}")
                continue
            plan.append((path, staged, os.path.join(work, f"{i:04d}.pdf")))
        if plan:
            cmd = [binary, "--headless", "--norestore",
                   f"-env:UserInstallation=file://{work}/profile",
                   "--convert-to", "pdf", "--outdir", work]
            cmd += [staged for _, staged, _ in plan]
            try:
                subprocess.run(cmd, capture_output=True, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                pass                       # недороблене доб'ємо поодинці нижче
            except OSError as exc:
                for path, _, _ in plan:
                    out[path] = (None, f"ПОМИЛКА_ЗАПУСКУ:{exc}")
                return out
            for path, _, made in plan:
                if path in out:
                    continue
                if os.path.exists(made) and os.path.getsize(made) > 0:
                    target = _pdf_copy_path(path)
                    try:
                        _sh.move(made, target)
                        out[path] = (target, "СТВОРЕНО")
                    except OSError as exc:
                        out[path] = (None, f"НЕ_ЗАПИСАНО:{exc}")
    # Те, що пакет не подужав, робимо поодинці — щоб не втратити копію мовчки.
    for path in todo:
        if path not in out:
            out[path] = convert_to_pdf(path)
    return out


def _safe_name(title: str, fallback: str = "dokument") -> str:
    name = re.sub(r"[^\w\d.\-() ]+", "_", str(title or "").strip())
    name = re.sub(r"_+", "_", name).strip("_ ") or fallback
    return name[:110]


def _guess_ext(title: str, head: bytes) -> str:
    if os.path.splitext(title)[1]:
        return ""
    if head[:4] == b"%PDF":
        return ".pdf"
    if head[:2] == b"PK":
        return ".docx"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return ".doc"
    return ""


#: Скільки документів тягнемо одночасно. Було 8 — сервер документів
#: Prozorro під таким навантаженням відповідає 429, а старе завантаження
#: без повторів записувало це як «не завантажено». Чотири — з запасом.
DOC_WORKERS = 4

#: Скільки відхилень розбираємо одночасно. Стільки ж, скільки вже
#: використовує сканер Prozorro у legacy-модулі: більше без потреби
#: навантажує чужий сервер і ловить 429.
SCAN_WORKERS = 8


def case_archive(root_dir: str, ua_id: str) -> str:
    """
    Канонічна тека справи: <root>/spravy/_arhiv/<UA-ID>/.

    Тут лежать документи, маніфест і службова картка — разом, в одному місці,
    незалежно від того, скільки разів прогін торкався цієї закупівлі.
    """
    return os.path.join(root_dir, "spravy", ARCHIVE_DIR,
                        re.sub(r"[^\w\-.]", "_", str(ua_id or "bez_ua_id")))


def _rejection_filter(rejection_ids) -> tuple:
    """
    (шматок SQL, аргументи) для вибірки лише справ цього прогону.

    None -> без фільтра, усі справи в базі (ручна перебудова).
    []   -> ЖОДНОЇ справи. Це різні речі: у день, коли відхилень не знайшлось,
            порожній список не має означати «а тоді качай усю історію».
    """
    if rejection_ids is None:
        return "", ()
    ids = [x for x in rejection_ids if x]
    if not ids:
        return " WHERE 1 = 0", ()
    marks = ",".join("?" for _ in ids)
    return f" WHERE r.rejection_id IN ({marks})", tuple(ids)


#: Скільки неповних справ доліковувати за один прогін. Обмеження свідоме:
#: прогін має тривати передбачувано, а не «скільки треба».
HEAL_PER_RUN = 40


#: Скільки разів доліковуємо одну справу, перш ніж відкласти її для
#: людини. Невдача 5 прогонів поспіль — це вже не мережа, а щось інше.
MAX_CASE_ATTEMPTS = 5


def _case_is_complete(root_dir: str, ua_id: str) -> bool:
    """Чи є в теці справи і картка, і маніфест. Лише для діагностики."""
    folder = case_archive(root_dir, ua_id)
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return (any(n.startswith(CARD_PREFIX) for n in names)
            and MANIFEST_NAME in names)


def cases_needing_attention(db_path: str, root_dir: str, touched,
                            limit: int = HEAL_PER_RUN) -> tuple:
    """
    Справи ЦЬОГО прогону плюс ті, у яких документи ще не доведені до кінця.

    Стан береться з БАЗИ (docs_state), а не з вигляду теки: раніше справа
    з карткою і маніфестом вважалась повною, навіть якщо половина файлів
    не завантажилась, — і більше ніколи не доробилась. Обхід Диска тут не
    потрібен, тому перевірка не дорожчає з ростом архіву.

    Повертає (список rejection_id, скільки лишилось поза межею).
    """
    ids = list(dict.fromkeys(x for x in (touched or []) if x))
    db = Db(db_path)
    try:
        rows = db.q(
            "SELECT r.rejection_id FROM rejections r"
            "  LEFT JOIN leads l ON l.rejection_id = r.rejection_id"
            " WHERE COALESCE(l.status,'') <> 'SUPPRESSED'"
            "   AND (r.docs_state IS NULL"
            "        OR r.docs_state IN ('INCOMPLETE','EMPTY_LISTS'))"
            "   AND COALESCE(r.docs_attempts,0) < ?"
            " ORDER BY r.discovered_at DESC", (MAX_CASE_ATTEMPTS,))
    finally:
        db.close()
    nepovni = [r["rejection_id"] for r in rows if r["rejection_id"] not in ids]
    dobrano = nepovni[:max(0, limit)]
    return ids + dobrano, max(0, len(nepovni) - len(dobrano))

# ============================================================================
#  БЛОК 34. ДОКУМЕНТИ СПРАВИ: ЗАВАНТАЖЕННЯ І КОПІЇ ДЛЯ NOTEBOOKLM
# ----------------------------------------------------------------------------
#  Жоден документ не зникає мовчки (правило 17): або він у теці, або в
#  маніфесті є рядок із причиною — HTTP_404, ЗАВЕЛИКИЙ, НЕМАЄ_ПОСИЛАННЯ…
# ============================================================================

#: Невдачі, які варто пробувати знову наступним прогоном.
RETRYABLE_FAILURES = {"ПОМИЛКА_МЕРЕЖІ", "НЕ_ЗАВАНТАЖЕНО", "HTTP_429",
                      "HTTP_5XX", "ТАЙМАУТ", "НЕ_ЗАПИСАНО"}
#: Невдачі, які повтором не виправити. Їх не мучимо щопрогону.
PERMANENT_FAILURES = {"HTTP_403", "HTTP_404", "HTTP_410", "ЗАВЕЛИКИЙ",
                      "НЕМАЄ_ПОСИЛАННЯ"}
#: Скільки прогонів пробуємо один документ, перш ніж здатися.
MAX_DOC_ATTEMPTS = 5
#: Спроби в межах одного прогону і паузи між ними, секунди.
HTTP_TRIES = 4
HTTP_BACKOFF = (2, 5, 12, 30)


def _read_manifest(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


#: Як часто зберігати маніфест посеред завантаження справи.
MANIFEST_FLUSH_EVERY = 25


def _write_manifest(path: str, manifest: dict) -> None:
    """Запис через тимчасовий файл: обрив не лишає напівзаписаний JSON."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        # Мовчати не можна: без маніфесту наступний прогін не знатиме,
        # що вже завантажено, і качатиме повторно.
        print(f"   ! маніфест не записано ({os.path.basename(os.path.dirname(path))}):"
              f" {exc}")


def _free_name(folder: str, fname: str, sha256: str) -> str:
    """
    Імʼя, під яким файл ляже в теку, НІКОЛИ не затираючи інший файл.

    Такий самий вміст (той самий SHA-256) — те саме імʼя: це повтор після
    обриву, дубль не потрібен. Інший вміст — «(2)», «(3)» перед розширенням.
    """
    target = os.path.join(folder, fname)
    if not os.path.exists(target) or _sha256_file(target) == sha256:
        return fname
    stem, ext = os.path.splitext(fname)
    n = 2
    while True:
        cand = f"{stem} ({n}){ext}"
        path = os.path.join(folder, cand)
        if not os.path.exists(path) or _sha256_file(path) == sha256:
            return cand
        n += 1


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _manifest_entry(item: dict, group: str, status: str, prev: dict,
                    **fields) -> dict:
    """
    Рядок маніфесту. Правило 16: ід документа, дати публікації і зміни,
    джерело, SHA-256, коли отримано — усе поруч із файлом.
    """
    entry = {
        "назва": item.get("назва") or prev.get("назва") or "",
        "група": group,
        "статус": status,
        "файл": fields.pop("файл", "") or "",
        "байтів": int(fields.pop("байтів", 0) or 0),
        "джерело": item.get("url") or "",
        "document_id": item.get("document_id") or prev.get("document_id") or "",
        "опубліковано": item.get("опубліковано") or "",
        "змінено": item.get("змінено") or "",
    }
    if item.get("конверт"):
        entry["конверт"] = item["конверт"]
    if item.get("підпис"):
        entry["підпис"] = True
    for k, v in fields.items():
        if v not in (None, ""):
            entry[k] = v
    return entry


def _wanted_documents(r) -> list:
    """
    Усі документи справи в порядку ВАЖЛИВОСТІ, без повторів.

    Порядок має значення лише для черги завантаження: якщо тека впреться
    в межу розміру, першим має лягти рішення і пропозиція, а не 200 МБ
    креслень ТД. Префікс у назві файла (01, 02, 03 …) — окремо від порядку.
    """
    out, seen, titles = [], set(), {}

    def add(group, item):
        key = _doc_key(item)
        if key in seen:
            return
        seen.add(key)
        # Дві редакції того самого документа мають відрізнятись ДАТОЮ, а не
        # лише порядковим номером: інакше незрозуміло, яка діяла на дату
        # подання пропозиції (правило 16). Оригінальний запис не чіпаємо.
        title = str(item.get("назва") or "документ")
        tkey = (group, _base_title(title).lower())
        titles[tkey] = titles.get(tkey, 0) + 1
        date = str(item.get("опубліковано") or item.get("змінено") or "")[:10]
        if titles[tkey] > 1:
            marker = f"ред. {date}" if date else f"редакція {titles[tkey]}"
            item = dict(item, назва_файла=_with_marker(title, marker))
        elif _VERSION_MARK.search(title):
            # старий запис «Документ.pdf (редакція 2)» — лагодимо розширення
            item = dict(item, назва_файла=_with_marker(
                title, _VERSION_MARK.search(title).group(0).strip(" ()")))
        out.append((group, item))

    if r["protocol_url"]:
        add("01_rishennia", {"назва": r["protocol_title"] or "Протокол",
                             "url": r["protocol_url"]})
    if r["notice_24h_url"]:
        add("02_vymoga_24h", {"назва": r["notice_24h_title"] or "Вимога 24 години",
                              "url": r["notice_24h_url"]})
    for group, col in (("01_rishennia", "award_documents"),
                       ("04_propozycia", "bid_documents"),
                       ("03_TD", "td_documents"),
                       ("05_lystuvannia", "qa_documents")):
        try:
            docs = json.loads(r[col] or "[]")
        except (ValueError, TypeError):
            docs = []
        for d in docs:
            if isinstance(d, dict):
                add(group, d)
    return out


def fetch_document(legacy, session, url: str, max_bytes: int,
                   tmp_dir: str) -> dict:
    """
    Завантажує ОДИН документ у тимчасовий файл.

    Повертає {"статус", "файл", "байтів", "sha256", "http", "деталі"}.
    Статуси розрізняються (правило 13): HTTP_403, HTTP_404, HTTP_429,
    HTTP_5XX, ТАЙМАУТ, ПОМИЛКА_МЕРЕЖІ, ЗАВЕЛИКИЙ, ЗАВАНТАЖЕНО.

    Справжня сесія requests -> потокове завантаження з повторами й
    урахуванням Retry-After. Інше (підставна мережа тестів) -> legacy.get_bytes.
    """
    if hasattr(session, "get"):
        return _fetch_http(session, url, max_bytes, tmp_dir)
    return _fetch_legacy(legacy, session, url, max_bytes, tmp_dir)


def _fetch_legacy(legacy, session, url, max_bytes, tmp_dir) -> dict:
    import tempfile as _tf                                           # noqa: PLC0415
    try:
        data = legacy.get_bytes(session, url)
    except Exception as exc:                                        # noqa: BLE001
        return {"статус": "ПОМИЛКА_МЕРЕЖІ", "деталі": str(exc)[:200]}
    if not data:
        return {"статус": "НЕ_ЗАВАНТАЖЕНО",
                "деталі": "мережа не віддала файл (причину legacy не повідомляє)"}
    if len(data) > max_bytes:
        return {"статус": "ЗАВЕЛИКИЙ", "байтів": len(data),
                "деталі": f"більше {max_bytes // 2**20} МБ"}
    fd, path = _tf.mkstemp(dir=tmp_dir)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return {"статус": "ЗАВАНТАЖЕНО", "файл": path, "байтів": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "http": 200}


def _fetch_http(session, url, max_bytes, tmp_dir) -> dict:
    import tempfile as _tf                                           # noqa: PLC0415
    last = {"статус": "ПОМИЛКА_МЕРЕЖІ", "деталі": "не було жодної спроби"}
    for attempt in range(HTTP_TRIES):
        wait = HTTP_BACKOFF[min(attempt, len(HTTP_BACKOFF) - 1)]
        try:
            with session.get(url, stream=True, timeout=(15, 120)) as resp:
                code = int(getattr(resp, "status_code", 0) or 0)
                if code == 200:
                    h, n = hashlib.sha256(), 0
                    fd, path = _tf.mkstemp(dir=tmp_dir)
                    too_big = False
                    with os.fdopen(fd, "wb") as fh:
                        for chunk in resp.iter_content(1 << 20):
                            if not chunk:
                                continue
                            n += len(chunk)
                            if n > max_bytes:
                                too_big = True
                                break
                            h.update(chunk)
                            fh.write(chunk)
                    if too_big:
                        _safe_remove(path)
                        return {"статус": "ЗАВЕЛИКИЙ", "байтів": n, "http": 200,
                                "деталі": f"більше {max_bytes // 2**20} МБ"}
                    return {"статус": "ЗАВАНТАЖЕНО", "файл": path, "байтів": n,
                            "sha256": h.hexdigest(), "http": 200}
                if code in (403, 404, 410):
                    return {"статус": f"HTTP_{code}", "http": code,
                            "деталі": {403: "доступ заборонено",
                                       404: "документа за посиланням немає",
                                       410: "документ видалено"}[code]}
                if code == 429 or code >= 500:
                    ra = str((getattr(resp, "headers", {}) or {})
                             .get("Retry-After") or "")
                    if ra.isdigit():
                        wait = min(int(ra), 60)
                    last = {"статус": "HTTP_429" if code == 429 else "HTTP_5XX",
                            "http": code,
                            "деталі": f"сервер відповів {code} після "
                                      f"{attempt + 1} спроб"}
                else:
                    return {"статус": f"HTTP_{code}", "http": code,
                            "деталі": f"несподівана відповідь {code}"}
        except Exception as exc:                                    # noqa: BLE001
            name = type(exc).__name__
            if "Timeout" in name:
                last = {"статус": "ТАЙМАУТ", "деталі": f"{name} після "
                                                       f"{attempt + 1} спроб"}
            else:
                last = {"статус": "ПОМИЛКА_МЕРЕЖІ",
                        "деталі": f"{name}: {str(exc)[:160]}"}
        if attempt < HTTP_TRIES - 1:
            time.sleep(wait)
    return last


# --- NotebookLM -----------------------------------------------------------
#: Що NotebookLM читає САМ. Звірено з офіційною сторінкою Google
#: support.google.com/notebooklm/answer/16215270 (20.09.2026):
#: PDF, TXT, MD, DOCX, CSV, PPTX, ePub, зображення, аудіо.
#: НЕ читає: XLSX/XLS (лише Google Sheets), DOC, RTF, ODT/ODS, ZIP/RAR.
#: Ліміт — 200 МБ і 500 000 слів на одне джерело.
NOTEBOOKLM_ACCEPTS = {
    ".pdf", ".txt", ".md", ".csv", ".docx", ".pptx", ".epub",
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".jp2", ".ico",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg",
}
#: Що LibreOffice перетворює на PDF зі збереженням таблиць і значень.
LIBREOFFICE_TO_PDF = {".doc", ".dot", ".dotx", ".rtf", ".odt", ".xls", ".xlsx",
                      ".xlsm", ".ods", ".ppt", ".pps", ".odp", ".htm", ".html"}
#: Архіви — розпаковуємо, вміст конвертуємо.
ARCHIVE_EXT = {".zip", ".rar", ".7z"}
#: Контейнери підпису КЕП. Усередині може лежати сам документ.
SIGNATURE_EXT = {".p7s", ".p7m"}
#: Текст без розмітки для людини — копія .txt.
TEXT_COPY_EXT = {".xml", ".json"}
#: Статуси, коли копію зробити не вдалося — їх рахуємо як проблему.
NOTEBOOKLM_FAILED = {"КОНВЕРТАЦІЯ_НЕ_ВДАЛАСЬ", "НЕМАЄ_LIBREOFFICE",
                     "НЕ_РОЗПАКОВАНО", "НЕМАЄ_РОЗПАКОВУВАЧА",
                     "ТАЙМАУТ_КОНВЕРТАЦІЇ", "ПІДОЗРІЛИЙ_АРХІВ"}

#: Межі для архівів: захист від «архівної бомби» і від шляхів на кшталт
#: ../../ у назвах файлів усередині (zip-slip).
ARCHIVE_MAX_FILES = 3000
ARCHIVE_MAX_BYTES = 1024 * 1024 * 1024
ARCHIVE_MAX_DEPTH = 2
UNPACKED_SUFFIX = "__rozpakovano"

# Старі імена лишаються — ними користуються convert_to_pdf і картка.
NOTEBOOKLM_OK |= NOTEBOOKLM_ACCEPTS
CONVERTIBLE |= LIBREOFFICE_TO_PDF


def make_notebooklm_copies(paths, depth: int = 0) -> dict:
    """
    Для кожного файла — те, що зможе прочитати NotebookLM.

    Повертає {шлях: {"статус": …, "файли": [створені копії]}}.
      НЕ_ПОТРІБНО         — NotebookLM читає файл сам;
      СТВОРЕНО            — поруч лежить PDF/TXT-копія;
      РОЗПАКОВАНО         — архів розкрито, вміст теж оброблено;
      ВИТЯГНУТО           — з підпису КЕП дістали вкладений документ;
      ПІДПИС_БЕЗ_ДОКУМЕНТА — окремий підпис, документа всередині немає;
      ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ — напр. креслення .dwg: лишається оригінал.
    Жоден вхідний шлях не лишається без статусу.
    """
    out: dict = {}
    to_pdf: list = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in NOTEBOOKLM_ACCEPTS:
            out[path] = {"статус": "НЕ_ПОТРІБНО", "файли": []}
        elif ext in LIBREOFFICE_TO_PDF:
            to_pdf.append(path)
        elif ext in TEXT_COPY_EXT:
            out[path] = _text_copy(path)
        elif ext in ARCHIVE_EXT:
            out[path] = _expand_archive(path, depth)
        elif ext in SIGNATURE_EXT:
            out[path] = _unwrap_signature(path, depth)
        else:
            out[path] = {"статус": "ФОРМАТ_НЕ_КОНВЕРТУЄТЬСЯ", "файли": []}
    if to_pdf:
        for path, (pdf, status) in convert_many_to_pdf(to_pdf).items():
            ok = status in ("СТВОРЕНО", "ВЖЕ_БУЛА")
            out[path] = {"статус": "СТВОРЕНО" if ok else status,
                         "файли": [pdf] if pdf else []}
    return out


def _text_copy(path: str) -> dict:
    target = f"{path}__tekst.txt"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"статус": "СТВОРЕНО", "файли": [target]}
    except OSError as exc:
        return {"статус": "КОНВЕРТАЦІЯ_НЕ_ВДАЛАСЬ", "файли": [],
                "деталі": str(exc)[:200]}


def _zip_member_name(info) -> str:
    """Імʼя файла в ZIP. Архіви з українського Windows пишуть імена в CP866."""
    name = info.filename
    if not (info.flag_bits & 0x800):             # без прапорця UTF-8
        try:
            name = name.encode("cp437").decode("cp866")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _safe_member_path(root: str, name: str) -> Optional[str]:
    """Шлях усередині теки розпакування або None, якщо назва тікає назовні."""
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    parts = [_safe_name(p, "fail") for p in parts]
    target = os.path.normpath(os.path.join(root, *parts))
    if not target.startswith(os.path.normpath(root) + os.sep):
        return None
    return target


def _expand_archive(path: str, depth: int) -> dict:
    """Розпаковує ZIP (стандартною бібліотекою) або RAR/7Z (зовнішнім 7z)."""
    if depth >= ARCHIVE_MAX_DEPTH:
        return {"статус": "НЕ_РОЗПАКОВАНО", "файли": [],
                "деталі": "архів в архіві глибше двох рівнів"}
    dest = f"{os.path.splitext(path)[0]}{UNPACKED_SUFFIX}"
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".zip":
            extracted = _unzip(path, dest)
        else:
            extracted = _unpack_external(path, dest)
    except _ArchiveProblem as exc:
        return {"статус": exc.status, "файли": [], "деталі": exc.detail}
    # Вміст архіву — такі самі документи: конвертуємо, розпаковуємо далі.
    inner = make_notebooklm_copies(extracted, depth + 1)
    made = list(extracted)
    for info in inner.values():
        made += info.get("файли", [])
    return {"статус": "РОЗПАКОВАНО", "файли": made,
            "деталі": f"файлів у архіві: {len(extracted)}"}


class _ArchiveProblem(Exception):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _unzip(path: str, dest: str) -> list:
    import zipfile                                                   # noqa: PLC0415
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _ArchiveProblem("НЕ_РОЗПАКОВАНО", f"пошкоджений ZIP: {exc}") from exc
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > ARCHIVE_MAX_FILES:
            raise _ArchiveProblem("ПІДОЗРІЛИЙ_АРХІВ",
                                  f"{len(members)} файлів — більше межі")
        if sum(m.file_size for m in members) > ARCHIVE_MAX_BYTES:
            raise _ArchiveProblem("ПІДОЗРІЛИЙ_АРХІВ",
                                  "розпакований обсяг більший за 1 ГБ")
        out = []
        for m in members:
            if m.flag_bits & 0x1:
                raise _ArchiveProblem("НЕ_РОЗПАКОВАНО", "архів під паролем")
            target = _safe_member_path(dest, _zip_member_name(m))
            if target is None:
                continue                  # шлях тікав назовні — не пишемо
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
            out.append(target)
        return out


def _unpack_external(path: str, dest: str) -> list:
    """RAR і 7Z — через 7z, якщо він є. Немає — кажемо, що встановити."""
    import shutil as _sh                                             # noqa: PLC0415
    import subprocess                                                # noqa: PLC0415
    import tempfile as _tf                                           # noqa: PLC0415
    tool = _sh.which("7z") or _sh.which("7za")
    if not tool:
        raise _ArchiveProblem(
            "НЕМАЄ_РОЗПАКОВУВАЧА",
            "немає 7z. У Colab: !apt-get -qq install -y p7zip-full p7zip-rar")
    work = _tf.mkdtemp(prefix="tw_arc_")
    try:
        res = subprocess.run([tool, "x", "-y", "-p", f"-o{work}", path],
                             capture_output=True, timeout=600)
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or b"")[-200:].decode(
                "utf-8", "replace")
            why = ("архів під паролем" if "password" in tail.lower()
                   else f"7z: {tail.strip()[:150]}")
            raise _ArchiveProblem("НЕ_РОЗПАКОВАНО", why)
        out, total = [], 0
        for base, _, files in os.walk(work):
            for f in files:
                src = os.path.join(base, f)
                if os.path.islink(src):
                    continue              # посилання з архіву назовні — ні
                rel = os.path.relpath(src, work)
                target = _safe_member_path(dest, rel)
                if target is None:
                    continue
                total += os.path.getsize(src)
                if len(out) >= ARCHIVE_MAX_FILES or total > ARCHIVE_MAX_BYTES:
                    raise _ArchiveProblem("ПІДОЗРІЛИЙ_АРХІВ",
                                          "архів більший за межі розпакування")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                _sh.move(src, target)
                out.append(target)
        return out
    except subprocess.TimeoutExpired as exc:
        raise _ArchiveProblem("НЕ_РОЗПАКОВАНО", "7z не впорався за 10 хв") from exc
    finally:
        _sh.rmtree(work, ignore_errors=True)


def _der_read(buf: bytes, pos: int) -> tuple:
    """Один елемент DER/BER: (тег, початок_вмісту, кінець_вмісту, наступний)."""
    tag = buf[pos]
    pos += 1
    if tag & 0x1F == 0x1F:                       # довгий тег
        while buf[pos] & 0x80:
            pos += 1
        pos += 1
    first = buf[pos]
    pos += 1
    if first == 0x80:                            # невизначена довжина (BER)
        start, depth, p = pos, 0, pos
        while True:
            if buf[p] == 0 and buf[p + 1] == 0 and depth == 0:
                return tag, start, p, p + 2
            t, s, e, nxt = _der_read(buf, p)
            p = nxt
    if first & 0x80:
        n = first & 0x7F
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    else:
        length = first
    return tag, pos, pos + length, pos + length


def _der_children(buf: bytes, start: int, end: int) -> list:
    out, p = [], start
    while p < end:
        if buf[p] == 0 and p + 1 < end and buf[p + 1] == 0:
            break
        t, s, e, nxt = _der_read(buf, p)
        out.append((t, s, e))
        p = nxt
    return out


def _octets(buf: bytes, tag: int, s: int, e: int) -> bytes:
    """OCTET STRING — простий або складений із частин (BER)."""
    if tag == 0x04:
        return buf[s:e]
    if tag == 0x24:
        return b"".join(_octets(buf, t, cs, ce)
                        for t, cs, ce in _der_children(buf, s, e))
    return b""


def extract_signed_content(raw: bytes) -> Optional[bytes]:
    """
    Вміст, вкладений у підпис CMS/PKCS#7 (КЕП із «приєднаним» документом).

    Підпис НЕ перевіряється — лише дістається документ, щоб його можна
    було прочитати. None — якщо це окремий підпис без вкладеного документа
    або файл не є контейнером CMS.
    """
    try:
        if raw[:5] == b"-----":                   # PEM -> DER
            import base64 as _b64                                   # noqa: PLC0415
            body = b"".join(line for line in raw.splitlines()
                            if line and not line.startswith(b"-----"))
            raw = _b64.b64decode(body)
        tag, s, e, _ = _der_read(raw, 0)          # ContentInfo
        if tag != 0x30:
            return None
        kids = _der_children(raw, s, e)
        if len(kids) < 2:
            return None
        t0, s0, e0 = kids[1]                       # [0] EXPLICIT SignedData
        sd_tag, sd_s, sd_e, _ = _der_read(raw, s0)
        sd = _der_children(raw, sd_s, sd_e)
        # version, digestAlgorithms, encapContentInfo, …
        if len(sd) < 3:
            return None
        et, es, ee = sd[2]
        eci = _der_children(raw, es, ee)
        if len(eci) < 2:
            return None                            # немає eContent — окремий підпис
        ct, cs, ce = eci[1]                        # [0] EXPLICIT eContent
        inner = _der_children(raw, cs, ce)
        if not inner:
            return None
        it, is_, ie = inner[0]
        data = _octets(raw, it, is_, ie)
        return data or None
    except (IndexError, ValueError):
        return None


def _unwrap_signature(path: str, depth: int) -> dict:
    """«Документ.pdf.p7s» -> «Документ.pdf» поруч, і далі як звичайний файл."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return {"статус": "КОНВЕРТАЦІЯ_НЕ_ВДАЛАСЬ", "файли": [],
                "деталі": str(exc)[:200]}
    data = extract_signed_content(raw)
    if not data:
        return {"статус": "ПІДПИС_БЕЗ_ДОКУМЕНТА", "файли": [],
                "деталі": "окремий підпис КЕП — сам документ лежить окремим файлом"}
    stem = os.path.splitext(path)[0]              # «…Документ.pdf»
    if not os.path.splitext(stem)[1]:
        stem += _guess_ext(stem, data[:8]) or ".bin"
    target = stem
    if os.path.exists(target):                    # не затираємо наявний файл
        base, ext = os.path.splitext(stem)
        target = f"{base}__z_pidpysu{ext}"
    with open(target, "wb") as fh:
        fh.write(data)
    made = [target]
    inner = make_notebooklm_copies([target], depth + 1)
    for info in inner.values():
        made += info.get("файли", [])
    return {"статус": "ВИТЯГНУТО", "файли": made,
            "деталі": "документ дістано з підпису КЕП (підпис не перевірявся)"}


def download_case_documents(db_path: str, root_dir: str,
                            legacy: Optional[Any] = None,
                            only_new: bool = True,
                            make_pdf_copies: bool = True,
                            case_dir: Optional[str] = None,
                            rejection_ids=None) -> dict:
    """
    Кладе ВСІ документи справи в архів: <root>/spravy/_arhiv/<UA-ID>/.

      * рішення замовника і вимога 24 год;
      * УСЯ пропозиція учасника — усі чотири конверти Prozorro;
      * УСЯ тендерна документація з усіма змінами;
      * листування із замовником (запитання, вимоги, скарги).

    Жодного ліміту кількості. Документ, який скачати не вдалося, лишається
    в маніфесті зі статусом і причиною (правило 17). Порядок завантаження —
    за важливістю: рішення → 24 год → пропозиція → ТД → листування.

    rejection_ids — лише ці справи. None = усі справи бази (ручна перебудова).
    case_dir — лише для сумісності; документи завжди йдуть в архів.
    """
    if legacy is None:
        try:
            legacy = importlib.import_module(LEGACY_MODULE)
        except Exception:                                           # noqa: BLE001
            legacy = None
    if legacy is None or not hasattr(legacy, "make_session"):
        return {"завантажено": 0, "пропущено": 0,
                "помилок": 0, "причина": "немає модуля мережі"}

    migrate_case_layout(root_dir)
    db = Db(db_path)
    stats = {"завантажено": 0, "вже_були": 0, "помилок": 0, "завеликі": 0,
             "без_посилання": 0, "справ": 0, "pdf_копій": 0,
             "pdf_не_вдалось": 0, "копій_для_notebooklm": 0,
             "групи": {}, "теки": []}
    if make_pdf_copies and _soffice_bin() is None:
        print("   ! LibreOffice не знайдено — копій .xlsx/.doc для NotebookLM не буде.")
        print("     Встановити в Colab: !apt-get -qq install -y "
              "libreoffice-writer libreoffice-calc")
    import shutil as _sh                                             # noqa: PLC0415
    import tempfile as _tf                                           # noqa: PLC0415
    tmp_dir = _tf.mkdtemp(prefix="tw_dl_")
    try:
        where, args = _rejection_filter(rejection_ids)
        rows = db.q(
            "SELECT r.rejection_id, t.ua_id, c.edrpou_norm,"
            "       r.protocol_url, r.protocol_title,"
            "       r.notice_24h_url, r.notice_24h_title,"
            "       r.td_documents, r.bid_documents, r.award_documents,"
            "       r.qa_documents, r.qa_text"
            "  FROM rejections r"
            "  JOIN tenders t ON t.tender_id = r.tender_id"
            "  JOIN companies c ON c.company_id = r.company_id" + where, args)
        if not rows:
            return stats
        session = legacy.make_session()
        repo = Repository(db, Audit(db, new_run_id()))
        for r in rows:
            folder = case_archive(root_dir, r["ua_id"])
            os.makedirs(folder, exist_ok=True)
            stats["справ"] += 1
            if folder not in stats["теки"]:
                stats["теки"].append(folder)

            wanted = _wanted_documents(r)

            # Листування із замовником — текстом: запитання і відповіді в
            # Prozorro не документи, а поля, і без них картина неповна.
            if r["qa_text"]:
                try:
                    with open(os.path.join(
                            folder, "05_lystuvannia_00_zapytannya_ta_vidpovidi.txt"),
                            "w", encoding="utf-8") as fh:
                        fh.write("ЗАПИТАННЯ, ВИМОГИ ТА СКАРГИ ДО ЗАМОВНИКА\n"
                                 "джерело: Prozorro API, дослівно\n"
                                 + "=" * 70 + "\n\n" + r["qa_text"] + "\n")
                except OSError:
                    pass

            manifest_path = os.path.join(folder, MANIFEST_NAME)
            manifest = _read_manifest(manifest_path)
            total = sum(int(v.get("байтів") or 0) for v in manifest.values()
                        if v.get("статус") == "ЗАВАНТАЖЕНО")

            # 1) план: що тягнути. Уже завантажене з наявним файлом — не чіпаємо;
            #    безнадійне (403/404, завеликий) — не мучимо щопрогону.
            plan = []
            for i, (group, item) in enumerate(wanted, 1):
                key = _doc_key(item)
                prev = manifest.get(key) or {}
                g = stats["групи"].setdefault(group, {"очікувалось": 0, "є": 0,
                                                      "не_вдалось": 0})
                g["очікувалось"] += 1
                if only_new and prev.get("статус") == "ЗАВАНТАЖЕНО" \
                        and os.path.exists(os.path.join(folder, prev.get("файл", ""))):
                    stats["вже_були"] += 1
                    g["є"] += 1
                    continue
                if not item.get("url"):
                    manifest[key] = _manifest_entry(item, group, "НЕМАЄ_ПОСИЛАННЯ",
                                                    prev, деталі=(
                        "у публічному API немає посилання — найчастіше "
                        "конфіденційний документ; скачати неможливо"))
                    stats["без_посилання"] += 1
                    g["не_вдалось"] += 1
                    continue
                if prev.get("статус") in PERMANENT_FAILURES and only_new:
                    g["не_вдалось"] += 1
                    continue
                if int(prev.get("спроб") or 0) >= MAX_DOC_ATTEMPTS:
                    g["не_вдалось"] += 1
                    continue
                plan.append((i, group, item, key, prev))

            # 2) мережа — паралельно, у ЛОКАЛЬНУ тимчасову теку: запис
            #    незавершених файлів на Google Диск — це зайва синхронізація.
            results: dict = {}
            if plan:
                def _job(p):
                    return p[3], fetch_document(legacy, session, p[2]["url"],
                                                MAX_DOC_BYTES, tmp_dir)
                with ThreadPoolExecutor(max_workers=min(DOC_WORKERS,
                                                        len(plan))) as pool:
                    for key, res in pool.map(_job, plan):
                        results[key] = res

            # 3) запис у теку справи і маніфест — послідовно (бюджет теки).
            fresh: list = []
            for i, group, item, key, prev in plan:
                res = results.get(key) or {"статус": "ПОМИЛКА_МЕРЕЖІ",
                                           "деталі": "не запитано"}
                g = stats["групи"][group]
                attempts = int(prev.get("спроб") or 0) + 1
                if res["статус"] != "ЗАВАНТАЖЕНО":
                    manifest[key] = _manifest_entry(
                        item, group, res["статус"], prev, спроб=attempts,
                        http=res.get("http"), деталі=res.get("деталі", ""),
                        байтів=res.get("байтів", 0))
                    g["не_вдалось"] += 1
                    if res["статус"] == "ЗАВЕЛИКИЙ":
                        stats["завеликі"] += 1
                    elif res["статус"] in PERMANENT_FAILURES:
                        # 403/404: документа за посиланням у Prozorro немає
                        # або доступ закрито — повтор нічого не дасть.
                        stats["недоступні"] = stats.get("недоступні", 0) + 1
                    else:
                        stats["помилок"] += 1
                    continue
                size = int(res["байтів"])
                if total + size > MAX_CASE_BYTES:
                    _safe_remove(res["файл"])
                    manifest[key] = _manifest_entry(
                        item, group, "ПРОПУЩЕНО_ЛІМІТ", prev, спроб=attempts,
                        байтів=size, деталі=(
                            f"тека справи перевищила б {MAX_CASE_BYTES // 2**20} МБ"))
                    stats["завеликі"] += 1
                    g["не_вдалось"] += 1
                    continue
                with open(res["файл"], "rb") as fh:
                    head = fh.read(8)
                fname = (f"{group}_{i:03d}_"
                         f"{_safe_name(item.get('назва_файла') or item.get('назва'))}")
                fname += _guess_ext(fname, head)
                fname = _free_name(folder, fname, res["sha256"])
                target = os.path.join(folder, fname)
                try:
                    if os.path.exists(target):
                        # Той самий вміст уже лежить (обрив після запису
                        # файла, але до запису маніфесту) — не дублюємо.
                        _safe_remove(res["файл"])
                    else:
                        _sh.move(res["файл"], target)
                except OSError as exc:
                    _safe_remove(res["файл"])
                    manifest[key] = _manifest_entry(
                        item, group, "НЕ_ЗАПИСАНО", prev, спроб=attempts,
                        деталі=str(exc)[:200])
                    stats["помилок"] += 1
                    g["не_вдалось"] += 1
                    continue
                total += size
                stats["завантажено"] += 1
                g["є"] += 1
                manifest[key] = _manifest_entry(
                    item, group, "ЗАВАНТАЖЕНО", prev, спроб=attempts,
                    файл=fname, байтів=size, sha256=res["sha256"],
                    http=res.get("http"), коли=now_iso())
                fresh.append((key, target))
                if len(fresh) % MANIFEST_FLUSH_EVERY == 0:
                    # Обрив Colab посеред великої справи не має губити
                    # облік того, що вже лежить на Диску.
                    _write_manifest(manifest_path, manifest)

            # 4) копії для NotebookLM — усе, чого він не читає сам.
            if make_pdf_copies and fresh:
                copies = make_notebooklm_copies([p for _, p in fresh])
                for key, path in fresh:
                    info = copies.get(path) or {}
                    made = [os.path.relpath(x, folder)
                            for x in info.get("файли", [])]
                    manifest[key]["notebooklm"] = info.get("статус", "")
                    manifest[key]["notebooklm_файли"] = made
                    if info.get("деталі"):
                        manifest[key]["notebooklm_деталі"] = info["деталі"]
                    # Картка показує поруч «PDF-копія» — лише для копії,
                    # що лежить тут же, а не всередині розпакованого архіву.
                    manifest[key]["pdf_копія"] = next(
                        (m for m in made if m.lower().endswith(".pdf")
                         and os.sep not in m), "")
                    if info.get("статус") in ("СТВОРЕНО", "РОЗПАКОВАНО",
                                              "ВИТЯГНУТО"):
                        stats["копій_для_notebooklm"] += len(info.get("файли", []))
                        stats["pdf_копій"] += sum(
                            1 for x in info.get("файли", [])
                            if x.lower().endswith(".pdf"))
                    elif info.get("статус") in NOTEBOOKLM_FAILED:
                        stats["pdf_не_вдалось"] += 1
            _write_manifest(manifest_path, manifest)
            state = docs_state_of(wanted, manifest, folder)
            stats.setdefault("стани", {})
            stats["стани"][state] = stats["стани"].get(state, 0) + 1
            repo.set_docs_state(r["rejection_id"], state)
        return stats
    finally:
        db.close()
        _sh.rmtree(tmp_dir, ignore_errors=True)


class _DocLists:
    """Мінімальний набір полів, який розуміє refresh_rejection_documents."""

    def __init__(self, **kw):
        self.td_documents = kw.get("td", [])
        self.bid_documents = kw.get("bid", [])
        self.award_documents = kw.get("award", [])
        self.qa_documents = kw.get("qa", [])
        self.qa_text = kw.get("qa_text", "")
        self.protocol_url = self.protocol_title = self.protocol_published = ""
        self.notice_24h_url = self.notice_24h_title = ""
        self.notice_24h = ""


def rehydrate_documents(db_path: str, legacy, rejection_ids,
                        limit: int = HEAL_PER_RUN) -> dict:
    """
    Підтягує з Prozorro переліки документів для справ, де їх немає.

    Кому це треба: справи, записані версією до 02.09, мають порожні
    переліки. Якщо закупівля більше не змінюється, у щоденну стрічку вона
    не потрапляє — і без цього кроку справа лишилась би без документів
    НАЗАВЖДИ. Один запит до Prozorro на справу; кількість обмежена.
    """
    stats = {"перевірено": 0, "поповнено": 0, "помилок": 0, "причини": []}
    if legacy is None or not hasattr(legacy, "get_json") \
            or not getattr(legacy, "API", ""):
        stats["причини"].append("немає модуля мережі — пропущено")
        return stats
    where, args = _rejection_filter(rejection_ids)
    db = Db(db_path)
    try:
        rows = db.q(
            "SELECT r.rejection_id, r.tender_id, r.award_id,"
            "       r.qualification_id, r.bid_id, r.td_documents,"
            "       r.bid_documents, r.award_documents"
            "  FROM rejections r" + where, args)
        todo = [r for r in rows
                if not any(json.loads(r[c] or "[]") for c in
                           ("td_documents", "bid_documents", "award_documents"))]
        if not todo:
            return stats
        audit = Audit(db, new_run_id())
        repo = Repository(db, audit)
        session = legacy.make_session()
        for r in todo[:max(0, limit)]:
            stats["перевірено"] += 1
            try:
                data = legacy.get_json(session, f"{legacy.API}/{r['tender_id']}")
            except Exception as exc:                                # noqa: BLE001
                data = None
                stats["причини"].append(f"{r['tender_id']}: {type(exc).__name__}")
            tender = (data or {}).get("data") if isinstance(data, dict) else None
            if not tender:
                stats["помилок"] += 1
                continue
            stage_id = r["award_id"] or r["qualification_id"] or ""
            decision = {}
            for block in ("awards", "qualifications"):
                for a in (tender.get(block) or []):
                    if a.get("id") == stage_id:
                        decision = a
            bid_id = r["bid_id"] or decision.get("bid_id") or decision.get("bidID")
            bid, _problem = collect_bid_documents(tender, bid_id)
            qa, qa_text = collect_correspondence(tender)
            lists = _DocLists(td=pack_documents(tender.get("documents")),
                              bid=bid, award=pack_documents(decision.get("documents")),
                              qa=qa, qa_text=qa_text)
            if repo.refresh_rejection_documents(r["rejection_id"], lists):
                stats["поповнено"] += 1
        return stats
    finally:
        db.close()


#: Людські назви груп документів.
GROUP_LABELS = (("01_rishennia", "рішення замовника"),
                ("02_vymoga_24h", "вимога 24 год"),
                ("04_propozycia", "пропозиція учасника"),
                ("03_TD", "тендерна документація"),
                ("05_lystuvannia", "листування із замовником"))


def drive_path(path: str) -> str:
    """/content/drive/MyDrive/TENDERWIN/… -> «Мій диск › TENDERWIN › …»."""
    p = str(path or "")
    for prefix in ("/content/drive/MyDrive/", "/content/drive/My Drive/"):
        if p.startswith(prefix):
            return "Мій диск › " + " › ".join(
                x for x in p[len(prefix):].split("/") if x)
    return p


def documents_report(stats: dict, root_dir: str = "") -> str:
    """
    Підсумок документів прогону людською мовою.

    По групах: скільки документів очікувалось і скільки лежить у теці —
    щоб «усе завантажилось» було видно цифрою, а не вірою.
    """
    lines = [f"\nДокументи справ: {stats.get('завантажено', 0)} нових, "
             f"{stats.get('вже_були', 0)} уже були"
             + (f" · копій для NotebookLM {stats['копій_для_notebooklm']}"
                if stats.get("копій_для_notebooklm") else "")]
    groups = stats.get("групи") or {}
    for key, label in GROUP_LABELS:
        g = groups.get(key)
        if not g or not g.get("очікувалось"):
            continue
        line = f"   {label:<26} {g['є']:>4} з {g['очікувалось']:<4}"
        if g.get("не_вдалось"):
            line += f"  ! не вдалось {g['не_вдалось']} — причини в _manifest.json"
        lines.append(line)
    extra = []
    if stats.get("помилок"):
        extra.append(f"збоїв мережі {stats['помилок']} (наступний прогін "
                     f"спробує ще раз сам)")
    if stats.get("недоступні"):
        extra.append(f"недоступні в Prozorro {stats['недоступні']} "
                     f"(403/404 — повтор марний)")
    if stats.get("завеликі"):
        extra.append(f"завеликих {stats['завеликі']}")
    if stats.get("без_посилання"):
        extra.append(f"без посилання в Prozorro {stats['без_посилання']}")
    if stats.get("pdf_не_вдалось"):
        extra.append(f"копій не вдалося зробити {stats['pdf_не_вдалось']}")
    if extra:
        lines.append("   " + " · ".join(extra))
    teky = stats.get("теки") or []
    if teky:
        lines.append("Документи лежать у теках справ:")
        for t in teky[:12]:
            lines.append(f"   {drive_path(t)}")
        if len(teky) > 12:
            lines.append(f"   …і ще {len(teky) - 12} — усі в "
                         f"{drive_path(os.path.dirname(teky[0]))}")
    return "\n".join(lines)


def docs_state_of(wanted: list, manifest: dict, folder: str) -> str:
    """
    COMPLETE    — кожен документ або лежить у теці, або безнадійний
                  (403/404, завеликий, без посилання, вичерпано спроби);
    INCOMPLETE  — є що доробити наступним прогоном;
    EMPTY_LISTS — переліку документів немає взагалі (запис старої версії).
    """
    if not wanted:
        return "EMPTY_LISTS"
    for _group, item in wanted:
        v = manifest.get(_doc_key(item)) or {}
        st = v.get("статус")
        if st == "ЗАВАНТАЖЕНО":
            if not os.path.exists(os.path.join(folder, v.get("файл") or "")):
                return "INCOMPLETE"
            continue
        if st in PERMANENT_FAILURES or st == "ПРОПУЩЕНО_ЛІМІТ":
            continue
        if int(v.get("спроб") or 0) >= MAX_DOC_ATTEMPTS:
            continue
        return "INCOMPLETE"
    return "COMPLETE"


def run_folder(root_dir: str, run_id: str = "", when=None) -> str:
    """
    Тека прогону: <root>/spravy/ДД.ММ.РРРР/prohin_ГГ-ХХ/

    Спершу день, у ньому — окремий прогін. Так за тиждень видно, що саме
    зібрав скрипт кожного дня, і повторний прогін не змішується з ранішнім.
    """
    moment = when or now()
    day = moment.strftime("%d.%m.%Y")
    tail = (re.sub(r"[^\w\-]", "", run_id.split("-")[-1])[:6]
            if run_id else moment.strftime("%H-%M"))
    return os.path.join(root_dir, "spravy", day, f"prohin_{tail}")


def _case_roots(root_dir: str) -> list:
    """Усі теки, де можуть лежати справи: і нові за датами, і давніші."""
    spravy = os.path.join(root_dir, "spravy")
    if not os.path.isdir(spravy):
        return []
    out, plain = [], []
    for name in sorted(os.listdir(spravy)):
        path = os.path.join(spravy, name)
        if not os.path.isdir(path):
            continue
        if name == ARCHIVE_DIR:
            out.append(path)            # архів уже в новій розкладці
        elif re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", name):
            for run in sorted(os.listdir(path)):
                run_path = os.path.join(path, run)
                if os.path.isdir(run_path):
                    out.append(run_path)
        else:
            plain.append(path)          # розкладка до 02.09.2026
    return out + ([spravy] if plain else [])


def _fold_runs_into_archive(root_dir: str) -> dict:
    """
    Справи з тек прогонів -> канонічний архів. Нічого не перезаписує.

    Файл, який в архіві вже є, лишається як є, а старий залишається на
    своєму місці й рахується в конфліктах: людина побачить його і вирішить
    сама. Мовчазних затирань тут бути не може — у цих теках лежать
    і докази, і нотатки (правила 14 і 36).
    """
    out = {"зведено_в_архів": 0, "конфліктів": 0}
    spravy = os.path.join(root_dir, "spravy")
    if not os.path.isdir(spravy):
        return out
    for den in sorted(os.listdir(spravy)):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", den):
            continue
        den_path = os.path.join(spravy, den)
        for prohin in sorted(os.listdir(den_path)):
            run_path = os.path.join(den_path, prohin)
            if not os.path.isdir(run_path):
                continue
            for ua in sorted(os.listdir(run_path)):
                src = os.path.join(run_path, ua)
                if not os.path.isdir(src):
                    continue
                dst = case_archive(root_dir, ua)
                os.makedirs(dst, exist_ok=True)
                moved = False
                for f in sorted(os.listdir(src)):
                    sp = os.path.join(src, f)
                    if not os.path.isfile(sp):
                        continue
                    dp = os.path.join(dst, f)
                    if os.path.exists(dp):
                        out["конфліктів"] += 1
                        continue
                    try:
                        os.replace(sp, dp)
                        moved = True
                    except OSError:
                        out["конфліктів"] += 1
                if moved:
                    out["зведено_в_архів"] += 1
                try:
                    os.rmdir(src)          # порожню теку прибираємо
                except OSError:
                    pass                   # лишились файли — хай лишається
    return out


def migrate_case_layout(root_dir: str, verbose: bool = True) -> dict:
    """
    Стара розкладка -> нова: усе в ОДНІЙ теці справи.

    Було:  spravy/<UA-ID>/kartka_X.docx
           spravy/<UA-ID>/dokumenty/01_rishennia_...
    Стало: spravy/<UA-ID>/00_KARTKA_X.docx
           spravy/<UA-ID>/01_rishennia_...

    Далі — з тек прогонів у канонічний архів:

    Було:  spravy/02.09.2026/prohin_ab12cd/<UA-ID>/…
    Стало: spravy/_arhiv/<UA-ID>/…

    Нічого не перезаписує: якщо файл із таким імʼям уже є, старий лишається
    на місці й потрапляє у звіт (правило 14). Людські нотатки в картці
    зберігаються — файл ПЕРЕЙМЕНОВУЄТЬСЯ, а не створюється заново.
    """
    stats = {"перенесено_справ": 0, "піднято_файлів": 0,
             "перейменовано_карток": 0, "конфліктів": 0,
             "зведено_в_архів": 0}
    # Обхід усіх тек справ на Google Диску — це сотні мережевих звернень.
    # Розкладку треба перенести ОДИН раз; далі движок сам кладе файли
    # правильно. Маркер знімається вручну, якщо перенесення треба повторити.
    marker = os.path.join(root_dir, "spravy", MIGRATION_MARKER)
    if os.path.exists(marker):
        stats["вже_перенесено"] = True
        return stats
    pary = [(root, n) for root in _case_roots(root_dir)
            for n in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, n))]
    for spravy, name in pary:
        folder = os.path.join(spravy, name)
        if not os.path.isdir(folder):
            continue
        changed = False

        # 1. піднімаємо документи з підпапки
        sub = os.path.join(folder, LEGACY_DOC_SUBDIR)
        if os.path.isdir(sub):
            for f in sorted(os.listdir(sub)):
                src = os.path.join(sub, f)
                if not os.path.isfile(src):
                    continue
                target_name = MANIFEST_NAME if f == "manifest.json" else f
                dst = os.path.join(folder, target_name)
                if os.path.exists(dst):
                    stats["конфліктів"] += 1
                    continue
                try:
                    os.replace(src, dst)
                    stats["піднято_файлів"] += 1
                    changed = True
                except OSError:
                    stats["конфліктів"] += 1
            try:
                if not os.listdir(sub):
                    os.rmdir(sub)
            except OSError:
                pass

        # 2. перейменовуємо картку і запити, зберігаючи вміст
        for old_pref, new_pref in ((LEGACY_CARD_PREFIX, CARD_PREFIX),
                                   (LEGACY_QUERIES_PREFIX, QUERIES_PREFIX)):
            for f in sorted(os.listdir(folder)):
                if not f.startswith(old_pref):
                    continue
                dst = os.path.join(folder, new_pref + f[len(old_pref):])
                if os.path.exists(dst):
                    stats["конфліктів"] += 1
                    continue
                try:
                    os.replace(os.path.join(folder, f), dst)
                    if old_pref == LEGACY_CARD_PREFIX:
                        stats["перейменовано_карток"] += 1
                    changed = True
                except OSError:
                    stats["конфліктів"] += 1
        if changed:
            stats["перенесено_справ"] += 1

    # Тепер зводимо справи з тек прогонів у архів. Без цього кроку вже
    # завантажені документи лишились би розкиданими по датах, і перший же
    # прогін після оновлення качав би їх наново — тобто саме те, що ми
    # виправляємо (правило 14: наявні дані не викидаємо).
    # ДОДАЄМО, а не замінюємо: конфлікти першого кроку — теж конфлікти,
    # і .update() затер би їх нулем.
    for key, value in _fold_runs_into_archive(root_dir).items():
        stats[key] = stats.get(key, 0) + value

    if verbose and (stats["перенесено_справ"] or stats["зведено_в_архів"]):
        print(f"   розкладку справ оновлено: {stats['перенесено_справ']} тек, "
              f"піднято {stats['піднято_файлів']} файлів, "
              f"перейменовано карток {stats['перейменовано_карток']}"
              + (f", конфліктів {stats['конфліктів']}"
                 if stats["конфліктів"] else "")
              + (f", зведено в архів {stats['зведено_в_архів']}"
                 if stats["зведено_в_архів"] else ""))
    # Маркер ставимо ЛИШЕ після успішного проходу: інакше збій на середині
    # залишив би половину справ у старій розкладці назавжди.
    try:
        os.makedirs(os.path.join(root_dir, "spravy"), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({"коли": now_iso(), "движок": ENGINE_VERSION,
                       **{k: v for k, v in stats.items()}}, fh,
                      ensure_ascii=False, indent=1)
    except OSError:
        pass                     # без маркера просто відпрацює ще раз
    return stats


def case_folders(root_dir: str) -> None:
    """
    Показує, що САМЕ лежить у кожній теці справи. Відповідає на питання
    «де картка» фактами з диска, а не припущенням.
    """
    roots = _case_roots(root_dir)
    if not roots:
        print(f"Теки справ ще немає: {os.path.join(root_dir, 'spravy')}")
        print("Вона зʼявиться після першого прогону E.go().")
        return
    pary = [(root, n) for root in roots
            for n in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, n))
            and n.upper().startswith("UA-")]
    if not pary:
        print("Жодного відхилення ще не оброблено.")
        return
    print(f"Справ: {len(pary)}\n")
    last_root = None
    for spravy, n in pary:
        if spravy != last_root:
            last_root = spravy
            print(f"── прогін: {spravy}")
        folder = os.path.join(spravy, n)
        files = sorted(f for f in os.listdir(folder)
                       if os.path.isfile(os.path.join(folder, f)))
        kartky = [f for f in files if f.startswith(CARD_PREFIX)]
        print(f"■ {n}")
        if kartky:
            for k in kartky:
                print(f"    КАРТКА: {k}")
        else:
            print("    КАРТКИ НЕМАЄ — запустіть "
                  "E.build_case_cards(DYSK + '/leads.db', DYSK)")
        for f in files:
            if f in kartky:
                continue
            size = os.path.getsize(os.path.join(folder, f))
            print(f"    {f}   ({size // 1024} КБ)" if size >= 1024
                  else f"    {f}   ({size} Б)")
        pidpapka = os.path.join(folder, LEGACY_DOC_SUBDIR)
        if os.path.isdir(pidpapka):
            print(f"    ! лишилась стара підпапка {LEGACY_DOC_SUBDIR}/ — "
                  "запустіть E.migrate_case_layout(DYSK)")
        print()


def _clarity_keywords_table(d, order, extra_keywords, Pt, Cm, ALIGN) -> None:
    """Друга таблиця картки. Викликається лише за CLARITY_KEYWORDS_IN_CARD."""
    d.add_paragraph()
    kh = d.add_paragraph()
    kh.alignment = ALIGN.CENTER
    khr = kh.add_run("КЛЮЧОВІ СЛОВА ДЛЯ CLARITY APP")
    khr.bold = True
    khr.font.size = Pt(11)
    kt = d.add_table(rows=1, cols=5)
    kt.style = "Table Grid"
    for idx, name in enumerate(("№", "Підстава", "За кого", "Запит", "Навіщо")):
        kt.rows[0].cells[idx].text = ""
        run = kt.rows[0].cells[idx].paragraphs[0].add_run(name)
        run.bold = True
        run.font.size = Pt(9)
    pol_ua = {"neutral": "нейтрально", "participant": "за учасника",
              "buyer": "за замовника"}
    n = 0
    for row_q in order:
        n += 1
        cells = kt.add_row().cells
        for idx, val in enumerate((str(n), row_q.claim_id,
                                   pol_ua.get(row_q.polarity, row_q.polarity),
                                   row_q.query_text, row_q.why)):
            cells[idx].text = str(val)
    for extra in extra_keywords:
        n += 1
        cells = kt.add_row().cells
        for idx, val in enumerate((str(n), "—", "нейтрально", extra,
                                   "зі словника теми")):
            cells[idx].text = str(val)
    for row_t in kt.rows:
        for idx, w in enumerate((Cm(0.9), Cm(1.7), Cm(2.2), Cm(7.2), Cm(5.8))):
            row_t.cells[idx].width = w
        for cell in row_t.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(8)


def _local_files(folder: str) -> dict:
    """URL -> імʼя локального файла, з manifest.json теки справи."""
    path = os.path.join(folder, MANIFEST_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}
    return {url: (v["файл"], v.get("pdf_копія") or "")
            for url, v in data.items()
            if v.get("статус") == "ЗАВАНТАЖЕНО" and v.get("файл")}


# ============================================================================
#  БЛОК 30. ПРЕДМЕТ ЗАКУПІВЛІ В РОДОВОМУ ВІДМІНКУ
# ----------------------------------------------------------------------------
#  У листі йде «...на закупівлю поточного ремонту приміщень». Відмінюємо
#  ЛИШЕ голову назви — прикметники плюс перший іменник. Решта назви вже
#  стоїть у родовому («приміщень будівлі пральні») і чіпати її не можна.
#
#  Якщо голову не впізнали — НЕ вигадуємо форму. Беремо назву в лапки в
#  називному: «...на закупівлю «Поточний ремонт приміщень»». Це граматично
#  правильно і чесно (правило 5).
# ============================================================================

#: Іменник -> (родовий відмінок, рід/число). Керований словник: те, що
#: справді зустрічається в будівельних закупівлях.
NOUN_GENITIVE = {
    "ремонт": ("ремонту", "m"),
    "капремонт": ("капремонту", "m"),
    "монтаж": ("монтажу", "m"),
    "демонтаж": ("демонтажу", "m"),
    "нагляд": ("нагляду", "m"),
    "благоустрій": ("благоустрою", "m"),
    "ремонти": ("ремонтів", "pl"),
    "будівництво": ("будівництва", "n"),
    "проєктування": ("проєктування", "n"),
    "проектування": ("проектування", "n"),
    "постачання": ("постачання", "n"),
    "обладнання": ("обладнання", "n"),
    "устаткування": ("устаткування", "n"),
    "утримання": ("утримання", "n"),
    "обслуговування": ("обслуговування", "n"),
    "виготовлення": ("виготовлення", "n"),
    "влаштування": ("влаштування", "n"),
    "оснащення": ("оснащення", "n"),
    "прибирання": ("прибирання", "n"),
    "перевезення": ("перевезення", "n"),
    "реконструкція": ("реконструкції", "f"),
    "реставрація": ("реставрації", "f"),
    "термомодернізація": ("термомодернізації", "f"),
    "модернізація": ("модернізації", "f"),
    "заміна": ("заміни", "f"),
    "закупівля": ("закупівлі", "f"),
    "розробка": ("розробки", "f"),
    "розроблення": ("розроблення", "n"),
    "експертиза": ("експертизи", "f"),
    "придбання": ("придбання", "n"),
    "страхування": ("страхування", "n"),
    "оренда": ("оренди", "f"),
    "поставка": ("поставки", "f"),
    "поставки": ("поставок", "pl"),
    "послуги": ("послуг", "pl"),
    "послуга": ("послуги", "f"),
    "роботи": ("робіт", "pl"),
    "робота": ("роботи", "f"),
    "матеріали": ("матеріалів", "pl"),
    "товари": ("товарів", "pl"),
}

#: Прикметникові закінчення: тверда і мʼяка групи.
_ADJ_HARD = {"m": "ого", "n": "ого", "f": "ої", "pl": "их"}
_ADJ_SOFT = {"m": "ього", "n": "ього", "f": "ьої", "pl": "іх"}

#: Прикметник у називному -> основа + група.
_ADJ_NOM = (
    ("ий", "hard"), ("а", "hard"), ("е", "hard"), ("і", "hard"),
    ("ій", "soft"), ("я", "soft"), ("є", "soft"),
)

#: Код ДК на початку назви людині в реченні не потрібен.
_DK_PREFIX = re.compile(
    r"^\s*(?:ДК\s*021[:\s-]*2015\s*[:\-–—]?\s*)?"
    r"(?:\d{8}\s*-\s*\d\s*[:\-–—]?\s*)?", re.IGNORECASE)


def _adj_genitive(word: str, gender: str) -> Optional[str]:
    """Прикметник у родовому. None — якщо це не прикметник."""
    low = word.lower()
    for end, group in _ADJ_NOM:
        if not low.endswith(end) or len(low) <= len(end) + 2:
            continue
        # «-ій» перевіряємо раніше за «-й»? Порядок у _ADJ_NOM це вирішує.
        if end == "і" and low.endswith("ій"):
            continue                       # це мʼяка група, а не множина
        table = _ADJ_HARD if group == "hard" else _ADJ_SOFT
        return low[: -len(end)] + table[gender]
    return None


def subject_genitive(title: str) -> tuple[str, str]:
    """
    Повертає (текст_для_листа, впевненість).

    HIGH — голову назви відмінено;
    LOW  — назву не впізнано, вона йде в лапках у називному.
    """
    raw = re.sub(r"\s+", " ", str(title or "")).strip(" .;–—-")
    if not raw:
        return "", "NONE"
    raw = _DK_PREFIX.sub("", raw).strip(" :.-–—")
    if not raw:
        return "", "NONE"

    words = raw.split()
    # шукаємо перший іменник зі словника серед перших чотирьох слів
    head_i = None
    for i, w in enumerate(words[:4]):
        key = re.sub(r"[^\w'’\-]", "", w).lower()
        if key in NOUN_GENITIVE:
            head_i = i
            break
    if head_i is None:
        return f"«{raw}»", "LOW"

    noun_key = re.sub(r"[^\w'’\-]", "", words[head_i]).lower()
    noun_gen, gender = NOUN_GENITIVE[noun_key]

    out = []
    for w in words[:head_i]:              # прикметники перед іменником
        adj = _adj_genitive(w, gender)
        if adj is None:                   # не прикметник — не чіпаємо голову
            return f"«{raw}»", "LOW"
        out.append(adj)
    out.append(noun_gen)

    # Однорідна голова: «капітальний ремонт І реставрація» -> обидва іменники.
    tail = words[head_i + 1:]
    while len(tail) >= 2 and tail[0].lower() in ("і", "та", "й", ","):
        key2 = re.sub(r"[^\w'’\-]", "", tail[1]).lower()
        if key2 not in NOUN_GENITIVE:
            break
        out += [tail[0], NOUN_GENITIVE[key2][0]]
        tail = tail[2:]
    return " ".join(out + tail), "HIGH"


#: Повна організаційна форма -> абревіатура. У листі ніхто не пише
#: «ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ» повністю, і повна форма ще й
#: тягне за собою відмінювання, у якому легко помилитись.
ORG_ABBR = [
    (r"ТОВАРИСТВО\s+З\s+ОБМЕЖЕНОЮ\s+ВІДПОВІДАЛЬНІСТЮ", "ТОВ"),
    (r"ТОВАРИСТВО\s+З\s+ДОДАТКОВОЮ\s+ВІДПОВІДАЛЬНІСТЮ", "ТДВ"),
    (r"ПРИВАТНЕ\s+АКЦІОНЕРНЕ\s+ТОВАРИСТВО", "ПрАТ"),
    (r"ПУБЛІЧНЕ\s+АКЦІОНЕРНЕ\s+ТОВАРИСТВО", "ПАТ"),
    (r"АКЦІОНЕРНЕ\s+ТОВАРИСТВО", "АТ"),
    (r"КОМУНАЛЬНЕ\s+НЕКОМЕРЦІЙНЕ\s+ПІДПРИЄМСТВО", "КНП"),
    (r"КОМУНАЛЬНЕ\s+ПІДПРИЄМСТВО", "КП"),
    (r"ДЕРЖАВНЕ\s+ПІДПРИЄМСТВО", "ДП"),
    (r"ПРИВАТНЕ\s+ПІДПРИЄМСТВО", "ПП"),
    (r"НАУКОВО[-\s]ВИРОБНИЧЕ\s+ПІДПРИЄМСТВО", "НВП"),
    (r"ФЕРМЕРСЬКЕ\s+ГОСПОДАРСТВО", "ФГ"),
    (r"ФІЗИЧНА\s+ОСОБА[-\s]ПІДПРИЄМЕЦЬ", "ФОП"),
    (r"ДОЧІРНЄ\s+ПІДПРИЄМСТВО", "ДП"),
]
_ORG_ABBR_RE = [(re.compile(p, re.IGNORECASE), a) for p, a in ORG_ABBR]


def short_org_name(name: str) -> str:
    """«ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ «АЛЬФА»» -> «ТОВ «АЛЬФА»»."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        return ""
    for rx, abbr in _ORG_ABBR_RE:
        new = rx.sub(abbr, text)
        if new != text:
            return re.sub(r"\s+", " ", new).strip()
    return text


#: Головне слово в назві замовника -> рід. Від нього залежить «проводив»,
#: «проводила» чи «проводило» — інакше в першому ж рядку листа буде помилка.
BUYER_HEAD_GENDER = {
    "управління": "n", "міністерство": "n", "агентство": "n", "відомство": "n",
    "підприємство": "n", "товариство": "n", "об'єднання": "n", "бюро": "n",
    "госпітальне": "n", "кнп": "n", "кп": "n", "дп": "n", "тов": "n",
    "прат": "n", "пат": "n", "ат": "n", "нвп": "n", "тдв": "n", "пп": "n",
    "департамент": "m", "відділ": "m", "центр": "m", "заклад": "m",
    "фонд": "m", "ліцей": "m", "коледж": "m", "інститут": "m", "комітет": "m",
    "виконком": "m", "будинок": "m", "комбінат": "m", "завод": "m",
    "служба": "f", "адміністрація": "f", "рада": "f", "дирекція": "f",
    "лікарня": "f", "школа": "f", "гімназія": "f", "академія": "f",
    "інспекція": "f", "філія": "f", "громада": "f", "установа": "f",
    "організація": "f", "фірма": "f", "компанія": "f", "поліклініка": "f",
}
_VERB_BY_GENDER = {"m": "проводив", "f": "проводила",
                   "n": "проводило", "pl": "проводили"}


def buyer_verb(buyer_name: str) -> tuple[str, str]:
    """
    Повертає (форма дієслова, впевненість) для «...яку {verb} {ЗАМОВНИК}».

    HIGH — голову назви впізнано; LOW — визначено за закінченням слова.
    """
    text = re.sub(r"[«»\"']", " ", str(buyer_name or ""))
    words = [w for w in re.findall(r"[\w'’\-]+", text) if len(w) > 1]
    if not words:
        return _VERB_BY_GENDER["m"], "LOW"
    for w in words[:3]:
        g = BUYER_HEAD_GENDER.get(w.lower())
        if g:
            return _VERB_BY_GENDER[g], "HIGH"
    low = words[0].lower()
    if low.endswith(("а", "я")):
        g = "f"
    elif low.endswith(("о", "е", "я")):
        g = "n"
    elif low.endswith(("и", "і")):
        g = "pl"
    else:
        g = "m"
    return _VERB_BY_GENDER[g], "LOW"


# ============================================================================
#  БЛОК 31. ПЛАТА ЗА ПОДАННЯ СКАРГИ ДО АМКУ
# ----------------------------------------------------------------------------
#  Правило — ДАНІ, а не логіка, зашита в бізнес-код: норма змінюється, і
#  тоді має мінятись одна структура, а не десяток місць (§22 специфікації).
#
#  База — очікувана вартість ЛОТУ, якщо скарга стосується лота, інакше
#  очікувана вартість закупівлі. Ціна пропозиції учасника базою НЕ Є ніколи.
#
#  Якщо релевантний лот не встановлено — це стан, а не привід узяти суму
#  всього тендера. Саме тихий відкат на суму тендера і був коренем дефекту.
# ============================================================================
from decimal import Decimal, ROUND_CEILING                        # noqa: E402

AMCU_FEE_RULE = {
    "rule_id": "AMCU_POST_EVALUATION_292",
    "rate": Decimal("0.006"),
    "minimum_uah": Decimal("3000"),
    "maximum_uah": Decimal("170000"),
    "rounding_increment_uah": Decimal("10"),
    "legal_source": "Постанова КМУ № 292 від 22.04.2020",
    "complaint_type": "Оскарження рішення замовника після оцінки пропозиції",
    "rule_status": "VERIFIED",
}

#: Стани бази розрахунку.
FEE_BASE_LOT = "LOT_EXPECTED_VALUE"
FEE_BASE_TENDER = "TENDER_EXPECTED_VALUE"
FEE_STATUS_CALCULATED = "CALCULATED"
FEE_STATUS_NO_BASE = "AMCU_FEE_BASE_NOT_ESTABLISHED"


def round_up_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """
    Округлення ВГОРУ до кратного increment. Не банківське: 7 400,01 -> 7 410.
    Гроші рахуються Decimal, ніколи float (§27, правило 19).
    """
    if increment <= 0:
        return value
    return (value / increment).quantize(Decimal("1"),
                                        rounding=ROUND_CEILING) * increment


def calculate_amcu_fee(*, lot_value=None, tender_value=None,
                       lots_total: int = 0, lot_resolved: str = "",
                       lot_id: str = "", rule: Optional[dict] = None) -> dict:
    """
    Розрахункова плата за подання скарги + повний провенанс.

    Повертає словник із полями §31: база, її тип, ставка, сирий результат,
    які межі спрацювали, округлення, підсумок, ід правила і джерела.
    """
    rule = rule or AMCU_FEE_RULE
    out = {
        "amcu_fee_status": FEE_STATUS_NO_BASE,
        "amcu_fee_base_type": "",
        "amcu_fee_base_amount": None,
        "amcu_fee_rate": str(rule["rate"]),
        "amcu_fee_raw": None,
        "amcu_fee_min_applied": 0,
        "amcu_fee_max_applied": 0,
        "amcu_fee_rounding_applied": "",
        "amcu_fee_calculated": None,
        "amcu_fee_rule_id": rule["rule_id"],
        "amcu_fee_source_object_id": "",
        "amcu_fee_reason": "",
        "amcu_fee_candidates": "",
    }

    def _dec(value):
        if value in (None, ""):
            return None
        try:
            d = Decimal(str(value))
        except Exception:                                          # noqa: BLE001
            return None
        return d if d > 0 else None

    lot = _dec(lot_value)
    tender = _dec(tender_value)

    if lot is not None and lot_resolved == "YES":
        base, base_type, obj = lot, FEE_BASE_LOT, lot_id or ""
    elif lots_total and lots_total > 1 and lot_resolved != "YES":
        # Багатолотова закупівля, лот не встановлено. Брати суму всього
        # тендера НЕ МОЖНА: у прикладі зі специфікації це 20 млн замість 4.
        out["amcu_fee_reason"] = "RELEVANT_LOT_NOT_VERIFIED"
        out["amcu_fee_candidates"] = (
            f"лотів у закупівлі: {lots_total}; "
            f"вартість закупівлі: {tender if tender is not None else '—'}")
        return out
    elif tender is not None and (not lots_total or lots_total <= 1):
        base, base_type, obj = tender, FEE_BASE_TENDER, ""
    else:
        out["amcu_fee_reason"] = ("NO_EXPECTED_VALUE" if tender is None
                                  else "RELEVANT_LOT_NOT_VERIFIED")
        return out

    raw = base * rule["rate"]
    bounded = raw
    if bounded < rule["minimum_uah"]:
        bounded = rule["minimum_uah"]
        out["amcu_fee_min_applied"] = 1
    if bounded > rule["maximum_uah"]:
        bounded = rule["maximum_uah"]
        out["amcu_fee_max_applied"] = 1
    final = round_up_to_increment(bounded, rule["rounding_increment_uah"])

    out.update({
        "amcu_fee_status": FEE_STATUS_CALCULATED,
        "amcu_fee_base_type": base_type,
        "amcu_fee_base_amount": str(base),
        "amcu_fee_raw": str(raw),
        "amcu_fee_rounding_applied":
            f"вгору до {rule['rounding_increment_uah']} грн",
        "amcu_fee_calculated": str(final),
        "amcu_fee_source_object_id": obj,
    })
    return out


def money_uah(value) -> str:
    """«4000000» -> «4 000 000,00 грн». Порожнє значення не вигадуємо."""
    if value in (None, ""):
        return NOT_ESTABLISHED
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:                                              # noqa: BLE001
        return str(value)
    whole, _, cents = f"{d:.2f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return f"{grouped},{cents} грн"


# ============================================================================
#  БЛОК 32. ВАЛІДАТОР НЕСУПЕРЕЧЛИВОСТІ СЛУЖБОВОЇ КАРТКИ
# ----------------------------------------------------------------------------
#  Картці не можна вірити, якщо вона одночасно каже «вартість лоту не
#  встановлена» і показує цю вартість поруч. Це не косметика — це ознака
#  того, що дані в базі суперечать самі собі.
#
#  Суперечність не маскується: картка отримує позначку, а критичні для
#  безпеки відправки — блокують саму відправку (§44).
# ============================================================================
CONTRADICTION = "INTERNAL_DATA_CONTRADICTION"

#: Стани фактичної доставки (§39-41): «ще не відправлено» — це НЕ
#: «не встановлено», і плутати їх не можна.
DELIVERY_NOT_SENT_YET = "ЩЕ НЕ ВІДПРАВЛЕНО"
DELIVERY_NOT_RESOLVED = "DELIVERY_EMAIL_NOT_RESOLVED"


def validate_service_card_consistency(row, *, test_allowlist=None) -> list:
    """
    Перевірки A-F зі специфікації. Повертає список суперечностей;
    кожна — {код, поля, пояснення, критична}.
    """
    allow = set(test_allowlist if test_allowlist is not None else TEST_RECIPIENTS)
    out: list = []

    def add(code, fields, what, critical=False):
        out.append({"код": code, "поля": fields, "що": what,
                    "критична": critical})

    def val(name):
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return None

    # A. плата «не встановлена», хоча вартість лоту в базі є
    if val("amcu_fee_status") == FEE_STATUS_NO_BASE and val("lot_value_amount"):
        add("A_FEE_VS_LOT_VALUE",
            ["amcu_fee_status", "lot_value_amount"],
            f"плата не розрахована, хоча вартість лоту відома: "
            f"{val('lot_value_amount')}")
    # A2. те саме для вартості закупівлі без лотів
    if (val("amcu_fee_status") == FEE_STATUS_NO_BASE
            and val("tender_value_amount")
            and (val("lots_total") or 0) <= 1):
        add("A2_FEE_VS_TENDER_VALUE",
            ["amcu_fee_status", "tender_value_amount"],
            f"плата не розрахована, хоча вартість закупівлі відома: "
            f"{val('tender_value_amount')}")

    # B. лист надіслано, а адреси доставки немає
    if val("status") == "SENT" and not val("delivery_email_actual"):
        add("B_SENT_WITHOUT_DELIVERY_EMAIL",
            ["status", "delivery_email_actual"],
            "статус SENT без фактичної адреси доставки", critical=True)

    # C. учасника «встановлено», а ЄДРПОУ немає
    if val("company_name") and not val("edrpou_norm"):
        add("C_PARTICIPANT_WITHOUT_EDRPOU",
            ["company_name", "edrpou_norm"],
            "компанія названа, але ЄДРПОУ відсутній")

    # D. тестовий режим, а адреса поза білим списком — КРИТИЧНО
    mode, delivered = val("mode"), val("delivery_email_actual")
    if mode == "TEST" and delivered and delivered not in allow:
        add("D_TEST_RECIPIENT_NOT_ALLOWED",
            ["mode", "delivery_email_actual"],
            f"у тестовому режимі доставка на «{delivered}», якої немає "
            f"в білому списку", critical=True)

    # E. бойовий режим, а адреса — тестова скринька
    if mode == "LIVE" and delivered and delivered in allow:
        add("E_LIVE_USES_TEST_INBOX",
            ["mode", "delivery_email_actual"],
            f"бойовий режим, але доставка на тестову скриньку «{delivered}»",
            critical=True)

    # F. база плати «вартість лоту», а лота немає
    if val("amcu_fee_base_type") == FEE_BASE_LOT and not val("lot_id"):
        add("F_LOT_BASE_WITHOUT_LOT",
            ["amcu_fee_base_type", "lot_id"],
            "базою названо вартість лоту, але сам лот не визначений")
    return out


def delivery_display(row) -> tuple[str, str]:
    """
    (що показати, стан) для рядка «Кому фактично доставлено».

    «Ще не відправлено» — окремий стан. Раніше він виглядав як
    «НЕ ВСТАНОВЛЕНО», тобто як втрата даних, хоча даних просто ще немає.
    Якщо відправка впала — адресу все одно показуємо (§41).
    """
    def val(name):
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return None

    delivered, status = val("delivery_email_actual"), val("status")
    if delivered:
        if status in ("SEND_FAILED", "CANCELLED"):
            return f"{delivered} (спроба, статус {status})", status or ""
        if status in (None, "", "QUEUED"):
            return f"{delivered} — {DELIVERY_NOT_SENT_YET}", "QUEUED"
        return delivered, status or ""
    if status is None:
        return DELIVERY_NOT_SENT_YET, "NOT_SENT_YET"
    return DELIVERY_NOT_RESOLVED, "NOT_RESOLVED"


# ============================================================================
#  БЛОК 33. ЖУРНАЛ ПРОГОНІВ У GOOGLE ТАБЛИЦІ
# ----------------------------------------------------------------------------
#  Таблиця — ВІТРИНА, а не джерело істини. Єдине джерело — leads.db.
#  Тому журнал:
#    * ніколи не читається назад як факт;
#    * ніколи не пише в колонки, яких не знає (там Ваші нотатки);
#    * ніколи не стирає непорожню клітинку порожнім значенням;
#    * ніколи не зупиняє прогін: не вийшло — сказали кодом і пішли далі.
#
#  Зіставлення йде за НАЗВОЮ колонки, не за номером. Колонки можна
#  перейменовувати, міняти місцями і додавати свої — нічого не зламається.
# ============================================================================

#: Колонки, які веде движок: (назва, як дістати значення з рядка).
#: Порядок тут — лише порядок при СТВОРЕННІ таблиці. Далі порядок Ваш.
JOURNAL_COLUMNS: list = [
    (JOURNAL_KEY_COLUMN, lambda r: f"{r['ua_id']}·{r['edrpou_norm'] or ''}"),
    ("Дата прогону", lambda r: now().strftime("%d.%m.%Y %H:%M")),
    ("Закупівля", lambda r: r["ua_id"] or ""),
    ("Предмет", lambda r: r["tender_title"] or ""),
    ("Замовник", lambda r: r["buyer_name"] or ""),
    ("Учасник", lambda r: r["company_name"] or ""),
    ("ЄДРПОУ", lambda r: r["edrpou_norm"] or ""),
    ("Контактна особа", lambda r: r["contact_name_raw"] or ""),
    ("Телефон", lambda r: r["contact_phone"] or ""),
    ("E-mail", lambda r: r["contact_email"] or ""),
    ("Дата відхилення", lambda r: str(r["rejection_date"] or "")[:10]),
    ("Підстав у рішенні", lambda r: _journal_grounds(r)),
    ("Очікувана вартість лота", lambda r: money_uah(r["lot_value_amount"])),
    ("Ціна пропозиції", lambda r: money_uah(r["bid_amount"])),
    ("Різниця з переможцем", lambda r: _journal_gap(r)),
    ("Плата до АМКУ", lambda r: _journal_fee(r)),
    ("Строк на скаргу", lambda r: r["complaint_deadline"] or NOT_ESTABLISHED),
    ("Вимога 24 год", lambda r: {"YES": "є", "NO": "немає"}.get(
        r["notice_24h"] or "", NOT_ESTABLISHED)),
    ("Лист", lambda r: delivery_display(r)[1] or NOT_ESTABLISHED),
    ("Кому надіслано", lambda r: delivery_display(r)[0]),
    ("Тека справи", lambda r: r.get("_folder", "")),
    ("Посилання на Prozorro",
     lambda r: PROZORRO_TENDER_URL.format(ua_id=r["ua_id"])),
]

#: Колонки для ЛЮДИНИ. Створюються порожніми і движком не заповнюються
#: НІКОЛИ — ні зараз, ні при повторному прогоні.
JOURNAL_HUMAN_COLUMNS = ["Дзвінок", "Результат", "Мої нотатки"]


def _journal_grounds(row) -> str:
    """Скільки підстав у рішенні. Рахує той самий розбір, що й картка."""
    try:
        claims = analyze_protocol(row["rejection_reason_raw"] or "")
    except Exception:                                              # noqa: BLE001
        return NOT_ESTABLISHED
    return str(len(claims)) if claims else NOT_ESTABLISHED


def _journal_gap(row) -> str:
    win, bid = row["winner_amount"], row["bid_amount"]
    if win in (None, "") or bid in (None, ""):
        return NOT_ESTABLISHED
    try:
        diff = Decimal(str(win)) - Decimal(str(bid))
    except Exception:                                              # noqa: BLE001
        return NOT_ESTABLISHED
    znak = "дешевше за переможця" if diff > 0 else "дорожче за переможця"
    return f"{money_uah(abs(diff))} {znak}"


def _journal_fee(row) -> str:
    if row["amcu_fee_status"] == FEE_STATUS_CALCULATED:
        return money_uah(row["amcu_fee_calculated"])
    return f"{NOT_ESTABLISHED} ({row['amcu_fee_reason'] or 'без бази'})"


def _journal_state_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        JOURNAL_ID_FILE)


def journal_id(db_path: str) -> str:
    """Ід таблиці цього штабу, якщо його вже створювали."""
    path = _journal_state_path(db_path)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh).get("spreadsheet_id") or "")
    except (ValueError, OSError):
        return ""


#: Що робити з кожною відомою відповіддю Google. Ключ — reason з тіла
#: помилки; значення — (код TenderWin, що сталося, що робити).
GOOGLE_ERRORS = {
    "SERVICE_DISABLED": (
        "SHEETS_API_NOT_ENABLED",
        "Google Sheets API не увімкнено у Вашому проєкті Google Cloud.",
        "Відкрийте посилання нижче, натисніть «Enable» («Увімкнути») і "
        "зачекайте 1-2 хвилини, поки Google це рознесе. Потім запустіть "
        "клітинку ще раз."),
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT": (
        "SHEETS_SCOPE_MISSING",
        "Токен випущено БЕЗ доступу до таблиць.",
        "Виконайте E.reset_gmail_token(DYSK), потім E.setup_gmail(DYSK) — "
        "і пройдіть посилання авторизації ще раз."),
    "PERMISSION_DENIED": (
        "SHEETS_PERMISSION_DENIED",
        "Google відмовив у доступі до таблиці.",
        "Дві причини бувають разом: не увімкнено Sheets API (посилання нижче) "
        "або токен без доступу до таблиць (E.reset_gmail_token(DYSK), потім "
        "E.setup_gmail(DYSK))."),
    "RATE_LIMIT_EXCEEDED": (
        "SHEETS_RATE_LIMIT",
        "Google тимчасово обмежив кількість звернень.",
        "Зачекайте хвилину і запустіть ще раз. Дані не втрачені."),
    "NOT_FOUND": (
        "JOURNAL_NOT_FOUND",
        "Таблиці журналу за збереженим ідентифікатором більше немає.",
        "Якщо Ви її видалили — зітріть файл zhurnal.json поруч із базою, "
        "і движок створить нову."),
    "BACKEND_ERROR": (
        "SHEETS_TEMPORARY",
        "Тимчасовий збій на боці Google.",
        "Це не Ваша помилка і не помилка скрипта. Запустіть ще раз за "
        "кілька хвилин; дані не втрачені."),
}

#: Слова, за якими впізнаємо причину, якщо Google не дав машинного reason.
_GOOGLE_HINTS = (
    ("insufficient", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"),
    ("has not been used in project", "SERVICE_DISABLED"),
    ("rate limit", "RATE_LIMIT_EXCEEDED"),
    ("quota exceeded", "RATE_LIMIT_EXCEEDED"),
    ("was not found", "NOT_FOUND"),
)


def _google_error(exc) -> dict:
    """
    Перекладає помилку Google API у зрозумілу людині.

    Правило 13: людина має отримати КОД, що сталося, чи можна продовжувати
    і що робити далі — а не трейсбек на пів екрана.
    """
    text = str(exc)
    # Статус буває і в «HttpError 403 when…», і просто «503 backend error».
    m = (re.search(r"HttpError (\d{3})", text)
         or re.search(r"(?<!\d)([45]\d{2})(?!\d)", text))
    status = m.group(1) if m else ""
    reason = ""
    for key in GOOGLE_ERRORS:
        if key in text:
            reason = key
            break
    if not reason:
        low = text.lower()
        for slovo, key in _GOOGLE_HINTS:
            if slovo in low:
                reason = key
                break
    if not reason and status == "404":
        reason = "NOT_FOUND"
    if not reason and status == "429":
        reason = "RATE_LIMIT_EXCEEDED"
    if not reason and status.startswith("5"):
        reason = "BACKEND_ERROR"
    kod, shcho, shcho_robyty = GOOGLE_ERRORS.get(
        reason,
        ("SHEETS_API_ERROR",
         f"Google повернув помилку{' ' + status if status else ''}.",
         "Спробуйте ще раз за кілька хвилин. Якщо повторюється — "
         "надішліть мені цей текст."))
    posylannya = ""
    link = re.search(
        r"https://console\.(?:developers|cloud)\.google\.com/[^\s'\"<>]+",
        text)
    if link:
        posylannya = link.group(0).rstrip("'\",.")
    return {"код": kod, "що": shcho, "що_робити": shcho_robyty,
            "посилання": posylannya, "деталі": text[:400]}


def _journal_fail(exc) -> "EngineError":
    """Помилка Google -> EngineError із людським поясненням."""
    info = _google_error(exc)
    khvist = (f"\n\n   {info['посилання']}" if info["посилання"] else "")
    return ERR["JOURNAL_UNAVAILABLE"](
        f"{info['що']}\n   {info['що_робити']}{khvist}")


def journal_check(dysk: str = "/content/drive/MyDrive/TENDERWIN") -> dict:
    """
    Діагностика журналу. Нічого не змінює, нічого не створює.

    Каже словами, що саме заважає: вимкнений журнал, відсутня бібліотека,
    неувімкнений Sheets API, токен без потрібного дозволу.
    """
    db_path = os.path.join(dysk, "leads.db")
    print("ДІАГНОСТИКА ЖУРНАЛУ")
    print("-" * 66)
    out = {"готовий": False}

    def krok(ok, text, porada=""):
        print(f"   {'OK  ' if ok else 'НІ  '} {text}")
        if not ok and porada:
            for line in porada.splitlines():
                print(f"        {line}")
        return ok

    if not krok(JOURNAL_ENABLED, "журнал увімкнено",
                "Виконайте: E.JOURNAL_ENABLED = True"):
        return out
    try:
        import googleapiclient                                       # noqa: F401,PLC0415
        krok(True, "бібліотека google-api-python-client є")
    except ImportError:
        krok(False, "бібліотека google-api-python-client",
             "Запустіть клітинку 1 (Бібліотеки)")
        return out
    sid = journal_id(db_path)
    krok(bool(sid), f"журнал уже створено{': ' + sid if sid else ''}",
         "Ще не створено — це нормально, створиться сам.")
    try:
        service = _journal_service()
        if sid:
            _journal_header(service, sid)
            krok(True, "таблиця відкривається, заголовки читаються")
        else:
            service.spreadsheets().get(
                spreadsheetId="1" * 44).execute()
    except EngineError as err:
        krok(False, "доступ до Google Таблиць", err.what)
        return out
    except Exception as exc:                                        # noqa: BLE001
        info = _google_error(exc)
        if info["код"] == "JOURNAL_NOT_FOUND" and not sid:
            krok(True, "Sheets API відповідає (журналу ще немає — це нормально)")
        else:
            krok(False, f"доступ до Google Таблиць · {info['код']}",
                 info["що"] + "\n" + info["що_робити"]
                 + (f"\n{info['посилання']}" if info["посилання"] else ""))
            return out
    print("-" * 66)
    print("Журнал готовий до роботи.")
    out["готовий"] = True
    return out


def _journal_service():
    """Клієнт Google Sheets. Помилки — з кодом, а не з трейсбеком."""
    if not JOURNAL_ENABLED:
        raise ERR["JOURNAL_UNAVAILABLE"](
            "Журнал вимкнено.\n   Виконайте: E.JOURNAL_ENABLED = True")
    try:
        from googleapiclient.discovery import build                 # noqa: PLC0415
    except ImportError as exc:
        raise ERR["JOURNAL_UNAVAILABLE"](
            "Немає бібліотеки google-api-python-client."
            "\n   Запустіть клітинку 1 (Бібліотеки).") from exc
    cred = _oauth_credentials([SHEETS_SCOPE])
    return build("sheets", "v4", credentials=cred, cache_discovery=False)


def journal_create(db_path: str, title: str = "") -> dict:
    """
    Створює журнал ОДИН раз і запамʼятовує його ід поруч із базою.

    Файл створює саме движок — тоді вистачає найвужчого дозволу drive.file,
    і решта Вашого Диска для скрипта не існує. Далі таблиця Ваша: колонки
    можна перейменовувати, міняти місцями і додавати свої.
    """
    have = journal_id(db_path)
    if have:
        return {"spreadsheet_id": have, "статус": "УЖЕ_БУВ",
                "посилання": JOURNAL_URL.format(sid=have)}
    service = _journal_service()
    header = [name for name, _ in JOURNAL_COLUMNS] + JOURNAL_HUMAN_COLUMNS
    body = {
        "properties": {"title": title or JOURNAL_TITLE},
        "sheets": [{"properties": {"title": JOURNAL_SHEET,
                                   "gridProperties": {"frozenRowCount": 1}},
                    "data": [{"startRow": 0, "startColumn": 0, "rowData": [
                        {"values": [{"userEnteredValue":
                                     {"stringValue": h}} for h in header]}]}]}],
    }
    try:
        created = service.spreadsheets().create(body=body).execute()
    except EngineError:
        raise
    except Exception as exc:                                        # noqa: BLE001
        # Сирий HttpError на пів екрана — це не повідомлення для людини.
        raise _journal_fail(exc) from exc
    sid = created.get("spreadsheetId") or ""
    if not sid:
        raise ERR["JOURNAL_UNAVAILABLE"](
            "Google створив таблицю, але не повернув її ідентифікатор."
            "\n   Спробуйте ще раз за хвилину.")
    try:
        with open(_journal_state_path(db_path), "w", encoding="utf-8") as fh:
            json.dump({"spreadsheet_id": sid, "створено": now_iso(),
                       "движок": ENGINE_VERSION}, fh, ensure_ascii=False,
                      indent=1)
    except OSError as exc:
        # Таблиця вже є, але ми не змогли запамʼятати ід. Мовчати не можна:
        # наступний прогін створить ДРУГУ таблицю (правило 14).
        raise ERR["JOURNAL_UNAVAILABLE"](
            f"Таблицю створено ({sid}), але її ідентифікатор не записався"
            f" на Диск: {exc}"
            f"\n   Без цього наступний прогін створить ДРУГУ таблицю."
            f"\n   Перевірте, що Диск підключений, і запустіть ще раз.")
    return {"spreadsheet_id": sid, "статус": "СТВОРЕНО",
            "посилання": JOURNAL_URL.format(sid=sid)}


JOURNAL_URL = "https://docs.google.com/spreadsheets/d/{sid}/edit"


def _journal_header(service, sid: str) -> list:
    got = service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{JOURNAL_SHEET}!1:1").execute()
    rows = got.get("values") or []
    return [str(x).strip() for x in (rows[0] if rows else [])]


def _journal_keys(service, sid: str, key_col: int) -> dict:
    """Ключ рядка -> номер рядка в таблиці (1-based)."""
    letter = _col_letter(key_col)
    got = service.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{JOURNAL_SHEET}!{letter}2:{letter}").execute()
    out = {}
    for i, row in enumerate(got.get("values") or [], start=2):
        key = str(row[0]).strip() if row else ""
        if key and key not in out:
            out[key] = i
    return out


def _col_letter(index0: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n, out = index0 + 1, ""
    while n:
        n, rest = divmod(n - 1, 26)
        out = chr(65 + rest) + out
    return out


def journal_write(db_path: str, root_dir: str = "",
                  rejection_ids=None) -> dict:
    """
    Дописує/оновлює рядки журналу за справами прогону.

    Ніколи не кидає виняток назовні: прогін важливіший за вітрину.
    Повертає {"статус": ..., "додано": n, "оновлено": n, "посилання": ...}.
    """
    stats = {"статус": "ПРОПУЩЕНО", "додано": 0, "оновлено": 0,
             "посилання": "", "чому": "", "код": ""}
    if not JOURNAL_ENABLED:
        stats["чому"] = "журнал вимкнено (JOURNAL_ENABLED = False)"
        return stats
    try:
        service = _journal_service()
        sid = journal_id(db_path) or journal_create(db_path)["spreadsheet_id"]
        stats["посилання"] = JOURNAL_URL.format(sid=sid)
        header = _journal_header(service, sid)
        if not header:
            stats["статус"] = "ПОМИЛКА"
            stats["чому"] = "у таблиці немає рядка заголовків"
            return stats
        if JOURNAL_KEY_COLUMN not in header:
            stats["статус"] = "ПОМИЛКА"
            stats["чому"] = (f"немає колонки «{JOURNAL_KEY_COLUMN}» — "
                             f"без неї повторний прогін дублював би рядки")
            return stats

        rows = _journal_source_rows(db_path, root_dir, rejection_ids)
        if not rows:
            stats["статус"] = "НЕМАЄ_ЧОГО_ПИСАТИ"
            return stats

        # Тільки ті колонки, які движок ЗНАЄ і які людина лишила в таблиці.
        znayomi = {name: idx for idx, name in enumerate(header)
                   if name in {n for n, _ in JOURNAL_COLUMNS}}
        obchysly = dict(JOURNAL_COLUMNS)
        key_col = header.index(JOURNAL_KEY_COLUMN)
        existing = _journal_keys(service, sid, key_col)

        updates, appends = [], []
        for r in rows:
            values = {}
            for name, idx in znayomi.items():
                try:
                    values[idx] = obchysly[name](r)
                except Exception:                                  # noqa: BLE001
                    values[idx] = NOT_ESTABLISHED
            key = values.get(key_col, "")
            if key in existing:
                line = existing[key]
                for idx, val in values.items():
                    if val in ("", None):
                        continue      # порожнім значенням нічого не стираємо
                    letter = _col_letter(idx)
                    updates.append({"range": f"{JOURNAL_SHEET}!{letter}{line}",
                                    "values": [[val]]})
                stats["оновлено"] += 1
            else:
                line = [""] * len(header)
                for idx, val in values.items():
                    line[idx] = val
                appends.append(line)

        if updates:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "USER_ENTERED",
                      "data": updates}).execute()
        if appends:
            service.spreadsheets().values().append(
                spreadsheetId=sid, range=f"{JOURNAL_SHEET}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": appends}).execute()
            stats["додано"] = len(appends)
        stats["статус"] = "ЗАПИСАНО"
        return stats
    except EngineError as err:
        stats["статус"] = "ПОМИЛКА"
        stats["чому"] = err.what
        return stats
    except Exception as exc:                                       # noqa: BLE001
        info = _google_error(exc)
        stats["статус"] = "ПОМИЛКА"
        stats["код"] = info["код"]
        stats["чому"] = (info["що"] + " " + info["що_робити"]
                         + (f" {info['посилання']}" if info["посилання"] else ""))
        return stats


def _journal_source_rows(db_path: str, root_dir: str, rejection_ids) -> list:
    """Рядки для журналу. Той самий фільтр по прогону, що й у карток."""
    where, args = _rejection_filter(rejection_ids)
    db = Db(db_path)
    try:
        rows = db.q(
            "SELECT t.ua_id, t.tender_title, t.buyer_name,"
            "       c.company_name, c.edrpou_norm,"
            "       r.rejection_date, r.rejection_reason_raw,"
            "       r.lot_value_amount, r.bid_amount, r.winner_amount,"
            "       r.complaint_deadline, r.notice_24h,"
            "       r.amcu_fee_status, r.amcu_fee_calculated,"
            "       r.amcu_fee_reason,"
            "       ct.contact_name_raw, ct.contact_phone, ct.contact_email,"
            "       o.status, o.mode, o.delivery_email_actual"
            "  FROM rejections r"
            "  JOIN tenders   t  ON t.tender_id = r.tender_id"
            "  JOIN companies c  ON c.company_id = r.company_id"
            "  LEFT JOIN leads l ON l.rejection_id = r.rejection_id"
            "  LEFT JOIN contacts ct ON ct.contact_id = l.contact_id"
            "  LEFT JOIN outreach_events o ON o.lead_id = l.lead_id"
            + where + " ORDER BY t.ua_id, c.company_name", args)
        out = []
        for r in rows:
            d = dict(r)
            d["_folder"] = (case_archive(root_dir, d["ua_id"])
                            if root_dir else "")
            out.append(d)
        return out
    finally:
        db.close()
