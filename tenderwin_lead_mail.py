# -*- coding: utf-8 -*-
"""
=============================================================================
 TENDERWIN LEAD & MAIL v1.5.0 · ЛІДИ, ЛИСТИ І СЛУЖБОВІ КАРТКИ
=============================================================================

 ЩО ЦЕ

 Новий, окремий генератор лідів і листів, написаний за майстер-промптом v1.0
 (`36_MASTER_PROMPT_LEAD_MAIL_v1.0.md`). Він не робить аналізу і не готує
 скарг. Його робота — знайти сьогоднішні відхилення, скласти лист, покласти
 поруч робочу картку закупівлі, а з версії 1.3.0 — ще й службову картку
 відхилення з протоколом рішення замовника.

 ЧОТИРИ ГАРАНТІЇ

 1. У розсилку потрапляють лише події вікна прогону. Старе відхилення не
    стає сьогоднішнім через нову редакцію документа, початок stand-still
    чи відхилення іншого учасника.
 2. Одна компанія отримує один перший лист. Назавжди, з урахуванням
    історії попереднього движка.
 3. Лист іде лише туди, куди дозволяє режим, і це перевіряє тригер бази:
    TEST — тільки на адреси зі списку TEST_RECIPIENTS; LIVE (з 1.5.0) —
    тільки на адресу самої компанії з Prozorro. Тестові листи не займають
    бойового першого листа.
 4. Завантажуються ЛИШЕ документи самого рішення замовника (протокол,
    повідомлення про невідповідності) — для службової картки. Документи
    пропозиції і тендерна документація не завантажуються: у робочій картці
    лишаються їхні назви й посилання.

 ЩО ВЗЯТО З ПЕРЕВІРЕНОГО КОДУ (правило 10 — робоче не переписуємо)

   lead_machine_v1        розбір ПІБ, кличний відмінок, контакти учасника,
                          мережева сесія Prozorro
   tenderwin_lead_engine  шлюз «чи це взагалі ім'я», збірка RFC822,
                          доступ до Gmail, нормалізація ЄДРПОУ

 ЩО ЗМІНИЛОСЯ В 1.1.0 (рішення власника 22.09.2026)

   лист V8_OWNER    чинний текст + HTML-оформлення; той самий текст лишається
                    в листі другою частиною для клієнтів без HTML
   без строку —     закупівлі, де в Prozorro немає періоду оскарження, листа
   без листа        не отримують (питання П-2 закрите)

 ЩО ЗМІНИЛОСЯ В 1.3.0 (рішення власника 04.10.2026)

   службові картки  після листів M.go() бере свіжий стан кожного рішення,
                    завантажує його документи (протокол), витягує текст
                    посторінково і кладе «Службову картку відхилення» в теку
                    події: <TenderWin>/Службові картки/<дата>/<UA-ID>__<код>__<id>/
   перелік          у теці дня — Excel-перелік усіх подій прогону з
                    дослівними фрагментами: з нього відбирають справи
   M.kartky()       службові картки ще раз, без пошуку і листів: за останній
                    прогін, за конкретний день або прогін
   модуль           уся робота з документами — в окремому файлі
                    tenderwin_service_cards.py; без нього листи працюють

 ЩО ЗМІНИЛОСЯ В 1.4.0 (рішення власника 06.10.2026)

   теки справ       службова картка і протокол (рішення) замовника лягають
                    у «Мій диск/0 Cases» (на компʼютері G:\\Мій диск\\0 Cases),
                    у підтеку «<ID закупівлі> <ЄДРПОУ/ІПН учасника>»;
                    перелік для відбору — як і раніше, у TenderWin/Службові
                    картки/<дата>/
   лист V9_OWNER    V8 з правками власника: новий абзац про зіставлення і
                    про достатність аналізу, посилання на приклад аналізу
                    замість «відповідайте на цей лист», підпис без
                    посилань і з номером +380 800 357 135

 ЩО ЗМІНИЛОСЯ В 1.5.0 (рішення власника 08.10.2026)

   бойовий режим    MODE = "LIVE": лист іде на адресу компанії з Prozorro.
                    Усі попередні листи були тестовими і бойового першого
                    листа не займають; обмеження діє лише після бойового.
                    Тригер бази не пускає бойовий лист на іншу адресу.
   події з тесту    подія, яку вже бачив тестовий прогін, у бойовому
                    прогоні знову йде в розсилку (якщо вона у вікні)
   адреса           e-mail із помилкою — на ручний перегляд, не в розсилку
   лист V10_OWNER   V9 з телефоном «0 800 357 135»

 ЧОГО ТУТ НЕМАЄ СВІДОМО

   завантаження документів пропозиції і ТД · OCR · будь-який LLM ·
   бойовий режим · друга хвиля · чернетки скарг · Snov.io

 ЗАПУСК У COLAB

   M.setup(DYSK)    один раз за сеанс: база, токен, перевірки
   M.go()           сухий прогін: шукає, складає листи у файли, службові картки
   M.kartky()       службові картки ще раз (за прогін або за день)
   M.send()         відправка: TEST — на ваші скриньки, LIVE — клієнтам
   M.state()        що зараз у базі
=============================================================================
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import lead_machine_v1 as L                 # ядро: Prozorro, ПІБ, кличний
import tenderwin_lead_engine as E           # перевірені шлюзи й доступ до Gmail

VERSION = "1.5.0"
BUILD = "2026-10-08"
SCHEMA_VERSION = 2

# ============================================================================
#  БЛОК 0. НАЛАШТУВАННЯ
# ============================================================================

# --- Режим ------------------------------------------------------------------
#   "TEST" — листи йдуть лише на ваші скриньки з TEST_RECIPIENTS;
#   "LIVE" — бойовий (з 1.5.0, рішення власника 08.10.2026): лист іде на
#            адресу самої компанії з Prozorro. Тестові листи бойового першого
#            листа не займають. Надсилає M.send() (клітинка 7) одразу.
# Тут — безпечне значення за замовчуванням; бойовий режим вмикає ноутбук
# (клітинка 5) одним рядком M.MODE = "LIVE".
MODE = "TEST"

#: Куди дозволено доставляти листи в тестовому режимі. Порожній список
#: зупиняє тестову роботу — це стан спокою, а не привід написати клієнтам.
#: У бойовому режимі цей список не використовується.
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

SENDER_EMAIL = "vitalii@tenderwin.com.ua"
SENDER_NAME = "Віталій Щасливий"

# --- Вікно прогону ----------------------------------------------------------
# "TODAY_ONLY"      — буквально рішення власника: лише поточна доба за Києвом.
# "SINCE_LAST_RUN"  — від завершення попереднього прогону до зараз.
#
# При одному запуску на добу TODAY_ONLY означає, що відхилення, опубліковані
# після запуску, не потраплять у розсилку ніколи. Скрипт це не ховає: у
# підсумку окремим рядком друкується, скільки таких подій пропущено.
WINDOW_MODE = "TODAY_ONLY"

#: Події з надійністю часу MEDIUM (єдине джерело дати). Кваліфікації інакше
#: не відбираються взагалі. Підтвердити на реальних даних — тест Т-15.
ALLOW_MEDIUM_RELIABILITY = True

#: Розбіжність award.date і complaintPeriod.startDate, яку ще вважаємо збігом.
EVENT_TIME_TOLERANCE_SECONDS = 600

# --- Охоплення --------------------------------------------------------------
# Рішення власника: усі галузі й усі закупівлі без винятку. Фільтра за CPV
# і за вартістю немає. Лишається лише пропуск стадій, на яких відхилення
# не існує за визначенням.
SKIP_TENDER_STATUSES = {"draft", "active.enquiries", "active.tendering"}

# --- Які процедури взагалі обробляємо ----------------------------------------
# РІШЕННЯ ВЛАСНИКА 23.09.2026: допорогові та спрощені закупівлі не беремо —
# відхилення в них не оскаржується в АМКУ, і лист пропонував би шлях, якого
# немає.
#
# Перелік НАВМИСНО дозвільний, а не заборонний: якщо в Prozorro зʼявиться
# новий тип процедури, він не потрапить у розсилку мовчки. Такі закупівлі
# видно у звіті окремим рядком із назвою типу — далі рішення за вами.
#
# LEGAL_BASIS_NOT_VERIFIED: перелік складено за призначенням процедур, а не
# за звіркою з чинною редакцією закону на дату конкретної закупівлі
# (правило 7). Перед розширенням — звіряти з нормою, а не з цим коментарем.
APPEALABLE_METHOD_TYPES = {
    "aboveThreshold",                     # відкриті торги (до 2022)
    "aboveThresholdUA",                   # відкриті торги
    "aboveThresholdEU",                   # відкриті торги з публікацією EN
    "aboveThresholdUA.defense",           # оборонні, відкриті торги
    "competitiveDialogueUA",
    "competitiveDialogueUA.stage2",
    "competitiveDialogueEU",
    "competitiveDialogueEU.stage2",
    "closeFrameworkAgreementUA",          # рамкова угода
    "closeFrameworkAgreementSelectionUA",
    "esco",
    "negotiation",                        # переговорна процедура
    "negotiation.quick",
}

#: Типи, які свідомо пропускаємо і яких не питаємо в Prozorro взагалі.
#: Перелічені окремо лише для зрозумілого звіту.
KNOWN_SKIPPED_METHOD_TYPES = {
    "belowThreshold": "допорогова (спрощена) закупівля",
    "priceQuotation": "запит ціни пропозицій",
    "reporting": "звіт про договір без використання системи",
    "simple.defense": "спрощені торги (оборонні)",
}

#: Мінімальна очікувана вартість лота. 0 = фільтра немає (рішення власника).
MIN_LOT_VALUE = 0

#: Відхилення, у якого в даних Prozorro немає періоду оскарження взагалі.
#: РІШЕННЯ ВЛАСНИКА 22.09.2026 (питання П-2 закрите): таким компаніям НЕ
#: пишемо. Лист пропонує аналіз доцільності оскарження, а там, де шляху
#: оскарження немає, обіцяти його не можна.
#:   True  — лист не складається; лід видно у звіті як NO_COMPLAINT_ROUTE
#:           і в робочій картці, рішення за вами;
#:   False — лист складається так само, як для решти закупівель.
SKIP_WITHOUT_COMPLAINT_ROUTE = True

# --- Мережа -----------------------------------------------------------------
API = "https://public-api.prozorro.gov.ua/api/2.5/tenders"
FEED_LIMIT = 1000
MAX_FEED_PAGES = 400
SCAN_WORKERS = 12
HTTP_TRIES = 4
#: Пул зʼєднань не менший за кількість потоків. Із пулом 10 на 12 потоків
#: urllib3 сипав «Connection pool is full» (прогін 02.10.2026).
HTTP_POOL_SIZE = 32

# --- Листи ------------------------------------------------------------------
# Незмінні версії. Активну перемикає один рядок.
#   "V10_OWNER"  — ЧИННИЙ текст (08.10.2026): V9 з телефоном «0 800 357 135»
#   "V9_OWNER"   — текст 06.10.2026: V8 з правками власника — посилання
#                  на приклад аналізу, підпис без посилань,
#                  телефон +380 800 357 135
#   "V8_OWNER"   — текст 23.09.2026: назва й код компанії, точні
#                  дата й час відхилення та строку оскарження
#   "V7_OWNER"   — V6 з помʼякшеним реченням про самостійну скаргу
#   "V6_OWNER"   — текст, затверджений 22.09.2026 (з HTML)
#   "V4_OWNER"   — перший текст власника того самого дня
#   "V5_REDTEAM" — редакція після red team (додаток А.2 майстер-промпту)
LETTER_VERSION = "V10_OWNER"

#: Рядок про строк оскарження. Додається лише за наявності підтвердженої дати.
SHOW_COMPLAINT_DEADLINE = True

#: Як виглядає лист у скриньці клієнта.
#:   "HTML_AND_TEXT" — дві частини в одному листі: оформлений HTML і той самий
#:                     текст як заміна для клієнтів, що HTML не показують;
#:   "TEXT_ONLY"     — лише текст (менший ризик потрапити у «Промоакції»).
#: Рішення власника 22.09.2026: лист має бути зручним для читання.
LETTER_FORMAT = "HTML_AND_TEXT"

#: Сайт у підписі. Домен розсилки (tenderwin.com.ua) і домен сайту різні —
#: у листі має бути саме сайт.
SITE_URL = "https://tenderwin.in.ua"
SITE_LABEL = "tenderwin.in.ua"
#: Телефон у підписі версій V5–V8. У V9 підпис затверджений цілком, разом
#: із номером +380 800 357 135, і записаний у самому шаблоні.
PHONE_TEXT = "+380 50 310 14 92"
PHONE_TEL = "+380503101492"

#: V9: «Побачити, як виглядає мій аналіз, Ви можете тут: …». Сюди можна
#: поставити сторінку з прикладом аналізу, коли вона буде на сайті.
ANALYSIS_EXAMPLE_URL = SITE_URL
ANALYSIS_EXAMPLE_LABEL = SITE_LABEL

#: Рядок «відповідайте «стоп»». Вмикати лише тоді, коли відповіді реально
#: читаються (у цій версії читання скриньки немає).
SHOW_OPT_OUT_LINE = False

# --- Темп і обсяг -----------------------------------------------------------
MIN_SEND_DELAY_SECONDS = 120
MAX_SEND_DELAY_SECONDS = 180
MAX_SENDS_PER_RUN = 25

# --- Службові картки (з 1.3.0; рішення власника 04.10.2026) ----------------
#: Після листів M.go() завантажує документи самого рішення замовника
#: (протокол, повідомлення) і кладе «Службову картку відхилення» в теку
#: події. Документи пропозиції і ТД не завантажуються.
SERVICE_CARDS = True
#: Перед завантаженням брати свіжий стан рішення з Prozorro: документи,
#: додані після сканування, поточний статус рішення, скарги.
SERVICE_CARDS_FRESH = True
#: Тека справ (рішення власника 06.10.2026): у ній на кожне відхилення
#: підтека «<ID закупівлі> <ЄДРПОУ/ІПН учасника>» зі службовою карткою і
#: протоколом (рішенням) замовника. Це «G:\Мій диск\0 Cases» на компʼютері
#: і «/content/drive/MyDrive/0 Cases» у Colab — той самий «Мій диск».
CASES_DIRNAME = "0 Cases"
#: Повний шлях до теки справ, якщо треба інший. Порожньо — «0 Cases» у
#: корені «Мого диска», знайденого автоматично (див. cases_root()).
CASES_DIR = ""
#: Як називається корінь «Мого диска» в шляху: Colab, Google Диск для
#: компʼютера англійською й українською.
MY_DRIVE_NAMES = ("MyDrive", "My Drive", "Мій диск")
#: Де шукати «Мій диск», якщо робоча тека лежить не на ньому.
MY_DRIVE_ROOTS = ("/content/drive/MyDrive", "G:\\Мій диск", "G:\\My Drive")
#: Тека переліків для відбору (.xlsx) і журналів помилок карток — усередині
#: робочої теки TenderWin, як і раніше. У «0 Cases» лежать лише справи.
CARDS_DIRNAME = "Службові картки"

# --- Де живе стан -----------------------------------------------------------
DB_PATH = "tenderwin_lead_mail.db"
OUT_DIR = "vyhid_lystiv"
#: Тека переліків службових карток. M.setup(DYSK) і M.go(dysk=DYSK)
#: ставлять її всередину робочої теки на Диску.
CARDS_DIR = CARDS_DIRNAME
#: Робоча тека TenderWin (DYSK), від якої шукається «Мій диск».
WORK_DIR = ""
#: База попереднього движка. Потрібна один раз, щоб не написати «перший»
#: лист тим, кому вже писали.
LEGACY_DB_PATH = "leads.db"

KYIV = ZoneInfo("Europe/Kyiv")
PROZORRO_TENDER_URL = "https://prozorro.gov.ua/tender/{ua_id}"
NOT_ESTABLISHED = "НЕ ВСТАНОВЛЕНО"


# ============================================================================
#  БЛОК 1. ЧАС, ВІКНО ПРОГОНУ, ІДЕНТИФІКАТОРИ
# ============================================================================
def now() -> datetime:
    return datetime.now(KYIV)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """
    Початок і кінець доби за Києвом.

    Межі рахуються від місцевої півночі, а не додаванням 24 годин: у дні
    переходу на літній і зимовий час доба має 23 і 25 годин, і зсув на
    годину означав би пропущені або подвоєні події.
    """
    start = day.astimezone(KYIV).replace(hour=0, minute=0, second=0, microsecond=0)
    next_date = (start.date() + timedelta(days=1))
    end = datetime.combine(next_date, dtime(0, 0), tzinfo=KYIV)
    if span_hours(start, end) <= 0:       # захист від патологічного tzdata
        end = start + timedelta(days=1)
    return start, end


def span_hours(start: datetime, end: datetime) -> float:
    """
    Скільки реальних годин між двома мітками.

    Пряме віднімання тут непридатне: якщо в обох дат однаковий об'єкт tzinfo,
    Python ігнорує зсуви і рахує різницю за настінним годинником. У дні
    переходу часу це дало б 24 години замість 23 або 25. Тому обидві мітки
    зводимо до UTC.
    """
    delta = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    return delta.total_seconds() / 3600


@dataclass
class Window:
    """Вікно прогону: [start; end) і чим воно пояснюється людині."""
    start: datetime
    end: datetime
    mode: str
    tail_start: Optional[datetime] = None      # хвіст попередньої доби

    def contains(self, moment: Optional[datetime]) -> bool:
        return bool(moment) and self.start <= moment < self.end

    def in_tail(self, moment: Optional[datetime]) -> bool:
        """Подія, яку TODAY_ONLY відкидає, а SINCE_LAST_RUN узяв би."""
        if not moment or self.tail_start is None:
            return False
        return self.tail_start <= moment < self.start

    def hours(self) -> float:
        """Реальна довжина вікна. Вікно зазвичай обрізане поточним часом."""
        return span_hours(self.start, self.end)

    def day_hours(self) -> float:
        """Довжина календарної доби, якій належить вікно: 23, 24 або 25."""
        return span_hours(*day_bounds(self.start))

    def human(self) -> str:
        підпис = ("поточна доба" if self.mode == "TODAY_ONLY"
                  else "від попереднього прогону")
        текст = (f"{self.start.strftime('%d.%m.%Y %H:%M')} — "
                 f"{self.end.strftime('%d.%m.%Y %H:%M')} ({підпис})")
        # Примітка про перехід часу рахується від календарної доби, а не від
        # довжини вікна: вікно майже завжди коротше доби, бо закінчується
        # поточним часом, і плутати одне з одним не можна.
        доба = self.day_hours()
        if self.mode == "TODAY_ONLY" and abs(доба - 24) > 0.01:
            текст += f"; доба {доба:g} год — перехід часу"
        return текст


def build_window(prev_run_end: Optional[str], day: Optional[str] = None,
                 mode: str = "") -> Window:
    """Вікно прогону за налаштуванням і за часом попереднього прогону."""
    mode = mode or WINDOW_MODE
    moment = now()
    if day:
        base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KYIV)
        start, end = day_bounds(base)
        return Window(start=start, end=min(end, moment) if base.date() == moment.date() else end,
                      mode="TODAY_ONLY", tail_start=None)
    start_today, _ = day_bounds(moment)
    prev = parse_dt(prev_run_end)
    if mode == "SINCE_LAST_RUN" and prev:
        return Window(start=prev, end=moment, mode=mode, tail_start=None)
    return Window(start=start_today, end=moment, mode="TODAY_ONLY",
                  tail_start=prev if prev and prev < start_today else None)


def parse_dt(value: Any) -> Optional[datetime]:
    """
    Розбір часу з Prozorro. Значення без часового поясу не приймається:
    зсув на дві-три години міг би перекинути подію через межу доби.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def ua_date_short(moment: Optional[datetime]) -> str:
    if not moment:
        return ""
    місяці = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
    local = moment.astimezone(KYIV)
    return f"{local.day} {місяці[local.month - 1]}"


def ua_datetime_full(moment: Optional[datetime]) -> str:
    """
    «23 вересня 2026 року о 14:37» — за київським часом.

    Час у листі має збігатися з тим, що людина бачить у Prozorro, тому
    зсув беремо з tzdata, а не рахуємо руками.
    """
    if not moment:
        return ""
    місяці = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
    місцевий = moment.astimezone(KYIV)
    прийменник = "об" if місцевий.hour == 11 else "о"
    return (f"{місцевий.day} {місяці[місцевий.month - 1]} {місцевий.year} року "
            f"{прийменник} {місцевий:%H:%M}")


def code_label(code: Optional[str]) -> str:
    """ЄДРПОУ — 8 цифр, ІПН/РНОКПП — 10. Інше не називаємо навмання."""
    цифри = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(цифри) == 8:
        return "ЄДРПОУ"
    if len(цифри) == 10:
        return "ІПН"
    return "код"


# ============================================================================
#  БЛОК 2. ПОМИЛКИ: КОД, ЩО СТАЛОСЬ, ЩО ЗАЧЕПЛЕНО, ЧИ МОЖНА ДАЛІ, ЩО РОБИТИ
# ============================================================================
class MailError(RuntimeError):
    def __init__(self, code: str, what: str, affected: str,
                 may_continue: bool, next_step: str):
        super().__init__(f"[{code}] {what}")
        self.code, self.what = code, what
        self.affected, self.may_continue, self.next_step = affected, may_continue, next_step

    def render(self) -> str:
        return (f"\n  КОД: {self.code}\n  ЩО СТАЛОСЬ: {self.what}\n"
                f"  ЩО ЗАЧЕПЛЕНО: {self.affected}\n"
                f"  ЧИ МОЖНА ДАЛІ: {'так' if self.may_continue else 'ні'}\n"
                f"  ЩО РОБИТИ: {self.next_step}\n")


def _err(code, what, affected, may_continue, next_step) -> MailError:
    return MailError(code, what, affected, may_continue, next_step)


ERR = {
    "MODE_UNKNOWN": lambda m: _err(
        "MODE_UNKNOWN", f"Невідомий режим «{m}»",
        "Уся робота", False, 'Допустимо лише MODE = "TEST" або MODE = "LIVE".'),
    "LIVE_DELIVERY_NOT_CONTACT": lambda a: _err(
        "LIVE_DELIVERY_NOT_CONTACT", f"Бойовий лист не на адресу компанії: «{a}»",
        "Один лист, НЕ складено", True,
        "Спрацював запобіжник бази: у бойовому режимі лист іде лише на "
        "адресу самої компанії з Prozorro."),
    "TEST_ALLOWLIST_EMPTY": lambda: _err(
        "TEST_ALLOWLIST_EMPTY", "Список тестових адрес порожній",
        "Уся відправка", False,
        "Заповніть TEST_RECIPIENTS. Порожній список навмисно зупиняє роботу."),
    "TEST_RECIPIENT_NOT_ALLOWED": lambda a: _err(
        "TEST_RECIPIENT_NOT_ALLOWED", f"Доставка на «{a}» заборонена",
        "Один лист, НЕ надіслано", True,
        "Адреси немає в TEST_RECIPIENTS. Це спрацював запобіжник бази."),
    "STORAGE_UNAVAILABLE": lambda d: _err(
        "STORAGE_UNAVAILABLE", f"База недоступна: {d}",
        "Уся робота", False,
        "Без бази немає дедуплікації. Краще пропущений день, ніж день без неї."),
    "SCHEMA_MISMATCH": lambda f, x: _err(
        "SCHEMA_MISMATCH", f"Версія схеми {f}, скрипт очікує {x}",
        "Уся робота з базою", False, "Вкажіть іншу базу або оновіть скрипт."),
    "PROZORRO_UNAVAILABLE": lambda d: _err(
        "PROZORRO_UNAVAILABLE", f"Prozorro не відповідає: {d}",
        "Пошук відхилень", False, "Спробуйте пізніше; обхід не почався."),
    "TRANSPORT_UNAVAILABLE": lambda d: _err(
        "TRANSPORT_UNAVAILABLE", f"Немає доступу до пошти: {d}",
        "Відправка", False,
        "Токен має бути або в Colab Secrets, або файлом .gmail_token.json "
        "у теці бази. Виконайте M.setup(DYSK); якщо токена ще немає — "
        "E.mint_oauth_token()."),
    "IDENTITY_NOT_ESTABLISHED": lambda d: _err(
        "IDENTITY_NOT_ESTABLISHED", f"Не встановлено ЄДРПОУ/ІПН: {d}",
        "Один лід", True, "Лід іде на ручний перегляд; ключем компанії може "
        "бути лише код, не e-mail і не назва."),
    "SEND_FAILED": lambda d: _err(
        "SEND_FAILED", f"Gmail відхилив відправку: {d}",
        "Один лист", True, "Статус SEND_FAILED, повтор дозволено в межах партії."),
    "DELIVERY_UNKNOWN": lambda o: _err(
        "DELIVERY_UNKNOWN", f"Невідомо, чи пішов лист {o}",
        "Один лист", True, "Виконується звірка; сліпого повтору не буде."),
}


# ============================================================================
#  БЛОК 3. СХЕМА БАЗИ
# ----------------------------------------------------------------------------
#  Обмеження стоять у базі, а не в коді: перевірку «if row exists» можна
#  забути, обійти або зламати гонкою. Індекс і тригер — ні.
# ============================================================================
SCHEMA_SQL = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ---------- прогони ---------------------------------------------------------
CREATE TABLE runs (
  run_id        TEXT PRIMARY KEY,
  mode          TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  window_start  TEXT NOT NULL,
  window_end    TEXT NOT NULL,
  window_mode   TEXT NOT NULL,
  scan_state    TEXT,                -- COMPLETE / INCOMPLETE
  scan_note     TEXT
);

-- ---------- компанія: ключ тільки ЄДРПОУ/ІПН --------------------------------
CREATE TABLE companies (
  company_id   TEXT PRIMARY KEY,
  edrpou_norm  TEXT NOT NULL,
  company_name TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_companies_edrpou ON companies(edrpou_norm);

CREATE TABLE contacts (
  contact_id          TEXT PRIMARY KEY,
  company_id          TEXT NOT NULL REFERENCES companies(company_id),
  contact_email       TEXT,
  contact_name_raw    TEXT,
  contact_phone       TEXT,
  contact_vocative    TEXT,
  vocative_confidence TEXT,
  name_source         TEXT,
  created_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_contacts ON contacts(company_id, COALESCE(contact_email, ''));

-- ---------- закупівля -------------------------------------------------------
CREATE TABLE tenders (
  tender_id     TEXT PRIMARY KEY,
  ua_id         TEXT NOT NULL,
  title         TEXT,
  buyer_name    TEXT,
  buyer_edrpou  TEXT,
  cpv           TEXT,
  method_type   TEXT,
  tender_status TEXT,
  tender_value  REAL,
  retrieved_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_tenders_uaid ON tenders(ua_id);

-- ---------- подія відхилення ≠ закупівля, ≠ компанія ------------------------
CREATE TABLE events (
  event_id               TEXT PRIMARY KEY,
  run_id                 TEXT,
  tender_id              TEXT NOT NULL REFERENCES tenders(tender_id),
  company_id             TEXT NOT NULL REFERENCES companies(company_id),
  stage                  TEXT NOT NULL,        -- awards / qualifications
  object_id              TEXT NOT NULL,
  bid_id                 TEXT,
  lot_id                 TEXT,
  event_time             TEXT NOT NULL,
  event_time_source      TEXT NOT NULL,
  event_time_basis       TEXT NOT NULL,
  event_time_reliability TEXT NOT NULL,        -- HIGH / MEDIUM / CONFLICT / UNDETERMINED
  object_status          TEXT NOT NULL,
  status_checked_at      TEXT,
  reason_raw             TEXT,
  reason_source          TEXT,
  tender_value           REAL,
  lot_value              REAL,
  lot_resolved           TEXT,                 -- YES / NO / NO_LOTS
  bid_value              REAL,
  winner_value           REAL,
  winner_name            TEXT,
  complaint_start        TEXT,
  complaint_end          TEXT,
  docs_decision          TEXT,                 -- JSON: назви й посилання, БЕЗ завантаження
  docs_bid               TEXT,
  docs_td                TEXT,
  joint_bid              INTEGER NOT NULL DEFAULT 0,
  identity_note          TEXT,
  discovered_at          TEXT NOT NULL,
  state                  TEXT NOT NULL,        -- NEW / IN_LETTER / REVIEW / NOT_ELIGIBLE / NO_COMPLAINT_ROUTE
  state_reason           TEXT
);
CREATE UNIQUE INDEX ux_event_identity ON events(
  tender_id, stage, object_id, COALESCE(bid_id, ''), COALESCE(lot_id, ''));

-- ---------- звернення -------------------------------------------------------
CREATE TABLE outreach (
  outreach_id            TEXT PRIMARY KEY,
  run_id                 TEXT,
  batch_id               TEXT,
  company_id             TEXT NOT NULL REFERENCES companies(company_id),
  contact_id             TEXT REFERENCES contacts(contact_id),
  outreach_type          TEXT NOT NULL CHECK (outreach_type IN ('FIRST_TOUCH')),
  mode                   TEXT NOT NULL CHECK (mode IN ('TEST', 'LIVE')),
  status                 TEXT NOT NULL CHECK (status IN (
                            'QUEUED', 'SENDING', 'SENT', 'SENT_CONFIRMED',
                            'SEND_FAILED', 'DELIVERY_UNKNOWN', 'CANCELLED',
                            'MIGRATED_UNVERIFIED', 'LEGACY_SENT')),
  origin                 TEXT NOT NULL DEFAULT 'LEAD_MAIL',
  template_version       TEXT NOT NULL,
  subject_rendered       TEXT NOT NULL,
  body_rendered          TEXT NOT NULL,
  body_html              TEXT,                 -- HTML-частина листа, якщо була
  contact_email_original TEXT,
  delivery_email_actual  TEXT NOT NULL,
  message_id_header      TEXT NOT NULL,
  provider_message_id    TEXT,
  provider_thread_id     TEXT,
  provider_timestamp     TEXT,
  event_ids              TEXT NOT NULL,        -- JSON: про які події цей лист
  generated_at           TEXT NOT NULL,
  sent_at                TEXT,
  error_code             TEXT,
  error_detail           TEXT
);
CREATE UNIQUE INDEX ux_message_id ON outreach(message_id_header);

-- ГОЛОВНЕ ОБМЕЖЕННЯ: одна компанія — один перший лист у межах режиму.
-- Скасовані й невдалі виходять з індексу: повтор у межах партії дозволений.
CREATE UNIQUE INDEX ux_first_touch ON outreach(company_id, outreach_type, mode)
  WHERE status IN ('QUEUED', 'SENDING', 'SENT', 'SENT_CONFIRMED',
                   'DELIVERY_UNKNOWN', 'MIGRATED_UNVERIFIED', 'LEGACY_SENT');

-- ---------- стоп-лист -------------------------------------------------------
CREATE TABLE suppressions (
  suppression_id TEXT PRIMARY KEY,
  company_id     TEXT REFERENCES companies(company_id),
  email          TEXT,
  state          TEXT NOT NULL CHECK (state IN ('OPTED_OUT', 'DO_NOT_CONTACT',
                                                'INVALID_EMAIL', 'BOUNCED')),
  reason         TEXT,
  source         TEXT,
  created_at     TEXT NOT NULL
);

-- ---------- білий список тестових адрес ------------------------------------
CREATE TABLE test_allowlist (email TEXT PRIMARY KEY, added_at TEXT NOT NULL);

-- ---------- закупівлі, які не вдалося прочитати -----------------------------
CREATE TABLE scan_gaps (
  gap_id     TEXT PRIMARY KEY,
  run_id     TEXT,
  tender_uid TEXT NOT NULL,
  error_code TEXT NOT NULL,
  detail     TEXT,
  noted_at   TEXT NOT NULL,
  resolved   INTEGER NOT NULL DEFAULT 0
);

-- ---------- журнал подій (тільки додавання) ---------------------------------
CREATE TABLE audit_log (
  audit_id   TEXT PRIMARY KEY,
  entity     TEXT NOT NULL,
  entity_id  TEXT NOT NULL,
  event      TEXT NOT NULL,
  payload    TEXT,
  created_at TEXT NOT NULL
);

-- У тестовому режимі доставка можлива ЛИШЕ на адресу з білого списку.
-- Перенесені історичні рядки під це правило не підпадають: вони нічого
-- не надсилають, а лише займають перший дотик.
CREATE TRIGGER trg_test_mode_fail_closed BEFORE INSERT ON outreach
WHEN NEW.mode = 'TEST'
 AND NEW.status NOT IN ('MIGRATED_UNVERIFIED', 'LEGACY_SENT')
 AND NEW.delivery_email_actual NOT IN (SELECT email FROM test_allowlist)
BEGIN SELECT RAISE(ABORT, 'TEST_RECIPIENT_NOT_ALLOWED'); END;

-- Компанія у стоп-листі не отримує нічого автоматично.
CREATE TRIGGER trg_block_suppressed BEFORE INSERT ON outreach
WHEN NEW.status = 'QUEUED'
 AND EXISTS (SELECT 1 FROM suppressions s
             WHERE s.state IN ('OPTED_OUT', 'DO_NOT_CONTACT')
               AND (s.company_id = NEW.company_id
                    OR (s.email IS NOT NULL AND s.email = NEW.contact_email_original)))
BEGIN SELECT RAISE(ABORT, 'COMPANY_SUPPRESSED'); END;

-- Те саме на UPDATE. Без цього будь-яка майбутня правка коду — або рядок,
-- перенесений із історії, якому змінили статус, — могла б поставити в поле
-- доставки адресу клієнта, і відправка пішла б саме за нею.
CREATE TRIGGER trg_test_mode_fail_closed_update BEFORE UPDATE ON outreach
WHEN NEW.mode = 'TEST'
 AND NEW.status NOT IN ('MIGRATED_UNVERIFIED', 'LEGACY_SENT')
 AND NEW.delivery_email_actual NOT IN (SELECT email FROM test_allowlist)
BEGIN SELECT RAISE(ABORT, 'TEST_RECIPIENT_NOT_ALLOWED'); END;

-- У бойовому режимі лист іде ЛИШЕ на адресу самої компанії (з Prozorro):
-- адреса доставки дорівнює адресі контакту, і вона схожа на e-mail.
CREATE TRIGGER trg_live_delivery_is_contact BEFORE INSERT ON outreach
WHEN NEW.mode = 'LIVE'
 AND NEW.status NOT IN ('MIGRATED_UNVERIFIED', 'LEGACY_SENT')
 AND (NEW.contact_email_original IS NULL
      OR lower(NEW.delivery_email_actual) <> lower(NEW.contact_email_original)
      OR NEW.delivery_email_actual NOT LIKE '_%@_%._%')
BEGIN SELECT RAISE(ABORT, 'LIVE_DELIVERY_NOT_CONTACT'); END;

CREATE TRIGGER trg_live_delivery_is_contact_update BEFORE UPDATE ON outreach
WHEN NEW.mode = 'LIVE'
 AND NEW.status NOT IN ('MIGRATED_UNVERIFIED', 'LEGACY_SENT')
 AND (NEW.contact_email_original IS NULL
      OR lower(NEW.delivery_email_actual) <> lower(NEW.contact_email_original)
      OR NEW.delivery_email_actual NOT LIKE '_%@_%._%')
BEGIN SELECT RAISE(ABORT, 'LIVE_DELIVERY_NOT_CONTACT'); END;

-- Адреса доставки визначається один раз, під час складання листа.
CREATE TRIGGER trg_delivery_address_immutable BEFORE UPDATE OF delivery_email_actual
ON outreach
WHEN NEW.delivery_email_actual IS NOT OLD.delivery_email_actual
BEGIN SELECT RAISE(ABORT, 'DELIVERY_ADDRESS_IMMUTABLE'); END;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE'); END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE'); END;
"""


# ============================================================================
#  БЛОК 4. БАЗА І РЕПОЗИТОРІЙ
# ============================================================================
class Db:
    def __init__(self, path: str):
        self.path = path
        try:
            need_init = path == ":memory:" or not os.path.exists(path)
            parent = os.path.dirname(os.path.abspath(path))
            if path != ":memory:" and parent:
                os.makedirs(parent, exist_ok=True)
            self.conn = sqlite3.connect(path, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA busy_timeout = 15000")
            if need_init:
                self.conn.executescript(SCHEMA_SQL)
                self.conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                    (str(SCHEMA_VERSION),))
            else:
                self._ensure_guards()
            row = self.one("SELECT value FROM schema_meta WHERE key = 'version'")
            found = int(row["value"]) if row else 0
            if found and found < SCHEMA_VERSION:
                found = self._migrate(found)
            if found != SCHEMA_VERSION:
                raise ERR["SCHEMA_MISMATCH"](found, SCHEMA_VERSION)
        except (sqlite3.Error, OSError) as exc:
            raise ERR["STORAGE_UNAVAILABLE"](str(exc)) from exc

    def _migrate(self, found: int) -> int:
        """
        Покрокове оновлення схеми без втрати даних.

        Лише додавання колонок: жодного перезапису рядків, жодного видалення.
        Кожен крок окремо і в порядку версій.
        """
        if found == 1:
            cols = {r["name"] for r in self.q("PRAGMA table_info(outreach)")}
            if "body_html" not in cols:
                self.x("ALTER TABLE outreach ADD COLUMN body_html TEXT")
            self.x("UPDATE schema_meta SET value = '2' WHERE key = 'version'")
            found = 2
        return found

    def _ensure_guards(self) -> None:
        """
        Захисні тригери в базі, створеній попередньою збіркою.

        Схема при цьому не змінюється: тригери лише забороняють те, що й так
        не робить жоден шлях коду. Додаються без перезапису даних.
        """
        have = {r["name"] for r in self.q(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for name in ("trg_test_mode_fail_closed_update",
                     "trg_delivery_address_immutable",
                     "trg_live_delivery_is_contact",
                     "trg_live_delivery_is_contact_update"):
            if name in have:
                continue
            block = SCHEMA_SQL.split(f"CREATE TRIGGER {name}")[1].split("END;")[0]
            self.conn.executescript(f"CREATE TRIGGER {name}{block}END;")

    def q(self, sql: str, args: tuple = ()) -> list:
        return list(self.conn.execute(sql, args).fetchall())

    def one(self, sql: str, args: tuple = ()):
        return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple = ()):
        return self.conn.execute(sql, args)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


class Repo:
    """Єдина точка запису стану. Бізнес-правил тут немає — лише збереження."""

    def __init__(self, db: Db):
        self.db = db
        self.rearmed = 0          # подій, повернутих із тестових прогонів

    # --- журнал -------------------------------------------------------------
    def audit(self, entity: str, entity_id: str, event: str, payload: dict | None = None):
        self.db.x("INSERT INTO audit_log(audit_id, entity, entity_id, event,"
                  " payload, created_at) VALUES (?,?,?,?,?,?)",
                  (new_id(), entity, entity_id, event,
                   json.dumps(payload or {}, ensure_ascii=False), now_iso()))

    # --- прогін -------------------------------------------------------------
    def start_run(self, run_id: str, window: Window) -> None:
        self.db.x("INSERT INTO runs(run_id, mode, started_at, window_start,"
                  " window_end, window_mode) VALUES (?,?,?,?,?,?)",
                  (run_id, MODE, now_iso(), window.start.isoformat(),
                   window.end.isoformat(), window.mode))

    def finish_run(self, run_id: str, scan_state: str, note: str) -> None:
        self.db.x("UPDATE runs SET finished_at = ?, scan_state = ?, scan_note = ?"
                  " WHERE run_id = ?", (now_iso(), scan_state, note, run_id))

    def previous_run_end(self) -> Optional[str]:
        row = self.db.one("SELECT finished_at FROM runs WHERE finished_at IS NOT NULL"
                          " ORDER BY finished_at DESC LIMIT 1")
        return row["finished_at"] if row else None

    # --- довідники ----------------------------------------------------------
    def company(self, edrpou: str, name: str) -> str:
        row = self.db.one("SELECT company_id, company_name FROM companies"
                          " WHERE edrpou_norm = ?", (edrpou,))
        if row:
            if name and not row["company_name"]:
                self.db.x("UPDATE companies SET company_name = ? WHERE company_id = ?",
                          (name, row["company_id"]))
            return row["company_id"]
        cid = new_id()
        self.db.x("INSERT INTO companies(company_id, edrpou_norm, company_name,"
                  " created_at) VALUES (?,?,?,?)", (cid, edrpou, name or edrpou, now_iso()))
        return cid

    def contact(self, company_id: str, email: Optional[str], name_raw: str,
                phone: str, vocative: str, confidence: str, source: str) -> str:
        row = self.db.one("SELECT contact_id FROM contacts WHERE company_id = ?"
                          "   AND COALESCE(contact_email, '') = ?",
                          (company_id, (email or "")))
        if row:
            self.db.x("UPDATE contacts SET contact_name_raw = ?, contact_phone = ?,"
                      " contact_vocative = ?, vocative_confidence = ?, name_source = ?"
                      " WHERE contact_id = ?",
                      (name_raw, phone, vocative, confidence, source, row["contact_id"]))
            return row["contact_id"]
        kid = new_id()
        self.db.x("INSERT INTO contacts(contact_id, company_id, contact_email,"
                  " contact_name_raw, contact_phone, contact_vocative,"
                  " vocative_confidence, name_source, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (kid, company_id, email, name_raw, phone, vocative,
                   confidence, source, now_iso()))
        return kid

    def tender(self, t: dict) -> str:
        row = self.db.one("SELECT tender_id FROM tenders WHERE ua_id = ?", (t["ua_id"],))
        if row:
            self.db.x("UPDATE tenders SET tender_status = ?, retrieved_at = ?"
                      " WHERE tender_id = ?",
                      (t.get("tender_status"), now_iso(), row["tender_id"]))
            return row["tender_id"]
        self.db.x("INSERT INTO tenders(tender_id, ua_id, title, buyer_name,"
                  " buyer_edrpou, cpv, method_type, tender_status, tender_value,"
                  " retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (t["tender_id"], t["ua_id"], t.get("title"), t.get("buyer_name"),
                   t.get("buyer_edrpou"), t.get("cpv"), t.get("method_type"),
                   t.get("tender_status"), t.get("tender_value"), now_iso()))
        return t["tender_id"]

    # --- події --------------------------------------------------------------
    def event(self, facts: "Rejection", tender_id: str, company_id: str,
              run_id: str) -> tuple[str, bool]:
        row = self.db.one(
            "SELECT e.event_id, e.run_id, r.mode AS run_mode FROM events e"
            "  LEFT JOIN runs r ON r.run_id = e.run_id"
            " WHERE e.tender_id = ? AND e.stage = ?"
            "   AND e.object_id = ? AND COALESCE(e.bid_id,'') = ?"
            "   AND COALESCE(e.lot_id,'') = ?",
            (tender_id, facts.stage, facts.object_id, facts.bid_id or "",
             facts.lot_id or ""))
        if row:
            self.db.x("UPDATE events SET object_status = ?, status_checked_at = ?"
                      " WHERE event_id = ?",
                      (facts.object_status, now_iso(), row["event_id"]))
            if MODE == "LIVE" and (row["run_mode"] or "TEST") != "LIVE" \
                    and row["run_id"] != run_id:
                # Подію раніше бачив лише тестовий прогін: тестові рішення
                # («вже писали», «у листі») для бойової розсилки не діють.
                # Подія знову нова — але лише тому, що вона в поточному вікні.
                self.db.x("UPDATE events SET run_id = ?, state = 'NEW',"
                          " state_reason = NULL WHERE event_id = ?",
                          (run_id, row["event_id"]))
                self.audit("event", row["event_id"], "EVENT_REARMED_FOR_LIVE",
                           {"was_run": row["run_id"], "run": run_id})
                self.rearmed += 1
                return row["event_id"], True
            return row["event_id"], False
        eid = new_id()
        self.db.x(
            "INSERT INTO events(event_id, run_id, tender_id, company_id, stage,"
            " object_id, bid_id, lot_id, event_time, event_time_source,"
            " event_time_basis, event_time_reliability, object_status,"
            " status_checked_at, reason_raw, reason_source, tender_value,"
            " lot_value, lot_resolved, bid_value, winner_value, winner_name,"
            " complaint_start, complaint_end, docs_decision, docs_bid, docs_td,"
            " joint_bid, identity_note, discovered_at, state, state_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, run_id, tender_id, company_id, facts.stage, facts.object_id,
             facts.bid_id, facts.lot_id, facts.event_time.isoformat(),
             facts.time_source, facts.time_basis, facts.time_reliability,
             facts.object_status, now_iso(), facts.reason_raw, facts.reason_source,
             facts.tender_value, facts.lot_value, facts.lot_resolved,
             facts.bid_value, facts.winner_value, facts.winner_name,
             facts.complaint_start, facts.complaint_end,
             json.dumps(facts.docs_decision, ensure_ascii=False),
             json.dumps(facts.docs_bid, ensure_ascii=False),
             json.dumps(facts.docs_td, ensure_ascii=False),
             1 if facts.joint_bid else 0, facts.identity_note, now_iso(),
             "NEW", None))
        self.audit("event", eid, "EVENT_DISCOVERED",
                   {"ua_id": facts.ua_id, "edrpou": facts.edrpou,
                    "time": facts.event_time.isoformat(),
                    "reliability": facts.time_reliability})
        return eid, True

    def set_event_state(self, event_id: str, state: str, reason: str = "") -> None:
        self.db.x("UPDATE events SET state = ?, state_reason = ? WHERE event_id = ?",
                  (state, reason or None, event_id))

    # --- політика -----------------------------------------------------------
    def has_first_touch(self, company_id: str) -> Optional[sqlite3.Row]:
        """
        Чи вже писали цій компанії. Бойовий дотик блокує все, тестовий —
        лише тестові листи: інакше один тест назавжди позбавляв би компанію
        справжнього першого листа.
        """
        return self.db.one(
            "SELECT status, mode, generated_at FROM outreach"
            " WHERE company_id = ? AND outreach_type = 'FIRST_TOUCH'"
            "   AND (mode = 'LIVE' OR mode = ?)"
            "   AND status IN ('QUEUED','SENDING','SENT','SENT_CONFIRMED',"
            "                  'DELIVERY_UNKNOWN','MIGRATED_UNVERIFIED','LEGACY_SENT')"
            " LIMIT 1", (company_id, MODE))

    def suppression(self, company_id: str, email: Optional[str]):
        return self.db.one(
            "SELECT state, reason FROM suppressions"
            " WHERE state IN ('OPTED_OUT','DO_NOT_CONTACT')"
            "   AND (company_id = ? OR (email IS NOT NULL AND email = ?)) LIMIT 1",
            (company_id, email or ""))

    def suppress(self, *, state: str, reason: str, source: str,
                 company_id: Optional[str] = None, email: Optional[str] = None) -> None:
        self.db.x("INSERT INTO suppressions(suppression_id, company_id, email,"
                  " state, reason, source, created_at) VALUES (?,?,?,?,?,?,?)",
                  (new_id(), company_id, (email or "").lower() or None, state,
                   reason, source, now_iso()))

    # --- черга --------------------------------------------------------------
    def reserve(self, draft: "Draft") -> str:
        oid = new_id()
        try:
            self.db.x(
                "INSERT INTO outreach(outreach_id, run_id, batch_id, company_id,"
                " contact_id, outreach_type, mode, status, origin, template_version,"
                " subject_rendered, body_rendered, body_html, contact_email_original,"
                " delivery_email_actual, message_id_header, event_ids, generated_at)"
                " VALUES (?,?,?,?,?, 'FIRST_TOUCH', ?, 'QUEUED', 'LEAD_MAIL', ?,?,?,?,?,?,?,?,?)",
                (oid, draft.run_id, draft.batch_id, draft.company_id,
                 draft.contact_id, MODE, draft.template_version, draft.subject,
                 draft.body, draft.html or None, draft.contact_email,
                 draft.delivery_email, draft.message_id,
                 json.dumps(draft.event_ids), now_iso()))
        except sqlite3.IntegrityError as exc:
            text = str(exc)
            if "TEST_RECIPIENT_NOT_ALLOWED" in text:
                raise ERR["TEST_RECIPIENT_NOT_ALLOWED"](draft.delivery_email) from exc
            if "LIVE_DELIVERY_NOT_CONTACT" in text:
                raise ERR["LIVE_DELIVERY_NOT_CONTACT"](draft.delivery_email) from exc
            if "COMPANY_SUPPRESSED" in text:
                raise _err("COMPANY_SUPPRESSED", "Компанія у стоп-листі",
                           "Один лід", True, "Автоматичні звернення заборонені.") from exc
            if "ux_first_touch" in text:
                raise _err("FIRST_TOUCH_EXISTS", "Компанія вже отримувала перший лист",
                           "Один лід", True, "Очікувана поведінка політики.") from exc
            raise _err("QUEUE_INSERT_FAILED", f"База не прийняла запис: {text}",
                       "Один лід", True, "Покажіть цей текст розробнику.") from exc
        self.audit("outreach", oid, "OUTREACH_RESERVED",
                   {"company": draft.company_id, "events": draft.event_ids})
        return oid

    def set_outreach(self, outreach_id: str, status: str, **fields) -> None:
        cols = ["status = ?"]
        args: list = [status]
        for key in ("provider_message_id", "provider_thread_id", "provider_timestamp",
                    "sent_at", "error_code", "error_detail"):
            if key in fields:
                cols.append(f"{key} = ?")
                args.append(fields[key])
        args.append(outreach_id)
        self.db.x(f"UPDATE outreach SET {', '.join(cols)} WHERE outreach_id = ?",
                  tuple(args))
        self.audit("outreach", outreach_id, f"STATUS_{status}", fields)

    def queued(self, batch_id: str, limit: int) -> list:
        return self.db.q(
            "SELECT * FROM outreach WHERE status = 'QUEUED' AND mode = ?"
            "   AND batch_id = ? ORDER BY generated_at, outreach_id LIMIT ?",
            (MODE, batch_id, limit))

    def stale_queued(self, batch_id: str) -> list:
        """Листи попередніх партій, які так і не пішли. Вони не змішуються
        з сьогоднішньою розсилкою (правило «черга не переживає добу»)."""
        return self.db.q(
            "SELECT * FROM outreach WHERE status = 'QUEUED' AND mode = ?"
            "   AND COALESCE(batch_id, '') <> ?", (MODE, batch_id))

    def unresolved(self) -> list:
        return self.db.q("SELECT * FROM outreach WHERE status IN"
                         " ('SENDING', 'DELIVERY_UNKNOWN') AND mode = ?", (MODE,))

    # --- білий список -------------------------------------------------------
    def sync_allowlist(self, addresses: list) -> int:
        clean = [a.strip().lower() for a in addresses if a and a.strip()]
        if not clean:
            raise ERR["TEST_ALLOWLIST_EMPTY"]()
        self.db.x("DELETE FROM test_allowlist WHERE email NOT IN (%s)"
                  % ",".join("?" * len(clean)), tuple(clean))
        for addr in clean:
            self.db.x("INSERT OR IGNORE INTO test_allowlist(email, added_at)"
                      " VALUES (?,?)", (addr, now_iso()))
        return len(clean)

    # --- перенесення історії ------------------------------------------------
    def import_legacy(self, legacy_db_path: str) -> dict:
        """
        Переносить із бази попереднього движка тих, кому вже писали, і стоп-лист.

        Рядки, які движок сам надіслав, лишаються доказом відправки.
        Рядки, перенесені колись із CSV моноліту, доказу не мають: історія
        там писалася ДО відправки. Тому вони отримують окремий стан
        MIGRATED_UNVERIFIED — перший дотик займають, але «надісланими»
        не вважаються.
        """
        stats = {"компаній": 0, "звернень": 0, "стопів": 0, "пропущено": 0}
        if not legacy_db_path or not os.path.exists(legacy_db_path):
            stats["пропущено"] = -1        # бази немає — нічого переносити
            return stats
        src = sqlite3.connect(f"file:{legacy_db_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        try:
            rows = src.execute(
                "SELECT c.edrpou_norm, c.company_name, o.mode, o.status,"
                "       o.origin, o.generated_at, o.delivery_email_actual,"
                "       o.contact_email_original"
                "  FROM outreach_events o"
                "  JOIN companies c ON c.company_id = o.company_id"
                " WHERE o.outreach_type = 'FIRST_TOUCH'"
                "   AND o.status IN ('QUEUED','SENDING','SENT','DELIVERY_UNKNOWN',"
                "                    'BOUNCED','REPLIED','OPTED_OUT')").fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            edrpou = E.normalize_edrpou(row["edrpou_norm"])
            if not edrpou:
                stats["пропущено"] += 1
                continue
            cid = self.company(edrpou, row["company_name"] or edrpou)
            stats["компаній"] += 1
            status = ("MIGRATED_UNVERIFIED" if (row["origin"] or "") == "MIGRATED"
                      else "LEGACY_SENT")
            try:
                self.db.x(
                    "INSERT INTO outreach(outreach_id, run_id, batch_id, company_id,"
                    " contact_id, outreach_type, mode, status, origin,"
                    " template_version, subject_rendered, body_rendered,"
                    " contact_email_original, delivery_email_actual,"
                    " message_id_header, event_ids, generated_at)"
                    " VALUES (?,NULL,NULL,?,NULL,'FIRST_TOUCH',?,?, 'IMPORTED',"
                    "         'LEGACY', '[перенесено з попереднього движка]',"
                    "         '[текст у старій базі]', ?, ?, ?, '[]', ?)",
                    (new_id(), cid, row["mode"] or "LIVE", status,
                     row["contact_email_original"],
                     row["delivery_email_actual"] or "legacy@imported.local",
                     f"<legacy-{new_id()}@{E.GMAIL_DOMAIN}>",
                     row["generated_at"] or now_iso()))
                stats["звернень"] += 1
            except sqlite3.IntegrityError:
                pass                      # перший дотик уже є — саме те, що треба
        try:
            stops = src.execute(
                "SELECT s.state, s.suppression_reason, s.email, c.edrpou_norm"
                "  FROM suppressions s"
                "  LEFT JOIN companies c ON c.company_id = s.company_id").fetchall()
        except sqlite3.Error:
            stops = []
        for row in stops:
            edrpou = E.normalize_edrpou(row["edrpou_norm"]) if row["edrpou_norm"] else None
            cid = self.company(edrpou, edrpou) if edrpou else None
            state = row["state"] if row["state"] in (
                "OPTED_OUT", "DO_NOT_CONTACT", "INVALID_EMAIL", "BOUNCED") else "DO_NOT_CONTACT"
            self.suppress(state=state, reason=row["suppression_reason"] or "перенесено",
                          source="legacy_db", company_id=cid, email=row["email"])
            stats["стопів"] += 1
        src.close()
        self.audit("system", "legacy", "LEGACY_IMPORTED", stats)
        return stats


# ============================================================================
#  БЛОК 5. PROZORRO: СТРІЧКА, ЗАКУПІВЛІ, ПОДІЇ ВІДХИЛЕННЯ
# ----------------------------------------------------------------------------
#  Документи НЕ завантажуються. Зі структури закупівлі беруться лише назви
#  й посилання — їх відкриває людина з картки.
# ============================================================================
#: Конверти документів пропозиції. Звірено з документацією API 2.5.
BID_ENVELOPES = ("documents", "financialDocuments", "eligibilityDocuments",
                 "qualificationDocuments")


@dataclass
class Person:
    name_raw: str = ""
    email: Optional[str] = None
    phone: str = ""
    address: str = ""


@dataclass
class EventTime:
    time: Optional[datetime]
    source: str
    basis: str
    reliability: str            # HIGH / MEDIUM / CONFLICT / UNDETERMINED


@dataclass
class Rejection:
    """Факти однієї події відхилення. Нічого, що потребує завантаження."""
    ua_id: str
    tender_id: str
    tender_title: str
    buyer_name: str
    buyer_edrpou: str
    cpv: str
    method_type: str
    tender_status: str
    stage: str
    object_id: str
    bid_id: Optional[str]
    lot_id: Optional[str]
    event_time: datetime
    time_source: str
    time_basis: str
    time_reliability: str
    object_status: str
    edrpou: str
    company_name: str
    person: Person
    reason_raw: str = ""
    reason_source: str = "NOT_ESTABLISHED"
    tender_value: Optional[float] = None
    lot_value: Optional[float] = None
    lot_resolved: str = "NO_LOTS"
    bid_value: Optional[float] = None
    winner_value: Optional[float] = None
    winner_name: str = ""
    complaint_start: Optional[str] = None
    complaint_end: Optional[str] = None
    docs_decision: list = field(default_factory=list)
    docs_bid: list = field(default_factory=list)
    docs_td: list = field(default_factory=list)
    joint_bid: bool = False
    identity_note: str = ""


def event_time(obj: dict, stage: str, moment: Optional[datetime] = None) -> EventTime:
    """
    Коли сталося відхилення, з якого поля це взято і наскільки можна вірити.

    award:          основне поле `date` — в API воно переписується при кожній
                    зміні статусу, тож поки статус `unsuccessful`, це і є
                    момент рішення. Підтвердження — `complaintPeriod.startDate`,
                    який ставиться в той самий момент.
    qualification:  лише `date`. `complaintPeriod` кваліфікації зʼявляється
                    разом зі stand-still усієї закупівлі й моментом рішення
                    не є НІКОЛИ.

    Дати документів, `dateModified` закупівлі та кінець періоду оскарження
    часом події бути не можуть — саме на цьому ламався попередній відбір.
    """
    moment = moment or now()
    t_date = parse_dt(obj.get("date"))
    t_cp = parse_dt((obj.get("complaintPeriod") or {}).get("startDate"))

    def sane(candidate: Optional[datetime], src: str, basis: str,
             reliability: str) -> EventTime:
        if candidate is None:
            return EventTime(None, "—", "жодного придатного поля часу",
                             "UNDETERMINED")
        if candidate > moment + timedelta(minutes=10):
            return EventTime(candidate, src, f"{basis}; час у майбутньому",
                             "UNDETERMINED")
        return EventTime(candidate, src, basis, reliability)

    if stage == "qualifications":
        return sane(t_date, "qualification.date",
                    "єдине поле часу рішення; complaintPeriod кваліфікації — "
                    "це початок stand-still, не момент відхилення", "MEDIUM")

    if t_date and t_cp:
        delta = abs((t_date.astimezone(timezone.utc)
                     - t_cp.astimezone(timezone.utc)).total_seconds())
        if delta <= EVENT_TIME_TOLERANCE_SECONDS:
            return sane(t_date, "award.date + complaintPeriod.startDate",
                        f"обидва поля збігаються (різниця {int(delta)} с)", "HIGH")
        return sane(t_date, "award.date",
                    f"award.date і complaintPeriod.startDate розходяться на "
                    f"{int(delta)} с", "CONFLICT")
    if t_date:
        return sane(t_date, "award.date",
                    "немає complaintPeriod.startDate для звірки", "MEDIUM")
    if t_cp:
        return sane(t_cp, "complaintPeriod.startDate",
                    "немає award.date", "MEDIUM")
    return EventTime(None, "—", "жодного придатного поля часу", "UNDETERMINED")


def doc_links(docs: Any, limit: int = 40) -> list:
    """
    Назви й посилання документів — лише метадані, тут нічого не качається.

    limit=0 — без обмеження: так зберігаються документи рішення, бо службова
    картка має бачити всі (правило 17). Ідентифікатор, контрольна сума і
    повні дати з часовим поясом потрібні їй, щоб розрізняти редакції.
    """
    out = []
    for d in ((docs or [])[:limit] if limit else (docs or [])):
        if not isinstance(d, dict):
            continue
        out.append({
            "назва": L.clean_spaces(d.get("title") or ""),
            "посилання": d.get("url") or "",
            "опубліковано": str(d.get("datePublished") or "")[:19],
            "змінено": str(d.get("dateModified") or "")[:19],
            "формат": d.get("format") or "",
            "id": str(d.get("id") or ""),
            "тип": str(d.get("documentType") or ""),
            "hash": str(d.get("hash") or ""),
            "published_iso": str(d.get("datePublished") or ""),
            "modified_iso": str(d.get("dateModified") or ""),
        })
    return out


def bid_of(tender: dict, bid_id: Optional[str]) -> dict:
    for b in (tender.get("bids") or []):
        if bid_id and b.get("id") == bid_id:
            return b
    return {}


def bid_documents(bid: dict) -> list:
    """Усі чотири конверти пропозиції — тільки назви й посилання."""
    out = []
    for envelope in BID_ENVELOPES:
        out += doc_links(bid.get(envelope))
    return out


def resolve_identity(tender: dict, obj: dict, stage: str) -> tuple:
    """
    (ЄДРПОУ/ІПН, назва, примітка, спільна пропозиція).

    Код беремо з пропозиції цього учасника і звіряємо з постачальником у
    рішенні. Розбіжність — не привід вгадати: такий лід іде на перегляд.
    """
    bid = bid_of(tender, obj.get("bid_id") or obj.get("bidID"))
    tenderers = list(bid.get("tenderers") or [])
    suppliers = list(obj.get("suppliers") or [])
    note_parts = []

    def ident(source: dict) -> tuple:
        ident_obj = (source or {}).get("identifier") or {}
        return (E.normalize_edrpou(ident_obj.get("id")),
                str(ident_obj.get("scheme") or ""),
                str(ident_obj.get("legalName") or source.get("name") or ""))

    code_t, scheme_t, name_t = ident(tenderers[0]) if tenderers else (None, "", "")
    code_s, scheme_s, name_s = ident(suppliers[0]) if suppliers else (None, "", "")

    if code_t and code_s and code_t != code_s:
        return None, name_t or name_s, "IDENTITY_CONFLICT: код у пропозиції " \
            f"({code_t}) не збігається з кодом у рішенні ({code_s})", len(tenderers) > 1
    code = code_t or code_s
    name = name_t or name_s
    scheme = scheme_t or scheme_s
    # Порожня примітка читалася б у картці як «НЕ ВСТАНОВЛЕНО», хоча звірка
    # відбулася. Тому кожен випадок називаємо словами.
    if code_t and code_s:
        note_parts.append("код у пропозиції і в рішенні збігається")
    elif code_t:
        note_parts.append("код узято з пропозиції; у рішенні коду немає")
    elif code_s:
        note_parts.append("код узято з рішення; у пропозиції коду немає")
    else:
        note_parts.append("коду немає ні в пропозиції, ні в рішенні")
    if scheme and scheme.upper() not in ("UA-EDR", "UA_EDR"):
        note_parts.append(f"схема ідентифікатора «{scheme}»")
    if len(tenderers) > 1:
        note_parts.append(f"спільна пропозиція, учасників {len(tenderers)}")
    return code, name, "; ".join(note_parts), len(tenderers) > 1


def lot_facts(tender: dict, obj: dict, bid: dict) -> tuple:
    """(вартість лота, стан визначення лота, id лота)."""
    lots = list(tender.get("lots") or [])
    lot_id = obj.get("lotID") or obj.get("lotId") or ""
    if not lot_id and bid:
        for lv in (bid.get("lotValues") or []):
            if lv.get("relatedLot"):
                lot_id = lv["relatedLot"]
                break
    lot_value = None
    if lot_id:
        for lot in lots:
            if lot.get("id") == lot_id:
                try:
                    lot_value = float((lot.get("value") or {}).get("amount") or 0) or None
                except (TypeError, ValueError):
                    lot_value = None
                break
    if not lots:
        return lot_value, "NO_LOTS", lot_id or None
    return lot_value, ("YES" if (lot_id and lot_value is not None) else "NO"), lot_id or None


def _amount(container: Any) -> Optional[float]:
    try:
        value = float(((container or {}).get("value") or {}).get("amount") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    return value or None


def rejection_facts(tender: dict, stage: str, obj: dict,
                    moment: Optional[datetime] = None) -> tuple:
    """(Rejection або None, причина пропуску)."""
    ua_id = tender.get("tenderID") or ""
    when = event_time(obj, stage, moment)
    if when.time is None:
        return None, f"час події не встановлено ({when.basis})"

    edrpou, company_name, note, joint = resolve_identity(tender, obj, stage)
    if not edrpou:
        return None, note or "не встановлено ЄДРПОУ/ІПН учасника"

    try:
        contacts = L.participant_contacts(tender, obj) or {}
    except Exception as exc:                                    # noqa: BLE001
        return None, f"контакти не розібрано: {type(exc).__name__}"

    bid = bid_of(tender, obj.get("bid_id") or obj.get("bidID"))
    lot_value, lot_resolved, lot_id = lot_facts(tender, obj, bid)

    bid_value = _amount(obj)
    if bid_value is None and bid:
        for lv in (bid.get("lotValues") or []):
            if not lot_id or lv.get("relatedLot") == lot_id:
                bid_value = _amount(lv)
                break
        if bid_value is None:
            bid_value = _amount(bid)

    winner_value, winner_name = None, ""
    for other in (tender.get("awards") or []):
        if other.get("status") != "active":
            continue
        if lot_id and other.get("lotID") and other.get("lotID") != lot_id:
            continue
        winner_value = _amount(other)
        for sup in (other.get("suppliers") or []):
            winner_name = str(sup.get("name") or "")
            break
        break

    reason = "\n".join(x for x in (L.clean_spaces(obj.get("title") or ""),
                                   L.clean_spaces(obj.get("description") or "")) if x)
    period = obj.get("complaintPeriod") or {}
    buyer = tender.get("procuringEntity") or {}

    facts = Rejection(
        ua_id=ua_id, tender_id=str(tender.get("id") or ua_id),
        tender_title=L.soft_clean(tender.get("title") or ""),
        buyer_name=str(buyer.get("name") or ""),
        buyer_edrpou=str((buyer.get("identifier") or {}).get("id") or ""),
        cpv=(L.first_cpv(tender) or ""),
        method_type=str(tender.get("procurementMethodType") or ""),
        tender_status=str(tender.get("status") or ""),
        stage=stage, object_id=str(obj.get("id") or ""),
        bid_id=obj.get("bid_id") or obj.get("bidID"), lot_id=lot_id,
        event_time=when.time, time_source=when.source, time_basis=when.basis,
        time_reliability=when.reliability,
        object_status=str(obj.get("status") or ""),
        edrpou=edrpou, company_name=L.soft_clean(company_name or contacts.get("компанія") or ""),
        person=Person(name_raw=str(contacts.get("контактна_особа") or ""),
                      email=(str(contacts.get("email") or "").split(",")[0].strip().lower()
                             or None),
                      phone=str(contacts.get("телефон") or ""),
                      address=str(contacts.get("адреса") or "")),
        reason_raw=reason, reason_source=("AWARD_TITLE_DESCRIPTION" if reason
                                          else "NOT_ESTABLISHED"),
        tender_value=_amount(tender), lot_value=lot_value, lot_resolved=lot_resolved,
        bid_value=bid_value, winner_value=winner_value, winner_name=winner_name,
        complaint_start=str(period.get("startDate") or "") or None,
        complaint_end=str(period.get("endDate") or "") or None,
        docs_decision=doc_links(obj.get("documents"), limit=0),
        docs_bid=bid_documents(bid),
        docs_td=doc_links(tender.get("documents")),
        joint_bid=joint, identity_note=note)
    return facts, ""


@dataclass
class ScanResult:
    events: list = field(default_factory=list)          # Rejection у вікні
    review: list = field(default_factory=list)          # (Rejection-подібне, причина)
    counters: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)            # (uuid, код, деталі)
    skipped_types: dict = field(default_factory=dict)   # тип процедури -> скільки
    state: str = "COMPLETE"
    note: str = ""


def widen_pool(session, size: int = 0) -> None:
    """
    Пул зʼєднань requests під кількість потоків. Чужу (передану в тестах)
    сесію не чіпаємо: функцію викликають лише для власних сесій.
    """
    try:
        from requests.adapters import HTTPAdapter               # noqa: PLC0415
    except ImportError:                                         # pragma: no cover
        return
    if not hasattr(session, "mount"):
        return
    size = size or HTTP_POOL_SIZE
    adapter = HTTPAdapter(pool_connections=size, pool_maxsize=size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


class Prozorro:
    """Клієнт стрічки змін. Помилка обходу — це стан, а не кінець стрічки."""

    def __init__(self, session=None, api: str = API, verbose: bool = True):
        if session is None:
            session = L.make_session()
            widen_pool(session)
        self.session = session
        self.api = api
        self.verbose = verbose

    # --- один запит ---------------------------------------------------------
    def _get(self, url: str, params: Optional[dict] = None) -> tuple:
        """(дані, код помилки). Мовчазного None тут немає."""
        last = "UNKNOWN"
        for attempt in range(HTTP_TRIES):
            try:
                response = self.session.get(url, params=params, timeout=60)
            except Exception as exc:                            # noqa: BLE001
                last = f"NETWORK:{type(exc).__name__}"
                time.sleep(2 * (attempt + 1))
                continue
            code = response.status_code
            if code == 200:
                try:
                    return response.json(), ""
                except ValueError:
                    return None, "INVALID_JSON"
            if code in (429, 500, 502, 503, 504):
                last = f"HTTP_{code}"
                time.sleep(2 * (attempt + 1))
                continue
            return None, f"HTTP_{code}"
        return None, last

    # --- стрічка ------------------------------------------------------------
    def feed(self, window: Window) -> tuple:
        """(кандидати, стан, пояснення, сторінок, подій, пропущені uid->тип)."""
        offset = str(window.start.timestamp())
        candidates: list = []
        skipped_ids: dict = {}          # uid -> тип процедури (без повторів)
        pages = 0
        seen_events = 0
        state, note = "COMPLETE", ""
        while pages < MAX_FEED_PAGES:
            data, err = self._get(self.api, {
                "limit": FEED_LIMIT, "offset": offset,
                "opt_fields": "status,procurementMethodType"})
            if err:
                if pages == 0:
                    raise ERR["PROZORRO_UNAVAILABLE"](err)
                state = "INCOMPLETE"
                note = (f"стрічку прочитано не до кінця: сторінка {pages + 1} "
                        f"повернула {err}")
                break
            rows = data.get("data") or []
            seen_events += len(rows)
            for row in rows:
                if str(row.get("status") or "") in SKIP_TENDER_STATUSES:
                    continue
                метод = str(row.get("procurementMethodType") or "")
                if метод not in APPEALABLE_METHOD_TYPES:
                    # Закупівлю навіть не питаємо: це рішення власника, а не
                    # оптимізація. Рахуємо за id, бо одна закупівля може
                    # зʼявитися у стрічці кілька разів за добу.
                    skipped_ids[row["id"]] = метод or "(тип не вказано)"
                    continue
                candidates.append(row["id"])
            pages += 1
            nxt = (data.get("next_page") or {}).get("offset")
            if self.verbose and pages % 25 == 0:
                print(f"    сторінка {pages}: подій {seen_events:,}, "
                      f"кандидатів {len(candidates):,}")
            if not rows or not nxt:
                break
            offset = nxt
        else:
            state = "INCOMPLETE"
            note = (f"досягнуто межі в {MAX_FEED_PAGES} сторінок стрічки "
                    f"(MAX_FEED_PAGES); частина змін за сьогодні не прочитана, "
                    f"і ці відхилення в розсилку не потраплять. Якщо це "
                    f"повторюється — збільште MAX_FEED_PAGES і зробіть M.go() "
                    f"ще раз")
        return (list(dict.fromkeys(candidates)), state, note, pages, seen_events,
                skipped_ids)

    # --- одна закупівля -----------------------------------------------------
    def tender(self, uid: str) -> tuple:
        data, err = self._get(f"{self.api}/{uid}")
        if err:
            return None, err
        return (data or {}).get("data"), ""

    # --- повний обхід -------------------------------------------------------
    def scan(self, window: Window, retry_uids: Optional[list] = None) -> ScanResult:
        result = ScanResult()
        counters = {"подій_усього": 0, "у_вікні": 0, "старіші": 0,
                    "пропущено_за_процедурою": 0,
                    "хвіст_попередньої_доби": 0, "ненадійний_час": 0,
                    "без_ЄДРПОУ": 0, "закупівель_перевірено": 0,
                    "закупівель_з_відхиленнями": 0}
        if self.verbose:
            print(f"\nКРОК 1/3. Читаю стрічку змін Prozorro "
                  f"({window.human()})…")
        candidates, state, note, pages, seen, skipped_ids = self.feed(window)
        for uid in (retry_uids or []):
            if uid not in candidates:
                candidates.append(uid)
        result.state, result.note = state, note
        if self.verbose:
            print(f"    подій у стрічці: {seen:,} • закупівель до перевірки: "
                  f"{len(candidates):,}" + (f" (+{len(retry_uids or [])} з минулого "
                                            f"прогону)" if retry_uids else ""))
            if skipped_ids:
                print(f"    пропущено за типом процедури: {len(skipped_ids):,}")
            if state == "INCOMPLETE":
                print(f"    ! {note}")

        if self.verbose:
            print(f"\nКРОК 2/3. Перевіряю закупівлі ({SCAN_WORKERS} потоків)…")
        moment = now()

        def take(uid: str):
            tender, err = self.tender(uid)
            return uid, tender, err

        done = 0
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
            for uid, tender, err in pool.map(take, candidates):
                done += 1
                counters["закупівель_перевірено"] += 1
                if err or not tender:
                    result.gaps.append((uid, err or "EMPTY", ""))
                    continue
                метод = str(tender.get("procurementMethodType") or "")
                if метод not in APPEALABLE_METHOD_TYPES:
                    # Друга перевірка: закупівля могла прийти повтором
                    # прогалини, а не зі стрічки. Рахуємо за id, тому та сама
                    # закупівля двічі в підсумок не потрапляє.
                    skipped_ids[uid] = метод or "(тип не вказано)"
                    continue
                had_event = False
                for stage in ("awards", "qualifications"):
                    for obj in (tender.get(stage) or []):
                        if obj.get("status") != "unsuccessful":
                            continue
                        counters["подій_усього"] += 1
                        when = event_time(obj, stage, moment)
                        if when.reliability in ("UNDETERMINED", "CONFLICT"):
                            counters["ненадійний_час"] += 1
                            facts, why = rejection_facts(tender, stage, obj, moment)
                            result.review.append(
                                (facts, f"час події: {when.reliability} — {when.basis}")
                                if facts else (None, why))
                            continue
                        if not window.contains(when.time):
                            if window.in_tail(when.time):
                                counters["хвіст_попередньої_доби"] += 1
                            else:
                                counters["старіші"] += 1
                            continue
                        if (when.reliability == "MEDIUM"
                                and not ALLOW_MEDIUM_RELIABILITY):
                            counters["ненадійний_час"] += 1
                            facts, _ = rejection_facts(tender, stage, obj, moment)
                            if facts:
                                result.review.append(
                                    (facts, "надійність часу MEDIUM, а їх "
                                            "увімкнення вимкнено налаштуванням"))
                            continue
                        facts, why = rejection_facts(tender, stage, obj, moment)
                        if facts is None:
                            counters["без_ЄДРПОУ"] += 1
                            result.review.append((None, f"{tender.get('tenderID')}: {why}"))
                            continue
                        counters["у_вікні"] += 1
                        result.events.append(facts)
                        had_event = True
                if had_event:
                    counters["закупівель_з_відхиленнями"] += 1
                if self.verbose and done % 500 == 0:
                    print(f"    перевірено {done}/{len(candidates)} • "
                          f"подій у вікні: {counters['у_вікні']}")
        if result.gaps:
            result.state = "INCOMPLETE"
            extra = f"не завантажилось закупівель: {len(result.gaps)}"
            result.note = f"{result.note}; {extra}" if result.note else extra
        # Пропущені за процедурою рахуємо за id: та сама закупівля могла
        # зустрітися і у стрічці, і в повторі прогалини.
        counters["пропущено_за_процедурою"] = len(skipped_ids)
        for метод in skipped_ids.values():
            result.skipped_types[метод] = result.skipped_types.get(метод, 0) + 1
        result.counters = counters
        if self.verbose:
            print(f"    перевірено {done} • подій у вікні: {counters['у_вікні']}"
                  f" • на перевірку: {len(result.review)}")
        return result


# ============================================================================
#  БЛОК 6. ЗВЕРТАННЯ
# ----------------------------------------------------------------------------
#  Шлюз «чи це взагалі людське ім'я» береться з движка: він уже спинив
#  «Добрий день, Те!» з поля «Tender Department». Сумнів завжди вирішується
#  на користь нейтрального звертання.
# ============================================================================
SAFE_GREETING = "Добрий день!"

#: Звертання за канонічною специфікацією 03.09 §8 — лише ім'я:
#: «Добрий день, Іване!». Повернути по батькові — одне значення.
GREETING_WITH_PATRONYMIC = False


def greeting_for(raw_name: str, company_name: str = "") -> tuple:
    """
    (звертання, довіра, джерело).

    Ім'я вживається лише тоді, коли воно СПРАВДІ встановлене. Порожнє поле,
    назва підприємства, посада, латиниця чи саме прізвище дають нейтральне
    «Добрий день!»: хибна персоналізація гірша за нейтральну.
    """
    raw = (raw_name or "").strip()
    if not raw:
        return SAFE_GREETING, "LOW", "NO_NAME"
    block = E.person_name_block(raw, company_name)
    if block:
        return SAFE_GREETING, "LOW", block
    try:
        pib = L.parse_pib(raw, company_name)
    except Exception as exc:                                    # noqa: BLE001
        return SAFE_GREETING, "LOW", f"VOCATIVE_ERROR:{type(exc).__name__}"
    name, patr = pib.get("імя"), pib.get("по_батькові")
    if not name:
        return SAFE_GREETING, "LOW", "SURNAME_ONLY"
    fem = pib.get("стать") == "ж"
    try:
        voc = L.voc_first_female(name) if fem else L.voc_first_male(name)
        if patr and GREETING_WITH_PATRONYMIC:
            voc += " " + L.voc_patronymic(patr, fem)
    except Exception as exc:                                    # noqa: BLE001
        return SAFE_GREETING, "LOW", f"VOCATIVE_ERROR:{type(exc).__name__}"
    return f"Добрий день, {voc}!", ("HIGH" if patr else "MEDIUM"), (
        "PIB" if patr else "NAME")


# ============================================================================
#  БЛОК 7. ШАБЛОНИ ЛИСТА
# ----------------------------------------------------------------------------
#  Версії незмінні. Правка тексту — це нова версія, а не редагування старої:
#  інакше неможливо сказати, що саме отримала людина три тижні тому.
# ============================================================================
@dataclass(frozen=True)
class Template:
    version: str
    subject: str
    body: str
    html: str = ""          # порожньо = лист іде лише текстом


TEMPLATE_V4_OWNER = Template(
    version="V4_OWNER",
    subject="Чи обґрунтовано відхилили вашу пропозицію {ua_id}?",
    body=(
        "{greeting}\n"
        "\n"
        "Звернув увагу, що {rejection_phrase} було відхилено.\n"
        "\n"
        "Чи обґрунтоване рішення замовника та чи варто витрачати кошти на його "
        "оскарження?\n"
        "\n"
        "TenderWin пропонує аналіз {analysis_object} за 24 години. Ви отримаєте "
        "письмовий висновок: чи є підстави для оскарження, які існують ризики та "
        "що доцільно робити далі.\n"
        "\n"
        "Якщо оскарження недоцільне — прямо про це повідомимо.\n"
        "\n"
        "Вартість — 3 499 грн. Термін — після оплати й отримання необхідних "
        "документів.\n"
        "\n"
        "Якщо питання актуальне, просто відповідайте на цей лист.\n"
        "\n"
        "З повагою,\n"
        "Щасливий Віталій\n"
        "Радник з публічних закупівель TenderWin\n"
        "050 310 14 92\n"
    ),
)

TEMPLATE_V5_REDTEAM = Template(
    version="V5_REDTEAM",
    subject="Відхилення у закупівлі {ua_id}: чи є підстави для оскарження?",
    body=(
        "{greeting}\n"
        "\n"
        "Побачив у Prozorro, що {rejection_phrase} "
        "відхилено{date_suffix}.\n"
        "\n"
        "Питання після такого рішення зазвичай одне: чи справді підстави були і "
        "чи варто витрачати гроші на оскарження.\n"
        "\n"
        "Дам на нього письмову відповідь: чи були підстави для відхилення, які "
        "ризики та що доцільно робити далі — зіставивши те, що написав замовник у "
        "рішенні, з вимогами тендерної документації і вашими документами. Якщо "
        "підстав для скарги немає, напишу про це прямо.\n"
        "\n"
        "Документи закупівлі беру з Prozorro сам, збирати нічого не потрібно. "
        "Якщо чогось бракуватиме — скажу одразу.\n"
        "\n"
        "Вартість — 3 499 грн за одне рішення про відхилення, з усіма підставами, "
        "які в ньому названі. Висновок — протягом 24 годин після оплати.\n"
        "{deadline_block}"
        "\n"
        "Якщо питання актуальне — просто відповідайте на цей лист.\n"
        "\n"
        "З повагою,\n"
        "Віталій Щасливий\n"
        "радник з публічних закупівель, TenderWin\n"
        "+380 50 310 14 92 · tenderwin.in.ua\n"
        "{opt_out_block}"
    ),
)


# ----------------------------------------------------------------------------
#  V6_OWNER — ЧИННА ВЕРСІЯ. Текст затверджено власником 22.09.2026.
#  HTML-частина містить ті самі слова: інше лише розбиття на рядки й акценти.
#  Свідомо немає: зображень, кнопок, банерів, пікселя відстеження, зовнішніх
#  шрифтів і стилів. Лист має лишатися читабельним навіть як голий текст.
# ----------------------------------------------------------------------------
TEMPLATE_V6_OWNER = Template(
    version="V6_OWNER",
    subject="Відхилення у закупівлі {ua_id}: чи є підстави для оскарження?",
    body=(
        "{greeting}\n"
        "\n"
        "Побачив у Prozorro, що {rejection_phrase} "
        "відхилено{date_suffix}.\n"
        "\n"
        "Питання після такого рішення зазвичай одне: чи справді підстави були "
        "і чи варто витрачати гроші на оскарження.\n"
        "\n"
        "Дам на нього дуже детальну письмову відповідь: чи були підстави для "
        "відхилення, які ризики та що доцільно робити далі — зіставивши те, що "
        "написав замовник у рішенні, з вимогами тендерної документації, вашими "
        "документами та практикою АМКУ. Якщо підстав для скарги немає, напишу "
        "про це прямо.\n"
        "\n"
        "Цього Вам буде достатньо, щоб самостійно подати скаргу в АМКУ (якщо це "
        "доцільно), не витрачаючи зайвих коштів на юридичний супровід.\n"
        "\n"
        "Вартість — 3 499 грн за одне рішення про відхилення, з усіма підставами, "
        "які в ньому названі. Висновок — протягом 24 годин після оплати.\n"
        "{deadline_block}"
        "\n"
        "Якщо питання актуальне — просто відповідайте на цей лист.\n"
        "\n"
        "З повагою,\n"
        "Віталій Щасливий\n"
        "радник з публічних закупівель TenderWin\n"
        "{phone_text} · {site_label}\n"
        "{opt_out_block}"
    ),
    html=(
        # Увесь текст листа — тут, поряд із текстовою версією: версія листа
        # незмінна, і слова в обох частинах мають збігатися дослівно.
        '<!DOCTYPE html>\n'
        '<html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        # Поштові клієнти з темною темою інакше перефарбовують тло і текст
        # по-різному — лист лишився б темним по темному.
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        '</head>\n'
        '<body style="margin:0;padding:0;background:#ffffff;">\n'
        '<div style="max-width:620px;margin:0;padding:18px 20px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,"
        'Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#1f2328;'
        'background:#ffffff;">\n'

        '<p style="margin:0 0 16px 0;">{greeting}</p>\n'

        '<p style="margin:0 0 16px 0;">Побачив у Prozorro, що '
        '{rejection_html} відхилено{date_suffix}.</p>\n'

        '<p style="margin:0 0 16px 0;">Питання після такого рішення зазвичай '
        'одне: чи справді підстави були і чи варто витрачати гроші на '
        'оскарження.</p>\n'

        '<p style="margin:0 0 8px 0;">Дам на нього дуже детальну письмову '
        'відповідь:</p>\n'
        '<ul style="margin:0 0 12px 0;padding-left:22px;">'
        '<li style="margin:0 0 6px 0;">чи були підстави для відхилення,</li>'
        '<li style="margin:0 0 6px 0;">які ризики,</li>'
        '<li style="margin:0;">що доцільно робити далі</li></ul>\n'
        '<p style="margin:0 0 16px 0;">— зіставивши те, що написав замовник у '
        'рішенні, з вимогами тендерної документації, вашими документами та '
        'практикою АМКУ. Якщо підстав для скарги немає, напишу про це '
        'прямо.</p>\n'

        '<p style="margin:0 0 16px 0;">Цього Вам буде достатньо, щоб '
        'самостійно подати скаргу в АМКУ (якщо це доцільно), не витрачаючи '
        'зайвих коштів на юридичний супровід.</p>\n'

        '<div style="margin:0 0 16px 0;padding:12px 14px;background:#f6f8fa;'
        'border-left:3px solid #c9d1d9;">\n'
        '<p style="margin:0;"><strong>Вартість — 3 499 грн</strong> за одне '
        'рішення про відхилення, з усіма підставами, які в ньому названі.<br>'
        'Висновок — <strong>протягом 24 годин після оплати</strong>.</p>\n'
        '{deadline_html}'
        '</div>\n'

        '<p style="margin:0 0 18px 0;">Якщо питання актуальне — просто '
        'відповідайте на цей лист.</p>\n'

        '<p style="margin:0;font-size:15px;line-height:1.5;color:#3a4149;">'
        'З повагою,<br>'
        '<strong>Віталій Щасливий</strong><br>'
        'радник з публічних закупівель '
        '<a href="{site_url}" style="color:#0a58ca;">TenderWin</a><br>'
        '<a href="tel:{phone_tel}" style="color:#3a4149;'
        'text-decoration:none;">{phone_text}</a> · '
        '<a href="{site_url}" style="color:#0a58ca;">{site_label}</a>'
        '</p>\n'
        '{opt_out_html}'
        '</div></body></html>'
        ),
)

# ----------------------------------------------------------------------------
#  V7_OWNER — ЧИННА ВЕРСІЯ. Відрізняється від V6 одним реченням: обіцянка
#  достатності помʼякшена до «зазвичай цього достатньо» (рішення власника
#  23.09.2026). V6 лишається незмінною: вона вже названа в ADR-0019.
# ----------------------------------------------------------------------------
TEMPLATE_V7_OWNER = Template(
    version="V7_OWNER",
    subject="Відхилення у закупівлі {ua_id}: чи є підстави для оскарження?",
    body=(
        "{greeting}\n"
        "\n"
        "Побачив у Prozorro, що {rejection_phrase} "
        "відхилено{date_suffix}.\n"
        "\n"
        "Питання після такого рішення зазвичай одне: чи справді підстави були "
        "і чи варто витрачати гроші на оскарження.\n"
        "\n"
        "Дам на нього дуже детальну письмову відповідь: чи були підстави для "
        "відхилення, які ризики та що доцільно робити далі — зіставивши те, що "
        "написав замовник у рішенні, з вимогами тендерної документації, вашими "
        "документами та практикою АМКУ. Якщо підстав для скарги немає, напишу "
        "про це прямо.\n"
        "\n"
        "Зазвичай цього достатньо, щоб самостійно подати скаргу в АМКУ (якщо це "
        "доцільно), не витрачаючи зайвих коштів на юридичний супровід.\n"
        "\n"
        "Вартість — 3 499 грн за одне рішення про відхилення, з усіма підставами, "
        "які в ньому названі. Висновок — протягом 24 годин після оплати.\n"
        "{deadline_block}"
        "\n"
        "Якщо питання актуальне — просто відповідайте на цей лист.\n"
        "\n"
        "З повагою,\n"
        "Віталій Щасливий\n"
        "радник з публічних закупівель TenderWin\n"
        "{phone_text} · {site_label}\n"
        "{opt_out_block}"
    ),
    html=(
        # Увесь текст листа — тут, поряд із текстовою версією: версія листа
        # незмінна, і слова в обох частинах мають збігатися дослівно.
        '<!DOCTYPE html>\n'
        '<html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        # Поштові клієнти з темною темою інакше перефарбовують тло і текст
        # по-різному — лист лишився б темним по темному.
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        '</head>\n'
        '<body style="margin:0;padding:0;background:#ffffff;">\n'
        '<div style="max-width:620px;margin:0;padding:18px 20px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,"
        'Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#1f2328;'
        'background:#ffffff;">\n'

        '<p style="margin:0 0 16px 0;">{greeting}</p>\n'

        '<p style="margin:0 0 16px 0;">Побачив у Prozorro, що '
        '{rejection_html} відхилено{date_suffix}.</p>\n'

        '<p style="margin:0 0 16px 0;">Питання після такого рішення зазвичай '
        'одне: чи справді підстави були і чи варто витрачати гроші на '
        'оскарження.</p>\n'

        '<p style="margin:0 0 8px 0;">Дам на нього дуже детальну письмову '
        'відповідь:</p>\n'
        '<ul style="margin:0 0 12px 0;padding-left:22px;">'
        '<li style="margin:0 0 6px 0;">чи були підстави для відхилення,</li>'
        '<li style="margin:0 0 6px 0;">які ризики,</li>'
        '<li style="margin:0;">що доцільно робити далі</li></ul>\n'
        '<p style="margin:0 0 16px 0;">— зіставивши те, що написав замовник у '
        'рішенні, з вимогами тендерної документації, вашими документами та '
        'практикою АМКУ. Якщо підстав для скарги немає, напишу про це '
        'прямо.</p>\n'

        '<p style="margin:0 0 16px 0;">Зазвичай цього достатньо, щоб '
        'самостійно подати скаргу в АМКУ (якщо це доцільно), не витрачаючи '
        'зайвих коштів на юридичний супровід.</p>\n'

        '<div style="margin:0 0 16px 0;padding:12px 14px;background:#f6f8fa;'
        'border-left:3px solid #c9d1d9;">\n'
        '<p style="margin:0;"><strong>Вартість — 3 499 грн</strong> за одне '
        'рішення про відхилення, з усіма підставами, які в ньому названі.<br>'
        'Висновок — <strong>протягом 24 годин після оплати</strong>.</p>\n'
        '{deadline_html}'
        '</div>\n'

        '<p style="margin:0 0 18px 0;">Якщо питання актуальне — просто '
        'відповідайте на цей лист.</p>\n'

        '<p style="margin:0;font-size:15px;line-height:1.5;color:#3a4149;">'
        'З повагою,<br>'
        '<strong>Віталій Щасливий</strong><br>'
        'радник з публічних закупівель '
        '<a href="{site_url}" style="color:#0a58ca;">TenderWin</a><br>'
        '<a href="tel:{phone_tel}" style="color:#3a4149;'
        'text-decoration:none;">{phone_text}</a> · '
        '<a href="{site_url}" style="color:#0a58ca;">{site_label}</a>'
        '</p>\n'
        '{opt_out_html}'
        '</div></body></html>'
        ),
)

# ----------------------------------------------------------------------------
#  V8_OWNER — версія 23.09.2026 (чинна до V9). Відмінності від V7:
#    * у першому реченні названо компанію і її код (ЄДРПОУ/ІПН);
#    * дата й час відхилення повністю, а не лише день;
#    * строк оскарження — теж із точним часом;
#    * окремий абзац «Я уважно зіставлю…»;
#    * ціна і строк висновку — двома рядками.
#  HTML і текст містять ті самі слова.
# ----------------------------------------------------------------------------
TEMPLATE_V8_OWNER = Template(
    version="V8_OWNER",
    subject="Відхилення у закупівлі {ua_id}: чи є підстави для оскарження?",
    body=(
        "{greeting}\n"
        "\n"
        "{rejection_block}\n"
        "\n"
        "Питання після такого рішення зазвичай одне: чи справді підстави були "
        "і чи варто витрачати гроші на оскарження.\n"
        "\n"
        "Дам на нього дуже детальну письмову відповідь:\n"
        "\n"
        "• чи були підстави для відхилення,\n"
        "• які ризики,\n"
        "• що доцільно робити далі.\n"
        "\n"
        "Я уважно зіставлю те, що написав замовник у рішенні, з вимогами "
        "тендерної документації, вашими документами та практикою АМКУ. Якщо "
        "підстав для скарги немає, напишу про це прямо.\n"
        "\n"
        "Зазвичай цього достатньо, щоб самостійно подати скаргу в АМКУ (якщо це "
        "доцільно), не витрачаючи зайвих коштів на юридичний супровід.\n"
        "\n"
        "Вартість — 3 499 грн за одне рішення про відхилення, з усіма підставами, "
        "які в ньому названі.\n"
        "Висновок — протягом 24 годин після оплати.\n"
        "{deadline_block}"
        "\n"
        "Якщо питання актуальне — просто відповідайте на цей лист.\n"
        "\n"
        "З повагою,\n"
        "Віталій Щасливий\n"
        "радник з публічних закупівель TenderWin\n"
        "{phone_text} · {site_label}\n"
        "{opt_out_block}"
    ),
    html=(
        '<!DOCTYPE html>\n'
        '<html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        '</head>\n'
        '<body style="margin:0;padding:0;background:#ffffff;">\n'
        '<div style="max-width:620px;margin:0;padding:18px 20px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,"
        'Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#1f2328;'
        'background:#ffffff;">\n'

        '<p style="margin:0 0 16px 0;">{greeting}</p>\n'

        '<p style="margin:0 0 16px 0;">{rejection_block_html}</p>\n'

        '<p style="margin:0 0 16px 0;">Питання після такого рішення зазвичай '
        'одне: чи справді підстави були і чи варто витрачати гроші на '
        'оскарження.</p>\n'

        '<p style="margin:0 0 8px 0;">Дам на нього дуже детальну письмову '
        'відповідь:</p>\n'
        '<ul style="margin:0 0 16px 0;padding-left:22px;">'
        '<li style="margin:0 0 6px 0;">чи були підстави для відхилення,</li>'
        '<li style="margin:0 0 6px 0;">які ризики,</li>'
        '<li style="margin:0;">що доцільно робити далі.</li></ul>\n'

        '<p style="margin:0 0 16px 0;">Я уважно зіставлю те, що написав '
        'замовник у рішенні, з вимогами тендерної документації, вашими '
        'документами та практикою АМКУ. Якщо підстав для скарги немає, напишу '
        'про це прямо.</p>\n'

        '<p style="margin:0 0 16px 0;">Зазвичай цього достатньо, щоб '
        'самостійно подати скаргу в АМКУ (якщо це доцільно), не витрачаючи '
        'зайвих коштів на юридичний супровід.</p>\n'

        '<div style="margin:0 0 16px 0;padding:12px 14px;background:#f6f8fa;'
        'border-left:3px solid #c9d1d9;">\n'
        '<p style="margin:0;"><strong>Вартість — 3 499 грн</strong> за одне '
        'рішення про відхилення, з усіма підставами, які в ньому названі.<br>'
        'Висновок — <strong>протягом 24 годин після оплати</strong>.</p>\n'
        '{deadline_html}'
        '</div>\n'

        '<p style="margin:0 0 18px 0;">Якщо питання актуальне — просто '
        'відповідайте на цей лист.</p>\n'

        '<p style="margin:0;font-size:15px;line-height:1.5;color:#3a4149;">'
        'З повагою,<br>'
        '<strong>Віталій Щасливий</strong><br>'
        'радник з публічних закупівель '
        '<a href="{site_url}" style="color:#0a58ca;">TenderWin</a><br>'
        '<a href="tel:{phone_tel}" style="color:#3a4149;'
        'text-decoration:none;">{phone_text}</a> · '
        '<a href="{site_url}" style="color:#0a58ca;">{site_label}</a>'
        '</p>\n'
        '{opt_out_html}'
        '</div></body></html>'
    ),
)

# ----------------------------------------------------------------------------
#  V9_OWNER — ЧИННА ВЕРСІЯ (06.10.2026). V8 з правками власника:
#    * абзац «Я зіставлю те, що написав Замовник…» — з документами Вашої
#      тендерної пропозиції;
#    * «Якщо підстави для скарги є, то складеного мною аналізу зазвичай
#      Вам буде достатньо…» замість «Зазвичай цього достатньо…»;
#    * «Побачити, як виглядає мій аналіз, Ви можете тут: …» з посиланням
#      на сайт замість «Якщо питання актуальне — просто відповідайте…»;
#    * підпис без посилань (ні «TenderWin», ні сайту, ні tel:) і з
#      номером +380 800 357 135.
#  HTML і текст містять ті самі слова.
# ----------------------------------------------------------------------------
TEMPLATE_V9_OWNER = Template(
    version="V9_OWNER",
    subject="Відхилення у закупівлі {ua_id}: чи є підстави для оскарження?",
    body=(
        "{greeting}\n"
        "\n"
        "{rejection_block}\n"
        "\n"
        "Питання після такого рішення зазвичай одне: чи справді підстави були "
        "і чи варто витрачати гроші на оскарження.\n"
        "\n"
        "Дам на нього дуже детальну письмову відповідь:\n"
        "\n"
        "• чи були підстави для відхилення,\n"
        "• які ризики,\n"
        "• що доцільно робити далі.\n"
        "\n"
        "Я зіставлю те, що написав Замовник у рішенні, з вимогами тендерної "
        "документації, документами Вашої тендерної пропозиції та практикою "
        "АМКУ. Якщо підстав для скарги немає, напишу про це прямо.\n"
        "\n"
        "Якщо підстави для скарги є, то складеного мною аналізу зазвичай Вам "
        "буде достатньо, щоб самостійно подати скаргу в АМКУ, не витрачаючи "
        "зайвих коштів на юридичний супровід.\n"
        "\n"
        "Вартість — 3 499 грн за одне рішення про відхилення, з усіма підставами, "
        "які в ньому названі.\n"
        "Висновок — протягом 24 годин після оплати.\n"
        "{deadline_block}"
        "\n"
        "Побачити, як виглядає мій аналіз, Ви можете тут: {example_url}\n"
        "\n"
        "З повагою,\n"
        "Віталій Щасливий\n"
        "радник з публічних закупівель\n"
        "+380 800 357 135\n"
        "{opt_out_block}"
    ),
    html=(
        '<!DOCTYPE html>\n'
        '<html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        '</head>\n'
        '<body style="margin:0;padding:0;background:#ffffff;">\n'
        '<div style="max-width:620px;margin:0;padding:18px 20px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,"
        'Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#1f2328;'
        'background:#ffffff;">\n'

        '<p style="margin:0 0 16px 0;">{greeting}</p>\n'

        '<p style="margin:0 0 16px 0;">{rejection_block_html}</p>\n'

        '<p style="margin:0 0 16px 0;">Питання після такого рішення зазвичай '
        'одне: чи справді підстави були і чи варто витрачати гроші на '
        'оскарження.</p>\n'

        '<p style="margin:0 0 8px 0;">Дам на нього дуже детальну письмову '
        'відповідь:</p>\n'
        '<ul style="margin:0 0 16px 0;padding-left:22px;">'
        '<li style="margin:0 0 6px 0;">чи були підстави для відхилення,</li>'
        '<li style="margin:0 0 6px 0;">які ризики,</li>'
        '<li style="margin:0;">що доцільно робити далі.</li></ul>\n'

        '<p style="margin:0 0 16px 0;">Я зіставлю те, що написав Замовник у '
        'рішенні, з вимогами тендерної документації, документами Вашої '
        'тендерної пропозиції та практикою АМКУ. Якщо підстав для скарги '
        'немає, напишу про це прямо.</p>\n'

        '<p style="margin:0 0 16px 0;">Якщо підстави для скарги є, то '
        'складеного мною аналізу зазвичай Вам буде достатньо, щоб самостійно '
        'подати скаргу в АМКУ, не витрачаючи зайвих коштів на юридичний '
        'супровід.</p>\n'

        '<div style="margin:0 0 16px 0;padding:12px 14px;background:#f6f8fa;'
        'border-left:3px solid #c9d1d9;">\n'
        '<p style="margin:0;"><strong>Вартість — 3 499 грн</strong> за одне '
        'рішення про відхилення, з усіма підставами, які в ньому названі.<br>'
        'Висновок — <strong>протягом 24 годин після оплати</strong>.</p>\n'
        '{deadline_html}'
        '</div>\n'

        '<p style="margin:0 0 18px 0;">Побачити, як виглядає мій аналіз, Ви '
        'можете тут: <a href="{example_url}" style="color:#0a58ca;">'
        '{example_label}</a></p>\n'

        '<p style="margin:0;font-size:15px;line-height:1.5;color:#3a4149;">'
        'З повагою,<br>'
        '<strong>Віталій Щасливий</strong><br>'
        'радник з публічних закупівель<br>'
        '+380 800 357 135'
        '</p>\n'
        '{opt_out_html}'
        '</div></body></html>'
    ),
)

# ----------------------------------------------------------------------------
#  V10_OWNER — ЧИННА ВЕРСІЯ (08.10.2026). V9 слово в слово, змінено лише
#  номер у підписі: «0 800 357 135» замість «+380 800 357 135». Похідна від
#  V9, щоб решта тексту гарантовано збігалася; V9 не змінюється.
# ----------------------------------------------------------------------------
V9_PHONE, V10_PHONE = "+380 800 357 135", "0 800 357 135"
assert TEMPLATE_V9_OWNER.body.count(V9_PHONE) == 1
assert TEMPLATE_V9_OWNER.html.count(V9_PHONE) == 1
TEMPLATE_V10_OWNER = Template(
    version="V10_OWNER",
    subject=TEMPLATE_V9_OWNER.subject,
    body=TEMPLATE_V9_OWNER.body.replace(V9_PHONE, V10_PHONE),
    html=TEMPLATE_V9_OWNER.html.replace(V9_PHONE, V10_PHONE),
)

ALL_TEMPLATES = {t.version: t for t in (TEMPLATE_V4_OWNER, TEMPLATE_V5_REDTEAM,
                                        TEMPLATE_V6_OWNER, TEMPLATE_V7_OWNER,
                                        TEMPLATE_V8_OWNER, TEMPLATE_V9_OWNER,
                                        TEMPLATE_V10_OWNER)}


def active_template() -> Template:
    if LETTER_VERSION not in ALL_TEMPLATES:
        raise _err("TEMPLATE_UNKNOWN", f"Невідома версія листа «{LETTER_VERSION}»",
                   "Усі листи", False,
                   f"Допустимі значення: {', '.join(sorted(ALL_TEMPLATES))}.")
    return ALL_TEMPLATES[LETTER_VERSION]


def short_title(text: str, limit: int = 110) -> str:
    clean = L.clean_spaces(text or "").strip().strip('«»"„“\'')
    clean = clean.replace("«", "„").replace("»", "“")
    if len(clean) <= limit:
        return clean
    return clean[:limit - 1].rsplit(" ", 1)[0] + "…"


def esc(text: Any) -> str:
    """Екранування для HTML. Назва закупівлі приходить із Prozorro як є."""
    import html as _html                                          # noqa: PLC0415
    return _html.escape(str(text or ""), quote=False)


def procurement_phrase(events: list, limit: int = 5, html: bool = False) -> str:
    """«UA-… «назва»» або перелік, якщо сьогодні відхилили кілька пропозицій."""
    parts = []
    for ev in events[:limit]:
        title = short_title(ev.tender_title, 80)
        if html:
            ua = f"<strong>{esc(ev.ua_id)}</strong>"
            parts.append(f"{ua} «{esc(title)}»" if title else ua)
        else:
            parts.append(f"{ev.ua_id} «{title}»" if title else ev.ua_id)
    if len(events) > limit:
        parts.append(f"та ще {len(events) - limit}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " і " + parts[-1]


def rejection_phrase(events: list, html: bool = False) -> str:
    """
    «вашу пропозицію в закупівлі …» або «ваші пропозиції в закупівлях … і …».

    Число залежить від того, скільки відхилень компанія отримала сьогодні.
    Одне відхилення дає текст, слово в слово затверджений власником.
    """
    перелік = procurement_phrase(events, html=html)
    if len(events) == 1:
        return f"вашу пропозицію в закупівлі {перелік}"
    return f"ваші пропозиції в закупівлях {перелік}"


def company_block(events: list, html: bool = False) -> str:
    """«ТОВ «БУДІНВЕСТ» (ЄДРПОУ 12345678)» — як у Prozorro, без здогадок."""
    подія = events[0]
    назва = L.clean_spaces(getattr(подія, "company_name", "") or "")
    код = getattr(подія, "edrpou", "") or ""
    if html:
        назва, код = esc(назва), esc(код)
    if назва and код:
        return f"{назва} ({code_label(код)} {код})"
    return назва or (f"{code_label(код)} {код}" if код else "")


def rejection_block(events: list, html: bool = False) -> str:
    """
    Абзац про відхилення.

    Одне відхилення — речення, затверджене власником слово в слово.
    Кілька — те саме речення і перелік: у кожної закупівлі свій час рішення,
    і зліпити їх в один час означало б сказати неправду.
    """
    фірма = company_block(events, html=html)
    if len(events) == 1:
        подія = events[0]
        назва = short_title(подія.tender_title, 80)
        ua = f"<strong>{esc(подія.ua_id)}</strong>" if html else подія.ua_id
        предмет = f" «{esc(назва) if html else назва}»" if назва else ""
        коли = ua_datetime_full(подія.event_time)
        return (f"Побачив у Prozorro, що пропозицію {фірма} в закупівлі "
                f"{ua}{предмет} відхилено ({коли}).")
    рядки = []
    for подія in events[:5]:
        назва = short_title(подія.tender_title, 80)
        ua = f"<strong>{esc(подія.ua_id)}</strong>" if html else подія.ua_id
        предмет = f" «{esc(назва) if html else назва}»" if назва else ""
        рядки.append(f"{ua}{предмет} — {ua_datetime_full(подія.event_time)}")
    якщо_більше = (f"та ще {len(events) - 5}" if len(events) > 5 else "")
    if якщо_більше:
        рядки.append(якщо_більше)
    вступ = f"Побачив у Prozorro, що пропозиції {фірма} відхилено:"
    if html:
        пункти = "".join(f'<li style="margin:0 0 6px 0;">{р}</li>' for р in рядки)
        return (f'{вступ}</p>\n<ul style="margin:0 0 16px 0;padding-left:22px;">'
                f'{пункти}</ul>\n<p style="margin:0 0 16px 0;">')
    return вступ + "\n" + "\n".join(f"• {р}" for р in рядки)


def analysis_object(events: list) -> str:
    """«вашого відхилення» / «ваших відхилень» — за кількістю подій."""
    return "вашого відхилення" if len(events) == 1 else "ваших відхилень"


def deadline_line(events: list, moment: Optional[datetime] = None) -> str:
    """
    Рядок про строк — лише з даних Prozorro і лише якщо строк ще відкритий.
    Це факт системи, а не тлумачення норми (правило 7).
    """
    if not SHOW_COMPLAINT_DEADLINE:
        return ""
    moment = moment or now()
    dates = [parse_dt(ev.complaint_end) for ev in events]
    dates = [d for d in dates if d and d > moment]
    if not dates:
        return ""
    soonest = min(dates)
    # Точний час, а не лише дата: строк закінчується не опівночі, і людина
    # має бачити те саме, що показує Prozorro.
    хвіст = " (найраніший строк із цих закупівель)" if len(dates) > 1 else ""
    return (f"\nУ Prozorro подати скаргу на це рішення можна до "
            f"{ua_datetime_full(soonest)}{хвіст}.\n")


def deadline_html(events: list, moment: Optional[datetime] = None) -> str:
    """Той самий рядок у HTML: дата виділена, бо це єдиний строк у листі."""
    текст = deadline_line(events, moment).strip()
    if not текст:
        return ""
    дата = текст.rsplit(" до ", 1)[-1].rstrip(".")
    хвіст = ""
    if " (найраніший" in дата:
        дата, хвіст = дата.split(" (найраніший", 1)
        хвіст = " (найраніший" + хвіст
    початок = текст[:len(текст) - len(дата) - len(хвіст) - 1]
    return ('<p style="margin:10px 0 0 0;padding-top:10px;'
            'border-top:1px solid #e1e4e8;">'
            f'{esc(початок)}<strong>{esc(дата)}</strong>{esc(хвіст)}.</p>\n')


#: Мітки, які можуть бути порожніми законно (строк, відмова, дата події).
OPTIONAL_FIELDS = ("deadline_block", "deadline_html", "opt_out_block",
                   "opt_out_html", "date_suffix")


@dataclass(frozen=True)
class RenderedLetter:
    """Готовий лист: тема, текст і — якщо версія його має — HTML."""
    subject: str
    body: str
    html: str = ""


def render_letter(events: list, greeting: str,
                  template: Optional[Template] = None,
                  moment: Optional[datetime] = None) -> RenderedLetter:
    """Готовий лист. Порожніх міток у тексті не лишається."""
    tpl = template or active_template()
    main = events[0]
    ctx = {
        "greeting": greeting,
        "ua_id": main.ua_id,
        "procurement": procurement_phrase(events),
        "rejection_phrase": rejection_phrase(events),
        "rejection_block": rejection_block(events),
        "analysis_object": analysis_object(events),
        "date_suffix": (f" {ua_date_short(main.event_time)}"
                        if main.time_reliability in ("HIGH", "MEDIUM") else ""),
        "deadline_block": deadline_line(events, moment),
        "opt_out_block": ("\nЯкщо такі листи вам не потрібні — відповідайте "
                          "«стоп», і я більше не писатиму.\n"
                          if SHOW_OPT_OUT_LINE else ""),
        "phone_text": PHONE_TEXT,
        "site_label": SITE_LABEL,
        "example_url": ANALYSIS_EXAMPLE_URL,
    }
    if "{rejection_block}" in tpl.body and not all(
            getattr(ev, "event_time", None) for ev in events):
        raise _err("EVENT_TIME_MISSING", "У події немає часу рішення",
                   "Один лист, НЕ сформовано", True,
                   "Лист називає точний час відхилення — без нього він "
                   "стверджував би невстановлене. Лід іде на ручний перегляд.")
    needed = set(re.findall(r"\{(\w+)\}", tpl.subject + tpl.body))
    empty = sorted(f for f in needed
                   if f not in OPTIONAL_FIELDS
                   and not str(ctx.get(f) or "").strip())
    if empty:
        raise _err("LETTER_FIELD_EMPTY", f"Порожні поля листа: {', '.join(empty)}",
                   "Один лист, НЕ сформовано", True,
                   "Лід іде на ручний перегляд: лист із порожньою міткою "
                   "клієнту не надсилається.")
    subject = tpl.subject.format(**ctx)
    body = tpl.body.format(**ctx).replace("\n\n\n", "\n\n")
    html = render_html(tpl, ctx, events, moment)
    left = E._leftover_placeholders(subject + "\n" + body + "\n" + html)  # noqa: SLF001
    if left:
        raise _err("PLACEHOLDER_IN_LETTER", f"У листі лишились мітки: {left}",
                   "Один лист, НЕ сформовано", True, "Лід іде на ручний перегляд.")
    return RenderedLetter(subject=subject, body=body, html=html)


def render_html(tpl: Template, ctx: dict, events: list,
                moment: Optional[datetime] = None) -> str:
    """
    HTML-частина листа.

    Усе, що прийшло з Prozorro або від людини, екранується: назва закупівлі
    цілком може містити «&» чи «<». Порожній результат означає, що лист піде
    лише текстом — це законний стан, а не помилка.
    """
    if not tpl.html or LETTER_FORMAT != "HTML_AND_TEXT":
        return ""
    html_ctx = {
        "greeting": esc(ctx["greeting"]),
        "rejection_html": rejection_phrase(events, html=True),
        "rejection_block_html": rejection_block(events, html=True),
        "date_suffix": esc(ctx["date_suffix"]),
        "deadline_html": deadline_html(events, moment),
        "opt_out_html": ('<p style="margin:14px 0 0 0;font-size:13px;'
                         'color:#6b7480;">Якщо такі листи вам не потрібні — '
                         'відповідайте «стоп», і я більше не писатиму.</p>\n'
                         if SHOW_OPT_OUT_LINE else ""),
        "site_url": SITE_URL,
        "site_label": SITE_LABEL,
        "phone_text": PHONE_TEXT,
        "phone_tel": PHONE_TEL,
        "example_url": esc(ANALYSIS_EXAMPLE_URL),
        "example_label": esc(ANALYSIS_EXAMPLE_LABEL),
    }
    return tpl.html.format(**html_ctx)


# ============================================================================
#  БЛОК 8. ПОЛІТИКА: КОМУ МОЖНА ПИСАТИ
# ----------------------------------------------------------------------------
#  Порядок перевірок навмисний: спершу те, що забороняє назавжди, потім те,
#  що просто відкладає.
# ============================================================================
@dataclass
class Verdict:
    allowed: bool
    state: str
    reason: str = ""


#: Одна адреса без пробілів, ком і крапок підряд. Те, що не проходить,
#: іде на ручний перегляд: лист «у нікуди» гірший за пропущений.
_STRICT_EMAIL = re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}")


def valid_email(email: Optional[str]) -> bool:
    return bool(email) and len(email) <= 254 and bool(
        _STRICT_EMAIL.fullmatch(email.strip().lower()))


def evaluate(repo: Repo, company_id: str, email: Optional[str],
             events: list) -> Verdict:
    stop = repo.suppression(company_id, email)
    if stop is not None:
        return Verdict(False, "SUPPRESSED", f"стоп-лист: {stop['state']}")

    touched = repo.has_first_touch(company_id)
    if touched is not None:
        return Verdict(False, "NOT_ELIGIBLE",
                       f"компанія вже отримувала перший лист "
                       f"({touched['status']}, {(touched['generated_at'] or '')[:10]})")

    if not email:
        return Verdict(False, "REVIEW", "не знайдено e-mail учасника")
    if not valid_email(email):
        return Verdict(False, "REVIEW", f"e-mail учасника з помилкою: «{email}»")

    with_route = [ev for ev in events if ev.complaint_start or ev.complaint_end]
    if SKIP_WITHOUT_COMPLAINT_ROUTE and not with_route:
        return Verdict(False, "NO_COMPLAINT_ROUTE",
                       "у цій закупівлі немає періоду оскарження — лист про "
                       "оскарження обіцяв би те, чого немає")

    if MIN_LOT_VALUE:
        best = max((ev.lot_value or ev.tender_value or 0) for ev in events)
        if best < MIN_LOT_VALUE:
            return Verdict(False, "NOT_ELIGIBLE",
                           f"вартість лота менша за поріг {MIN_LOT_VALUE:,.0f}".replace(",", " "))
    return Verdict(True, "IN_LETTER")


def delivery_address(company_id: str, allowlist: list,
                     contact_email: Optional[str] = None) -> str:
    """
    Куди піде лист.

    LIVE — на адресу самої компанії (з Prozorro), і нікуди більше: те саме
    перевіряє тригер бази. TEST — на тестову скриньку, детерміновано:
    повторний прогін надсилає на ту саму адресу, і листи не перемішуються.
    """
    if MODE == "LIVE":
        if not valid_email(contact_email):
            raise ERR["LIVE_DELIVERY_NOT_CONTACT"](contact_email or "")
        return contact_email.strip().lower()
    if MODE != "TEST":
        raise ERR["MODE_UNKNOWN"](MODE)
    if not allowlist:
        raise ERR["TEST_ALLOWLIST_EMPTY"]()
    import hashlib                                              # noqa: PLC0415
    index = int(hashlib.sha256(company_id.encode("utf-8")).hexdigest()[:8], 16)
    return allowlist[index % len(allowlist)]


# ============================================================================
#  БЛОК 9. ЧЕРГА І ВІДПРАВКА
# ============================================================================
@dataclass
class Draft:
    run_id: str
    batch_id: str
    company_id: str
    contact_id: Optional[str]
    template_version: str
    subject: str
    body: str
    html: str
    contact_email: Optional[str]
    delivery_email: str
    message_id: str
    event_ids: list


def build_letter_bytes(row: sqlite3.Row) -> bytes:
    """
    Збирає лист із рядка черги — один шлях і для прев'ю, і для відправки.

    Якщо в рядку є HTML, лист іде двома частинами: текст і HTML. Поштовий
    клієнт показує оформлену, а той, що HTML не показує (або фільтр, що
    читає лише текст), бачить ті самі слова.
    """
    return E.build_rfc822(
        sender_name=SENDER_NAME, sender_email=SENDER_EMAIL,
        to_email=row["delivery_email_actual"], subject=row["subject_rendered"],
        body=row["body_rendered"], message_id=row["message_id_header"],
        # Заголовок «кому призначався» — лише в тестовому листі.
        intended_to=(row["contact_email_original"] if row["mode"] == "TEST" else None),
        html_body=row["body_html"] or "")


def make_transport(dry_run: bool, out_dir: str):
    """Сухий прогін узагалі не має транспорту до Gmail — надіслати нічим."""
    if dry_run:
        return E.DryRunTransport(os.path.join(out_dir, "eml"))
    if MODE not in ("TEST", "LIVE"):
        raise ERR["MODE_UNKNOWN"](MODE)
    # Доступ перевіряємо до створення транспорту: інакше людина побачила б
    # технічну помилку бібліотеки Google замість зрозумілої дії.
    є_доступ, звідки = gmail_secret_ready()
    if not є_доступ:
        raise ERR["TRANSPORT_UNAVAILABLE"](звідки)
    return E.GmailOAuthTransport(SENDER_EMAIL, read_enabled=True)


def verify_sent(transport, provider_message_id: str) -> tuple:
    """
    (підтверджено, пояснення). «Прийнято» ще не означає «надіслано»: у
    документації Gmail прямо сказано, що відповідь 200 цього не доводить.
    Підтвердженням вважаємо мітку SENT на самому листі.
    """
    api = getattr(transport, "_read_api", None)
    if api is None or not provider_message_id:
        return False, "немає доступу на читання — стан ACCEPTED_BY_PROVIDER"
    try:
        msg = api.users().messages().get(
            userId="me", id=provider_message_id, format="metadata",
            metadataHeaders=["Message-ID"]).execute()
    except Exception as exc:                                    # noqa: BLE001
        return False, f"перевірка не вдалася: {type(exc).__name__}"
    labels = msg.get("labelIds") or []
    if "SENT" in labels:
        return True, "лист у «Надісланих»"
    return False, f"мітки листа: {', '.join(labels) or 'немає'}"


# ============================================================================
#  БЛОК 10. РОБОЧА КАРТКА ЗАКУПІВЛІ
# ----------------------------------------------------------------------------
#  Документи не завантажуються. У картці — назви й посилання, які людина
#  відкриває сама. Наявну картку скрипт не перезаписує: у ній можуть бути
#  ваші нотатки.
# ============================================================================
def complaint_state(end_iso: Optional[str], moment: Optional[datetime] = None) -> str:
    end = parse_dt(end_iso)
    if not end:
        return NOT_ESTABLISHED
    return "ВІДКРИТИЙ" if end > (moment or now()) else "ЗАКРИТИЙ"


def money(value: Optional[float]) -> str:
    if value is None:
        return NOT_ESTABLISHED
    return f"{value:,.0f} грн".replace(",", " ")


def write_card(row: sqlite3.Row, out_dir: str, letter: Optional[sqlite3.Row] = None,
               run_id: str = "") -> tuple[str, bool]:
    """
    Одна картка на подію відхилення.

    Повертає (шлях, чи створено щойно). Наявний файл не переписується і не
    рахується як нова картка: у ньому можуть бути ваші нотатки.
    """
    try:
        import docx                                             # noqa: PLC0415
        from docx.shared import Pt                              # noqa: PLC0415
    except ImportError:
        return "", False
    day = (row["event_time"] or "")[:10] or now().strftime("%Y-%m-%d")
    folder = os.path.join(out_dir, "kartky", day)
    os.makedirs(folder, exist_ok=True)
    # Ідентифікатор рішення в назві обовʼязковий: в одній закупівлі ту саму
    # компанію можуть відхилити двічі (два лоти, два рішення), і без нього
    # друга картка мовчки не створилася б — це втрата доказу.
    name = (f"{row['ua_id']}__{row['edrpou_norm']}__"
            f"{str(row['object_id'] or 'bez-id')[:8]}.docx")
    path = os.path.join(folder, re.sub(r"[^\w.\-]", "_", name))
    if os.path.exists(path):
        return path, False               # ваші нотатки не перезаписуються

    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    doc.add_heading("РОБОЧА КАРТКА ЗАКУПІВЛІ", level=1)
    doc.add_paragraph(f"{row['ua_id']} · {row['company_name']} · "
                      f"подія {(row['event_time'] or '')[:16].replace('T', ' ')}")

    def table(title: str, rows: list) -> None:
        doc.add_heading(title, level=2)
        t = doc.add_table(rows=0, cols=2)
        t.style = "Table Grid"
        for left, right in rows:
            cells = t.add_row().cells
            cells[0].text = str(left)
            cells[1].text = str(right if right not in (None, "") else NOT_ESTABLISHED)

    table("Закупівля", [
        ("Ідентифікатор", row["ua_id"]),
        ("Предмет", row["title"]),
        ("Замовник", f"{row['buyer_name']} (ЄДРПОУ {row['buyer_edrpou'] or '—'})"),
        ("CPV", row["cpv"]),
        ("Тип процедури", row["method_type"]),
        ("Стан закупівлі", row["tender_status"]),
        ("Посилання", PROZORRO_TENDER_URL.format(ua_id=row["ua_id"])),
    ])
    table("Учасник", [
        ("Компанія", row["company_name"]),
        ("ЄДРПОУ/ІПН", row["edrpou_norm"]),
        ("Контактна особа", row["contact_name_raw"]),
        ("E-mail", row["contact_email"]),
        ("Телефон", row["contact_phone"]),
        ("Спільна пропозиція", "так" if row["joint_bid"] else "ні"),
        ("Зіставлення учасника", row["identity_note"]),
    ])
    table("Подія відхилення", [
        ("Стадія", "рішення після оцінки" if row["stage"] == "awards"
         else "прекваліфікація"),
        ("Ідентифікатор обʼєкта", row["object_id"]),
        ("bid_id / lot_id", f"{row['bid_id'] or '—'} / {row['lot_id'] or '—'}"),
        ("Час події", (row["event_time"] or "").replace("T", " ")[:19]),
        ("Джерело часу", row["event_time_source"]),
        ("Чому саме це поле", row["event_time_basis"]),
        ("Надійність часу", row["event_time_reliability"]),
        ("Статус обʼєкта", row["object_status"]),
        ("Статус перевірено", (row["status_checked_at"] or "").replace("T", " ")[:19]),
    ])
    table("Суми", [
        ("Очікувана вартість закупівлі", money(row["tender_value"])),
        ("Очікувана вартість лота", money(row["lot_value"])),
        ("Лот визначено", row["lot_resolved"]),
        ("Ціна пропозиції учасника", money(row["bid_value"])),
        ("Ціна переможця", money(row["winner_value"])),
        ("Переможець", row["winner_name"]),
    ])
    table("Строк оскарження (за даними Prozorro)", [
        ("Початок періоду", (row["complaint_start"] or "").replace("T", " ")[:19]),
        ("Кінець періоду", (row["complaint_end"] or "").replace("T", " ")[:19]),
        ("Стан", complaint_state(row["complaint_end"])),
        ("Увага", "Це поле системи, а не юридичний висновок. Строк для "
                  "конкретної закупівлі перевіряйте окремо."),
    ])

    doc.add_heading("Що написав замовник (дослівно)", level=2)
    doc.add_paragraph(row["reason_raw"] or
                      f"{NOT_ESTABLISHED}. У короткому описі рішення тексту немає — "
                      f"відкрийте протокол за посиланням нижче.")

    for title, key in (("Документи рішення замовника", "docs_decision"),
                       ("Документи пропозиції учасника", "docs_bid"),
                       ("Тендерна документація і зміни", "docs_td")):
        try:
            items = json.loads(row[key] or "[]")
        except (ValueError, TypeError):
            items = []
        позначка = ("копії файлів — у службовій картці"
                    if key == "docs_decision" and SERVICE_CARDS else "не завантажуються")
        doc.add_heading(f"{title} — {len(items)} шт. ({позначка})", level=2)
        if not items:
            doc.add_paragraph("У структурі закупівлі документів цього типу немає.")
            continue
        t = doc.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        head = t.rows[0].cells
        head[0].text, head[1].text, head[2].text = "Назва", "Опубліковано", "Посилання"
        for item in items[:40]:
            cells = t.add_row().cells
            cells[0].text = item.get("назва", "")
            cells[1].text = (item.get("опубліковано", "") or "").replace("T", " ")[:16]
            cells[2].text = item.get("посилання", "")

    doc.add_heading("Лист", level=2)
    if letter is None:
        doc.add_paragraph("Лист не формувався. Причина: "
                          f"{row['state_reason'] or row['state']}.")
    else:
        doc.add_paragraph(f"Версія шаблону: {letter['template_version']}"
                          + (" · оформлення HTML + текст" if letter["body_html"]
                             else " · лише текст"))
        doc.add_paragraph(f"Тема: {letter['subject_rendered']}")
        doc.add_paragraph(f"Стан: {letter['status']}")
        doc.add_paragraph(f"Кому призначався: {letter['contact_email_original'] or '—'}")
        doc.add_paragraph(("Куди фактично пішов (тестовий режим): "
                           if letter["mode"] == "TEST" else "Куди пішов (бойовий лист): ")
                          + f"{letter['delivery_email_actual']}")
        doc.add_paragraph("Текст листа:")
        for piece in (letter["body_rendered"] or "").split("\n"):
            doc.add_paragraph(piece)

    doc.add_heading("Нотатки (скрипт сюди не пише ніколи)", level=2)
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph(f"Прогін {run_id} · TenderWin Lead & Mail {VERSION}")
    doc.save(path)
    return path, True


# ============================================================================
#  БЛОК 11. ОРКЕСТРАТОР
# ----------------------------------------------------------------------------
#  Тут немає бізнес-правил — лише порядок кроків. Кожен крок можна викликати
#  окремо, і саме так вони перевіряються тестами.
# ============================================================================
class LeadMail:
    def __init__(self, db_path: Optional[str] = None, out_dir: Optional[str] = None,
                 verbose: bool = True):
        self.db = Db(db_path or DB_PATH)
        self.repo = Repo(self.db)
        self.out_dir = out_dir or OUT_DIR
        os.makedirs(self.out_dir, exist_ok=True)
        self.verbose = verbose
        # Хвіст робить ідентифікатор унікальним, навіть якщо два прогони
        # почалися в ту саму секунду: інакше повторний запуск впав би на
        # UNIQUE-обмеженні runs.run_id і втратив би облік прогону.
        stamp = now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.run_id = f"R{stamp}"
        self.batch_id = f"B{stamp}"

    # --- крок 0: підготовка -------------------------------------------------
    def prepare(self, legacy_db: Optional[str] = None) -> dict:
        """Білий список у базу і одноразове перенесення історії движка."""
        stats = {"тестових_адрес": (
            self.repo.sync_allowlist(TEST_RECIPIENTS)
            if MODE == "TEST" or [a for a in TEST_RECIPIENTS if a.strip()] else 0)}
        row = self.db.one("SELECT value FROM schema_meta WHERE key = 'legacy_imported'")
        if row is None:
            imported = self.repo.import_legacy(legacy_db or LEGACY_DB_PATH)
            self.db.x("INSERT INTO schema_meta(key, value) VALUES"
                      " ('legacy_imported', ?)",
                      (json.dumps({"коли": now_iso(), **imported}, ensure_ascii=False),))
            stats["перенесено_з_движка"] = imported
        else:
            stats["перенесено_з_движка"] = "вже виконано"
        return stats

    # --- крок 1: знайти -----------------------------------------------------
    def scan(self, window: Window, source: Optional[Prozorro] = None) -> ScanResult:
        client = source or Prozorro(verbose=self.verbose)
        retry = [r["tender_uid"] for r in self.db.q(
            "SELECT DISTINCT tender_uid FROM scan_gaps WHERE resolved = 0 LIMIT 500")]
        return client.scan(window, retry_uids=retry)

    # --- крок 2: записати ---------------------------------------------------
    def ingest(self, result: ScanResult) -> dict:
        stats = {"нових_подій": 0, "уже_були": 0, "на_перевірку": 0}
        rearmed_before = self.repo.rearmed
        for facts in result.events:
            company_id = self.repo.company(facts.edrpou, facts.company_name)
            tender_id = self.repo.tender({
                "tender_id": facts.tender_id, "ua_id": facts.ua_id,
                "title": facts.tender_title, "buyer_name": facts.buyer_name,
                "buyer_edrpou": facts.buyer_edrpou, "cpv": facts.cpv,
                "method_type": facts.method_type,
                "tender_status": facts.tender_status,
                "tender_value": facts.tender_value})
            greeting, confidence, source = greeting_for(facts.person.name_raw,
                                                        facts.company_name)
            self.repo.contact(company_id, facts.person.email, facts.person.name_raw,
                              facts.person.phone, greeting, confidence, source)
            _, is_new = self.repo.event(facts, tender_id, company_id, self.run_id)
            stats["нових_подій" if is_new else "уже_були"] += 1
        for facts, why in result.review:
            stats["на_перевірку"] += 1
            if facts is None:
                continue
            company_id = self.repo.company(facts.edrpou, facts.company_name)
            tender_id = self.repo.tender({
                "tender_id": facts.tender_id, "ua_id": facts.ua_id,
                "title": facts.tender_title, "buyer_name": facts.buyer_name,
                "buyer_edrpou": facts.buyer_edrpou, "cpv": facts.cpv,
                "method_type": facts.method_type,
                "tender_status": facts.tender_status,
                "tender_value": facts.tender_value})
            event_id, _ = self.repo.event(facts, tender_id, company_id, self.run_id)
            self.repo.set_event_state(event_id, "REVIEW", why)
        for uid, code, detail in result.gaps:
            self.db.x("INSERT INTO scan_gaps(gap_id, run_id, tender_uid, error_code,"
                      " detail, noted_at) VALUES (?,?,?,?,?,?)",
                      (new_id(), self.run_id, uid, code, detail, now_iso()))
        self.db.x("UPDATE scan_gaps SET resolved = 1 WHERE tender_uid IN"
                  " (SELECT t.tender_id FROM tenders t) AND run_id <> ?", (self.run_id,))
        if self.repo.rearmed > rearmed_before:
            stats["повернуто_з_тесту"] = self.repo.rearmed - rearmed_before
        return stats

    # --- крок 3: скласти листи ---------------------------------------------
    def build_letters(self) -> dict:
        """
        Одна компанія — один лист, і в ньому всі її сьогоднішні закупівлі.
        Порядок детермінований: головна закупівля — найдорожчий лот, за
        рівності — найраніша подія.
        """
        stats = {"листів": 0, "SUPPRESSED": 0, "NOT_ELIGIBLE": 0,
                 "REVIEW": 0, "NO_COMPLAINT_ROUTE": 0, "помилок": 0}
        rows = self.db.q(
            "SELECT e.*, c.edrpou_norm, c.company_name, t.ua_id, t.title,"
            "       t.buyer_name"
            "  FROM events e"
            "  JOIN companies c ON c.company_id = e.company_id"
            "  JOIN tenders   t ON t.tender_id = e.tender_id"
            " WHERE e.state = 'NEW' AND e.run_id = ?"
            " ORDER BY c.company_id, COALESCE(e.lot_value, e.tender_value, 0) DESC,"
            "          e.event_time", (self.run_id,))
        by_company: dict = {}
        for row in rows:
            by_company.setdefault(row["company_id"], []).append(row)

        allow = [r["email"] for r in self.db.q(
            "SELECT email FROM test_allowlist ORDER BY email")]
        for company_id, group in by_company.items():
            events = [self._facts_from_row(r) for r in group]
            contact = self.db.one(
                "SELECT contact_id, contact_email, contact_name_raw, contact_vocative"
                "  FROM contacts WHERE company_id = ?"
                " ORDER BY (contact_email IS NULL), created_at LIMIT 1", (company_id,))
            email = contact["contact_email"] if contact else None
            verdict = evaluate(self.repo, company_id, email, events)
            if not verdict.allowed:
                stats[verdict.state] = stats.get(verdict.state, 0) + 1
                for row in group:
                    self.repo.set_event_state(row["event_id"], verdict.state,
                                              verdict.reason)
                continue
            greeting = (contact["contact_vocative"] if contact else "") or SAFE_GREETING
            try:
                letter = render_letter(events, greeting)
            except MailError as err:
                stats["REVIEW"] += 1
                for row in group:
                    self.repo.set_event_state(row["event_id"], "REVIEW", err.what)
                continue
            idem = new_id()
            draft = Draft(run_id=self.run_id, batch_id=self.batch_id,
                          company_id=company_id,
                          contact_id=contact["contact_id"] if contact else None,
                          template_version=active_template().version,
                          subject=letter.subject, body=letter.body,
                          html=letter.html, contact_email=email,
                          delivery_email=delivery_address(company_id, allow, email),
                          message_id=E.build_message_id(idem),
                          event_ids=[r["event_id"] for r in group])
            try:
                self.repo.reserve(draft)
            except MailError as err:
                stats["помилок" if err.code == "QUEUE_INSERT_FAILED"
                      else "NOT_ELIGIBLE"] += 1
                for row in group:
                    self.repo.set_event_state(row["event_id"], "NOT_ELIGIBLE", err.what)
                continue
            stats["листів"] += 1
            for row in group:
                self.repo.set_event_state(row["event_id"], "IN_LETTER", "")
        return stats

    def _facts_from_row(self, row: sqlite3.Row) -> Rejection:
        """Рядок бази -> факти для шаблона. Без повторного походу в мережу."""
        return Rejection(
            ua_id=row["ua_id"], tender_id=row["tender_id"],
            tender_title=row["title"] or "", buyer_name=row["buyer_name"] or "",
            buyer_edrpou="", cpv="", method_type="", tender_status="",
            stage=row["stage"], object_id=row["object_id"], bid_id=row["bid_id"],
            lot_id=row["lot_id"],
            event_time=parse_dt(row["event_time"]) or now(),
            time_source=row["event_time_source"], time_basis=row["event_time_basis"],
            time_reliability=row["event_time_reliability"],
            object_status=row["object_status"], edrpou=row["edrpou_norm"],
            company_name=row["company_name"], person=Person(),
            tender_value=row["tender_value"], lot_value=row["lot_value"],
            bid_value=row["bid_value"], complaint_start=row["complaint_start"],
            complaint_end=row["complaint_end"])

    # --- крок 4: черга попередніх партій ------------------------------------
    def cancel_stale(self) -> int:
        """
        Листи, складені раніше і так і не надіслані, не змішуються з
        сьогоднішньою розсилкою. Компанія при цьому звільняється: листа їй
        ніхто не надсилав.
        """
        stale = self.repo.stale_queued(self.batch_id)
        for row in stale:
            self.repo.set_outreach(row["outreach_id"], "CANCELLED",
                                   error_code="STALE_BATCH",
                                   error_detail="лист іншої партії, не надсилається")
        return len(stale)

    # --- крок 5: сухий прогін ----------------------------------------------
    def preview(self) -> dict:
        """Складає листи у файли. Стан у базі не змінює — надіслати звідси
        структурно неможливо."""
        folder = os.path.join(self.out_dir, "eml")
        os.makedirs(folder, exist_ok=True)
        rows = self.repo.queued(self.batch_id, 10_000)
        html_count = 0
        for row in rows:
            raw = build_letter_bytes(row)
            base = re.sub(r"[^\w.\-]", "_", f"{row['delivery_email_actual']}_"
                                            f"{row['outreach_id'][:8]}")
            with open(os.path.join(folder, base + ".eml"), "wb") as fh:
                fh.write(raw)
            with open(os.path.join(folder, base + ".txt"), "w", encoding="utf-8") as fh:
                куди = ("Куди піде в тесті" if row["mode"] == "TEST"
                        else "Куди піде (БОЙОВИЙ лист клієнту)")
                fh.write(f"Кому призначався: {row['contact_email_original']}\n"
                         f"{куди}: {row['delivery_email_actual']}\n"
                         f"Тема: {row['subject_rendered']}\n\n{row['body_rendered']}")
            # Той самий лист, який побачить клієнт: відкривається у браузері.
            if row["body_html"]:
                html_count += 1
                with open(os.path.join(folder, base + ".html"), "w",
                          encoding="utf-8") as fh:
                    fh.write(row["body_html"])
        return {"складено": len(rows), "з_оформленням": html_count, "тека": folder}

    # --- крок 6: відправка ------------------------------------------------------
    def send(self, transport=None, max_sends: Optional[int] = None,
             paced: bool = True) -> dict:
        """TEST — на тестові скриньки; LIVE — клієнтам."""
        if MODE not in ("TEST", "LIVE"):
            raise ERR["MODE_UNKNOWN"](MODE)
        if MODE == "TEST" and not self.db.q("SELECT email FROM test_allowlist LIMIT 1"):
            raise ERR["TEST_ALLOWLIST_EMPTY"]()
        transport = transport or make_transport(dry_run=False, out_dir=self.out_dir)
        stats = {"SENT_CONFIRMED": 0, "SENT": 0, "SEND_FAILED": 0,
                 "DELIVERY_UNKNOWN": 0}
        rows = self.repo.queued(self.batch_id, max_sends or MAX_SENDS_PER_RUN)
        for index, row in enumerate(rows):
            if index and paced:
                pause = random.uniform(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
                if self.verbose:
                    print(f"    пауза {pause:.0f} с …", flush=True)
                time.sleep(pause)
            self.repo.set_outreach(row["outreach_id"], "SENDING")
            raw = build_letter_bytes(row)
            result = transport.send(raw, row["message_id_header"])
            if not result.ok and getattr(result, "unknown", False):
                self.repo.set_outreach(row["outreach_id"], "DELIVERY_UNKNOWN",
                                       error_code="UNKNOWN",
                                       error_detail=(result.error or "")[:200])
                stats["DELIVERY_UNKNOWN"] += 1
                continue
            if not result.ok:
                self.repo.set_outreach(row["outreach_id"], "SEND_FAILED",
                                       error_code="SEND_FAILED",
                                       error_detail=(result.error or "")[:200])
                stats["SEND_FAILED"] += 1
                continue
            confirmed, why = verify_sent(transport, result.message_id)
            self.repo.set_outreach(
                row["outreach_id"], "SENT_CONFIRMED" if confirmed else "SENT",
                provider_message_id=result.message_id,
                provider_thread_id=result.thread_id,
                provider_timestamp=result.provider_timestamp,
                sent_at=now_iso(), error_code=None if confirmed else "NOT_VERIFIED",
                error_detail=None if confirmed else why)
            stats["SENT_CONFIRMED" if confirmed else "SENT"] += 1
            if self.verbose:
                print(f"    [{index + 1}/{len(rows)}] "
                      f"{'SENT_CONFIRMED' if confirmed else 'SENT'} → "
                      f"{row['delivery_email_actual']}", flush=True)
        return stats

    # --- крок 7: звірка після збою ------------------------------------------
    def reconcile(self, transport=None) -> dict:
        stats = {"перевірено": 0, "знайдено": 0, "на_ручний_перегляд": 0,
                 "звірка_не_вдалась": 0}
        rows = self.repo.unresolved()
        if not rows:
            return stats
        transport = transport or make_transport(dry_run=False, out_dir=self.out_dir)
        for row in rows:
            stats["перевірено"] += 1
            try:
                found = transport.find_by_message_id(row["message_id_header"])
            except Exception as exc:                            # noqa: BLE001
                stats["звірка_не_вдалась"] += 1
                self.repo.set_outreach(row["outreach_id"], "DELIVERY_UNKNOWN",
                                       error_code="RECONCILE_ERROR",
                                       error_detail=type(exc).__name__)
                continue
            if found:
                self.repo.set_outreach(row["outreach_id"], "SENT_CONFIRMED",
                                       provider_message_id=found.get("id"),
                                       provider_thread_id=found.get("threadId"),
                                       sent_at=now_iso(), error_code=None,
                                       error_detail=None)
                stats["знайдено"] += 1
            else:
                self.repo.set_outreach(row["outreach_id"], "DELIVERY_UNKNOWN",
                                       error_code="MANUAL_REVIEW_REQUIRED",
                                       error_detail="звірка не знайшла листа; "
                                                    "сліпого повтору не буде")
                stats["на_ручний_перегляд"] += 1
        return stats

    # --- крок 8: картки -----------------------------------------------------
    def cards(self) -> dict:
        rows = self.db.q(
            "SELECT e.*, c.edrpou_norm, c.company_name, t.ua_id, t.title,"
            "       t.buyer_name, t.buyer_edrpou, t.cpv, t.method_type,"
            "       t.tender_status,"
            "       (SELECT contact_email FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_email,"
            "       (SELECT contact_name_raw FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_name_raw,"
            "       (SELECT contact_phone FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_phone"
            "  FROM events e"
            "  JOIN companies c ON c.company_id = e.company_id"
            "  JOIN tenders   t ON t.tender_id = e.tender_id"
            " WHERE e.run_id = ?", (self.run_id,))
        made, existed, skipped = 0, 0, 0
        for row in rows:
            letter = self.db.one(
                "SELECT * FROM outreach WHERE batch_id = ? AND company_id = ?"
                " ORDER BY generated_at DESC LIMIT 1",
                (self.batch_id, row["company_id"]))
            path, is_new = write_card(row, self.out_dir, letter, self.run_id)
            if not path:
                skipped += 1
            elif is_new:
                made += 1
            else:
                existed += 1
        return {"карток": made, "уже_були": existed, "не_створено": skipped,
                "тека": os.path.join(self.out_dir, "kartky")}

    # --- крок 9: службові картки з протоколами (з 1.3.0) --------------------
    def service_card_rows(self, run_id: Optional[str] = None,
                          day: Optional[str] = None) -> list:
        """
        Події для службових карток: за прогоном або за київською датою події.

        Лист шукаємо саме з цією подією (event_ids), а не будь-який лист
        компанії: інакше картка приписала б події чужий лист.
        """
        sql = (
            "SELECT e.*, c.edrpou_norm, c.company_name, t.ua_id, t.title,"
            "       t.buyer_name, t.buyer_edrpou, t.cpv, t.method_type,"
            "       t.tender_status,"
            "       (SELECT contact_email FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_email,"
            "       (SELECT contact_name_raw FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_name_raw,"
            "       (SELECT contact_phone FROM contacts k WHERE k.company_id = e.company_id"
            "          ORDER BY (contact_email IS NULL), created_at LIMIT 1) contact_phone,"
            "       (SELECT o.status FROM outreach o"
            "         WHERE o.event_ids LIKE '%' || e.event_id || '%'"
            "         ORDER BY o.generated_at DESC LIMIT 1) letter_status,"
            "       (SELECT o.template_version FROM outreach o"
            "         WHERE o.event_ids LIKE '%' || e.event_id || '%'"
            "         ORDER BY o.generated_at DESC LIMIT 1) letter_template"
            "  FROM events e"
            "  JOIN companies c ON c.company_id = e.company_id"
            "  JOIN tenders   t ON t.tender_id = e.tender_id")
        if day:
            target = datetime.strptime(day, "%Y-%m-%d").date()
            # Час у базі записаний із зсувом. Беремо запас у добу з кожного
            # боку і звіряємо київську дату вже в Python.
            low = (target - timedelta(days=1)).isoformat()
            high = (target + timedelta(days=2)).isoformat()
            rows = self.db.q(sql + " WHERE e.event_time >= ? AND e.event_time < ?"
                             " ORDER BY e.event_time", (low, high))
            rows = [r for r in rows if parse_dt(r["event_time"])
                    and parse_dt(r["event_time"]).astimezone(KYIV).date() == target]
        else:
            rows = self.db.q(sql + " WHERE e.run_id = ? ORDER BY e.event_time",
                             (run_id or self.run_id,))
        return [dict(r) for r in rows]

    def service_cards(self, run_id: Optional[str] = None, day: Optional[str] = None,
                      label: str = "", fresh: Optional[bool] = None,
                      tender_source=None, doc_fetcher=None) -> dict:
        """
        Службові картки: свіжий стан рішення, документи рішення, картка,
        перелік. Уся робота з файлами — у модулі tenderwin_service_cards.
        """
        SC = service_cards_module()
        rows = self.service_card_rows(run_id=run_id, day=day)
        use_fresh = SERVICE_CARDS_FRESH if fresh is None else fresh
        client = tender_source if tender_source is not None else (
            Prozorro(verbose=False) if use_fresh else None)
        if doc_fetcher is None:
            session = L.make_session()
            widen_pool(session)
            doc_fetcher = SC.HttpDocFetcher(session=session)
        stats = SC.build_service_cards(
            rows, root=cases_root(), index_root=CARDS_DIR,
            run_label=label or run_id or self.run_id,
            tender_fetcher=(client.tender if (client is not None and use_fresh)
                            else None),
            doc_fetcher=doc_fetcher, verbose=self.verbose,
            lead_mail_version=VERSION)
        self.repo.audit("run", label or run_id or self.run_id, "SERVICE_CARDS_BUILT",
                        {"подій": stats.get("подій"), "карток": stats.get("карток"),
                         "текст": stats.get("текст_рішення"),
                         "помилок": len(stats.get("помилки") or [])})
        return stats

    # --- звіт ---------------------------------------------------------------
    def export(self) -> str:
        path = os.path.join(self.out_dir, f"zvit_{self.run_id}.csv")
        rows = self.db.q(
            "SELECT t.ua_id, c.edrpou_norm, c.company_name, e.event_time,"
            "       e.event_time_reliability, e.stage, e.state, e.state_reason,"
            "       e.lot_value, e.complaint_end,"
            "       (SELECT status FROM outreach o WHERE o.company_id = e.company_id"
            "          AND o.batch_id = ? LIMIT 1) letter_status,"
            "       (SELECT delivery_email_actual FROM outreach o"
            "         WHERE o.company_id = e.company_id AND o.batch_id = ? LIMIT 1) delivered_to"
            "  FROM events e"
            "  JOIN companies c ON c.company_id = e.company_id"
            "  JOIN tenders   t ON t.tender_id = e.tender_id"
            " WHERE e.run_id = ? ORDER BY e.event_time",
            (self.batch_id, self.batch_id, self.run_id))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["ID закупівлі", "ЄДРПОУ", "Компанія", "Час події",
                             "Надійність часу", "Стадія", "Стан ліда", "Причина",
                             "Вартість лота", "Строк оскарження до",
                             "Стан листа",
                             "Куди пішов у тесті" if MODE == "TEST" else "Куди пішов"])
            for row in rows:
                writer.writerow([row["ua_id"], row["edrpou_norm"], row["company_name"],
                                 (row["event_time"] or "").replace("T", " ")[:19],
                                 row["event_time_reliability"], row["stage"],
                                 row["state"], row["state_reason"] or "",
                                 row["lot_value"] or "",
                                 (row["complaint_end"] or "")[:19],
                                 row["letter_status"] or "", row["delivered_to"] or ""])
        return path

    def close(self) -> None:
        self.db.close()


# ============================================================================
#  БЛОК 12. ТОЧКИ ВХОДУ ДЛЯ COLAB
# ----------------------------------------------------------------------------
#  Людина не редагує код. Вона запускає чотири команди.
# ============================================================================
def _paths(dysk: str = "") -> None:
    """
    Ставить шляхи на Диск, щоб база і токен пережили сеанс Colab.

    Без аргументу шляхи лишаються ті, що є, але тека токена однаково має
    бути відома: рівно там, де база. Інакше M.send() без аргументу не
    знайшов би `.gmail_token.json`, який лежить поруч із базою, і сказав
    би «немає доступу до пошти», хоча доступ є.
    """
    global DB_PATH, OUT_DIR, LEGACY_DB_PATH, CARDS_DIR, WORK_DIR
    if dysk:
        DB_PATH = os.path.join(dysk, "tenderwin_lead_mail.db")
        OUT_DIR = os.path.join(dysk, "vyhid_lystiv")
        LEGACY_DB_PATH = os.path.join(dysk, "leads.db")
        CARDS_DIR = os.path.join(dysk, CARDS_DIRNAME)
        WORK_DIR = dysk
        E.TOKEN_DIR = dysk
        return
    if not E.TOKEN_DIR:
        E.TOKEN_DIR = os.path.dirname(os.path.abspath(DB_PATH)) or os.getcwd()


def cases_root() -> str:
    """
    Тека справ «0 Cases» у корені «Мого диска».

    Порядок пошуку: явний CASES_DIR → «Мій диск» серед батьківських тек
    робочої теки (у Colab це /content/drive/MyDrive) → відомі точки
    монтування (G:\\Мій диск на компʼютері) → «0 Cases» усередині робочої
    теки, якщо Диска не видно зовсім.
    """
    if CASES_DIR:
        return CASES_DIR
    start = os.path.abspath(WORK_DIR or os.path.dirname(os.path.abspath(DB_PATH))
                            or os.getcwd())
    path = start
    while True:
        if os.path.basename(path) in MY_DRIVE_NAMES:
            return os.path.join(path, CASES_DIRNAME)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    for root in MY_DRIVE_ROOTS:
        if os.path.isdir(root):
            return os.path.join(root, CASES_DIRNAME)
    return os.path.join(start, CASES_DIRNAME)


def gmail_secret_ready(dysk: str = "") -> tuple[bool, str]:
    """
    (чи є доступ до пошти, звідки він). Нічого не друкує і не зберігає.

    Порядок той самий, що в движку: змінна середовища -> Colab Secrets ->
    файл `.gmail_token.json` поруч із базою. Перевіряти це треба вже з
    відомою текою, інакше файл на Диску просто не видно.
    """
    _paths(dysk)
    if os.environ.get(E.GMAIL_OAUTH_ENV):
        return True, "змінна середовища"
    try:
        if E.ALLOW_COLAB_SECRETS:
            from google.colab import userdata                       # noqa: PLC0415
            if userdata.get(E.GMAIL_OAUTH_ENV):
                return True, "Colab Secrets"
    except Exception:                                               # noqa: BLE001
        pass
    if E.load_token():
        return True, f"файл {E.TOKEN_FILE_NAME} у теці {E.TOKEN_DIR}"
    return False, f"ні секрету {E.GMAIL_OAUTH_ENV}, ні файла {E.TOKEN_FILE_NAME}"


def _header(title: str) -> None:
    print("=" * 78)
    print(f"  TENDERWIN LEAD & MAIL {VERSION} від {BUILD} · {title}")
    print(f"  режим {MODE}" + (" — БОЙОВИЙ: листи клієнтам" if MODE == "LIVE" else "")
          + f" · шаблон листа {LETTER_VERSION}")
    print("=" * 78)


def setup(dysk: str = "", legacy_db: str = "") -> dict:
    """Один раз за сеанс: база, білий список, історія попереднього движка."""
    _paths(dysk)
    _header("підготовка")
    if MODE not in ("TEST", "LIVE"):
        raise ERR["MODE_UNKNOWN"](MODE)
    if MODE == "TEST" and not [a for a in TEST_RECIPIENTS if a.strip()]:
        raise ERR["TEST_ALLOWLIST_EMPTY"]()
    engine = LeadMail()
    try:
        stats = engine.prepare(legacy_db or LEGACY_DB_PATH)
        print(f"  база: {DB_PATH}")
        print(f"  вихід: {OUT_DIR}")
        if SERVICE_CARDS:
            print(f"  справи (картка + протокол): {cases_root()}")
        if MODE == "LIVE":
            print("  БОЙОВИЙ РЕЖИМ: листи складаються на адреси компаній із Prozorro.")
            print("  Надсилає M.send() (клітинка 7) — одразу, без запитань.")
            print("  Тестові листи бойового першого листа не займають.")
        else:
            print(f"  тестових адрес у білому списку: {stats['тестових_адрес']}")
        legacy = stats["перенесено_з_движка"]
        if isinstance(legacy, dict):
            if legacy.get("пропущено") == -1:
                print("  історія попереднього движка: бази не знайдено — "
                      "переношу нічого")
            else:
                print(f"  історія попереднього движка: компаній "
                      f"{legacy['компаній']}, звернень {legacy['звернень']}, "
                      f"стопів {legacy['стопів']}")
        else:
            print("  історія попереднього движка: вже перенесена раніше")
        print("\n  Далі: M.go() — сухий прогін за сьогодні.")
        return stats
    finally:
        engine.close()


def go(day: Optional[str] = None, dysk: str = "", verbose: bool = True) -> dict:
    """Сухий прогін: знайти сьогоднішні відхилення і скласти листи у файли."""
    _paths(dysk)
    _header("сухий прогін")
    engine = LeadMail(verbose=verbose)
    out: dict = {"run_id": engine.run_id, "batch_id": engine.batch_id}
    try:
        engine.prepare()
        window = build_window(engine.repo.previous_run_end(), day=day)
        engine.repo.start_run(engine.run_id, window)
        result = engine.scan(window)
        out["пошук"] = result.counters
        out["стан_обходу"] = result.state
        ingested = engine.ingest(result)
        out["записано"] = ingested
        out["скасовано_старих"] = engine.cancel_stale()
        out["листи"] = engine.build_letters()
        out["сухий_прогін"] = engine.preview()
        out["картки"] = engine.cards()
        out["звіт"] = engine.export()
        engine.repo.finish_run(engine.run_id, result.state, result.note)
        _print_report(engine, window, result, out)
        # Службові картки — після листів і звіту: їхня помилка чи обрив
        # не зачіпають ні листів, ні обліку прогону. Повторити: M.kartky().
        if SERVICE_CARDS:
            out["службові_картки"] = _service_cards_step(
                engine, run_id=engine.run_id, label=engine.run_id)
        return out
    finally:
        engine.close()


def kartky(day: Optional[str] = None, run: Optional[str] = None, dysk: str = "",
           fresh: Optional[bool] = None, verbose: bool = True) -> dict:
    """
    Службові картки ще раз — без пошуку і без листів.

      M.kartky(dysk=DYSK)                     події останнього прогону
      M.kartky(day="2026-10-02", dysk=DYSK)   усі події цього дня з бази
      M.kartky(run="R20261005-…", dysk=DYSK)  події конкретного прогону

    Уже завантажені файли вдруге не качаються; картку, яку ви правили,
    скрипт не перезаписує — нова версія ляже поруч.
    """
    _paths(dysk)
    _header("службові картки")
    if day:
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            print(f"  День «{day}» має бути у форматі РРРР-ММ-ДД, наприклад 2026-10-02.")
            return {"помилка": "BAD_DATE"}
    engine = LeadMail(verbose=verbose)
    try:
        if not day and not run:
            last = engine.db.one("SELECT run_id FROM runs WHERE finished_at IS NOT NULL"
                                 " ORDER BY finished_at DESC LIMIT 1")
            if last is None:
                print("  У базі ще немає завершеного прогону. Спершу M.go().")
                return {"помилка": "NO_RUNS"}
            run = last["run_id"]
        label = run or f"K{now():%Y%m%d-%H%M%S}_{day}"
        print(f"  Джерело подій: {'прогін ' + run if run else 'день ' + str(day)}")
        return _service_cards_step(engine, run_id=run, day=None if run else day,
                                   label=label, fresh=fresh)
    finally:
        engine.close()


def service_cards_module(reload: bool = False):
    """Модуль службових карток. Немає файла — листи працюють, картки ні."""
    try:
        import tenderwin_service_cards as SC                    # noqa: PLC0415
    except ImportError as exc:
        raise _err("SERVICE_CARDS_MODULE_MISSING",
                   f"Немає модуля службових карток ({exc})",
                   "Лише службові картки", True,
                   "Покладіть tenderwin_service_cards.py у теку TenderWin поруч з "
                   "іншими файлами (клітинка 2) і виконайте M.kartky(dysk=DYSK). "
                   "Листи й робочі картки цим не зачеплені.") from exc
    if reload:
        import importlib                                         # noqa: PLC0415
        SC = importlib.reload(SC)
    return SC


def _service_cards_step(engine: "LeadMail", *, run_id: Optional[str] = None,
                        day: Optional[str] = None, label: str = "",
                        fresh: Optional[bool] = None) -> dict:
    """Крок службових карток із власним підсумком. Падіння кроку — це
    повідомлення з кодом, а не обірваний прогін."""
    print("\n" + "-" * 78)
    print("  СЛУЖБОВІ КАРТКИ: протоколи рішень і перелік для відбору")
    print("-" * 78)
    try:
        stats = engine.service_cards(run_id=run_id, day=day, label=label, fresh=fresh)
    except MailError as err:
        print(err.render())
        return {"помилка": err.code}
    except Exception as exc:                                    # noqa: BLE001
        # Межа кроку: листи, робочі картки і звіт уже збережені.
        print(f"\n  КОД: SERVICE_CARDS_FAILED\n  ЩО СТАЛОСЬ: {type(exc).__name__}: {exc}"
              f"\n  ЩО ЗАЧЕПЛЕНО: лише службові картки; листи, робочі картки і звіт"
              f" уже збережено\n  ЧИ МОЖНА ДАЛІ: так\n  ЩО РОБИТИ: M.kartky(dysk=DYSK)"
              f" повторить цей крок; уже завантажене вдруге не качається\n")
        return {"помилка": "SERVICE_CARDS_FAILED",
                "деталі": f"{type(exc).__name__}: {exc}"}
    SC = service_cards_module()
    for line in SC.summary_lines(stats):
        print(f"  {line}")
    return stats


def send(dysk: str = "", max_sends: Optional[int] = None, paced: bool = True,
         batch: str = "", force: bool = False) -> dict:
    """
    Відправка складених листів. TEST — на ВАШІ тестові скриньки; LIVE —
    клієнтам, на адреси компаній із Prozorro (рішення власника 08.10.2026:
    без додаткового підтвердження).
    """
    _paths(dysk)
    _header("відправка КЛІЄНТАМ" if MODE == "LIVE" else "відправка на тестові скриньки")
    engine = LeadMail()
    try:
        row = engine.db.one(
            "SELECT batch_id, run_id, MAX(generated_at) last FROM outreach"
            " WHERE status = 'QUEUED' AND mode = ?"
            + (" AND batch_id = ?" if batch else "")
            + " GROUP BY batch_id ORDER BY last DESC LIMIT 1",
            (MODE, batch) if batch else (MODE,))
        if row is None:
            # Черга порожня, але з минулого разу могли лишитися листи без
            # певного стану (обрив під час відправки). Звірити їх треба саме
            # тут: іншого шляху до Gmail у скрипта немає.
            підсумок = {"надіслано": 0, "причина": "порожня черга"}
            зависли = engine.repo.unresolved()
            if зависли:
                print(f"  У черзі немає листів, але {len(зависли)} лист(и) "
                      f"з попереднього разу без певного стану — звіряю з Gmail.")
                підсумок["звірка"] = engine.reconcile()
                print(f"  {підсумок['звірка']}")
            else:
                print("  У черзі немає листів. Спершу зробіть M.go().")
            return підсумок
        day_of_batch = (row["last"] or "")[:10]
        if day_of_batch != now().strftime("%Y-%m-%d") and not force:
            print(f"  У черзі листи від {day_of_batch}, а сьогодні "
                  f"{now():%Y-%m-%d}. Вони стосуються подій іншого дня.")
            print("  Зробіть M.go() заново — або, якщо свідомо хочете "
                  "надіслати старі, M.send(force=True).")
            return {"надіслано": 0, "причина": "партія іншого дня"}
        engine.batch_id, engine.run_id = row["batch_id"], row["run_id"]
        stats = engine.send(max_sends=max_sends, paced=paced)
        stats["звірка"] = engine.reconcile()
        print(f"\n  Підсумок: {stats}")
        if MODE == "LIVE":
            лишилось = engine.db.one(
                "SELECT COUNT(*) n FROM outreach WHERE status = 'QUEUED'"
                " AND mode = 'LIVE' AND batch_id = ?", (row["batch_id"],))["n"]
            print("  Бойові листи пішли на адреси компаній. Цим компаніям скрипт "
                  "першого листа більше не надішле.")
            if лишилось:
                print(f"  У черзі цієї партії ще {лишилось} — виконайте M.send() ще раз.")
        else:
            print(f"  Листи пішли лише на адреси зі списку TEST_RECIPIENTS.")
        return stats
    finally:
        engine.close()


def state(dysk: str = "") -> dict:
    """Що зараз у базі. Нічого не змінює."""
    _paths(dysk)
    _header("стан бази")
    engine = LeadMail()
    try:
        out = {
            "події": {r["state"]: r["n"] for r in engine.db.q(
                "SELECT state, COUNT(*) n FROM events GROUP BY state ORDER BY n DESC")},
            "листи": {r["status"]: r["n"] for r in engine.db.q(
                "SELECT status, COUNT(*) n FROM outreach GROUP BY status ORDER BY n DESC")},
            "компаній": engine.db.one("SELECT COUNT(*) n FROM companies")["n"],
            "стоп-лист": engine.db.one("SELECT COUNT(*) n FROM suppressions")["n"],
            "незакриті прогалини сканування": engine.db.one(
                "SELECT COUNT(*) n FROM scan_gaps WHERE resolved = 0")["n"],
            "листи без певного стану": len(engine.repo.unresolved()),
        }
        last = engine.db.one("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
        if last:
            out["останній прогін"] = {
                "коли": (last["started_at"] or "").replace("T", " ")[:19],
                "вікно": f"{(last['window_start'] or '')[:16]} — "
                         f"{(last['window_end'] or '')[:16]}",
                "обхід": last["scan_state"] or "—",
                "примітка": last["scan_note"] or "",
            }
        for key, value in out.items():
            print(f"  {key}: {value}")
        if out["листи без певного стану"]:
            print("  ⓘ Ці листи не будуть надіслані повторно наосліп. "
                  "Зробіть M.send() — він звірить їх із Gmail.")
        return out
    finally:
        engine.close()


def _print_report(engine: LeadMail, window: Window, result: ScanResult,
                  out: dict) -> None:
    counters = result.counters
    letters = out["листи"]
    print("\n" + "-" * 78)
    print(f"  ПІДСУМОК ПРОГОНУ {engine.run_id}")
    print("-" * 78)
    print(f"  Вікно: {window.human()}")
    print(f"  Обхід стрічки: {result.state}"
          + (f" — {result.note}" if result.note else ""))
    print(f"  Закупівель перевірено: {counters['закупівель_перевірено']:,}"
          .replace(",", " "))
    if counters.get("пропущено_за_процедурою"):
        print(f"  Пропущено за типом процедури: "
              f"{counters['пропущено_за_процедурою']:,}".replace(",", " "))
        for тип, скільки in sorted(result.skipped_types.items(),
                                   key=lambda пара: -пара[1])[:6]:
            підпис = KNOWN_SKIPPED_METHOD_TYPES.get(тип, "не в переліку дозволених")
            print(f"     {тип}: {скільки:,} — {підпис}".replace(",", " "))
        print("     ⓘ Рішення власника: відхилення в цих процедурах не "
              "оскаржуються в АМКУ, тому листів по них немає.")
    print(f"  Відхилень у вікні: {counters['у_вікні']} "
          f"(усього побачено {counters['подій_усього']})")
    print(f"     старіші за вікно: {counters['старіші']}")
    if counters.get("хвіст_попередньої_доби"):
        print(f"     ! з’явилися після попереднього прогону, але вчора: "
              f"{counters['хвіст_попередньої_доби']} — за нинішнім "
              f"налаштуванням WINDOW_MODE='TODAY_ONLY' вони не потраплять "
              f"у розсилку ніколи")
    print(f"     ненадійний час події: {counters['ненадійний_час']} "
          f"(на ручний перегляд)")
    print(f"     без ЄДРПОУ/ІПН: {counters['без_ЄДРПОУ']}")
    print(f"  Записано подій: нових {out['записано']['нових_подій']}, "
          f"вже були {out['записано']['уже_були']}")
    if out["записано"].get("повернуто_з_тесту"):
        print(f"     з них раніше бачив лише тестовий прогін: "
              f"{out['записано']['повернуто_з_тесту']} — для бойової розсилки вони нові")
    print(f"  Листів складено: {letters['листів']}")
    ПОЯСНЕННЯ = {
        "NOT_ELIGIBLE": "компанія вже отримувала перший лист (або поріг вартості)",
        "SUPPRESSED": "стоп-лист",
        "REVIEW": "немає e-mail, e-mail із помилкою або ненадійні дані — на ваш перегляд",
        "NO_COMPLAINT_ROUTE": "у даних немає періоду оскарження",
    }
    for key in ("NOT_ELIGIBLE", "SUPPRESSED", "REVIEW", "NO_COMPLAINT_ROUTE"):
        if letters.get(key):
            print(f"     {key}: {letters[key]} — {ПОЯСНЕННЯ[key]}")
    if letters.get("NO_COMPLAINT_ROUTE"):
        print("       ⓘ Ці ліди можна включити в розсилку, поставивши "
              "SKIP_WITHOUT_COMPLAINT_ROUTE = False. Рішення за вами (П-2).")
    if out["скасовано_старих"]:
        print(f"  Скасовано листів попередніх партій: {out['скасовано_старих']}")
    оформлених = out["сухий_прогін"].get("з_оформленням", 0)
    print(f"  Файли листів: {out['сухий_прогін']['тека']}")
    if оформлених:
        print(f"     поруч із кожним листом лежить .html — відкрийте у браузері, "
              f"щоб побачити його очима клієнта ({оформлених} шт.)")
    картки = out["картки"]
    print(f"  Картки закупівель: {картки['карток']} нових"
          + (f", {картки['уже_були']} вже були" if картки.get("уже_були") else "")
          + f" — у {картки['тека']}")
    print(f"  Звіт: {out['звіт']}")
    if ALLOW_MEDIUM_RELIABILITY:
        print("\n  ⓘ Події з єдиним джерелом дати (MEDIUM) включені. Це "
              "потрібно підтвердити на реальних даних (тест Т-15 майстер-промпту).")
    if SERVICE_CARDS:
        print("  ⓘ Службові картки з протоколами рішень — наступним кроком, нижче.")
    else:
        print("  ⓘ Службові картки вимкнено (SERVICE_CARDS = False): документи "
              "рішень не завантажувались.")
    if MODE == "LIVE":
        print("\n  БОЙОВИЙ РЕЖИМ: листи складено на адреси КЛІЄНТІВ (поки лише у файли).")
        print("  Далі: прочитайте кілька листів у теці eml, потім M.send() —")
        print("  листи підуть клієнтам одразу.")
    else:
        print(f"\n  Далі: прочитайте кілька листів у теці eml, потім M.send().")


def tests(folder: str = "") -> None:
    """Прогін тестів цього скрипта і модуля службових карток."""
    import subprocess, sys                                      # noqa: PLC0415
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    for name in ("test_lead_mail.py", "test_service_cards.py", "test_0_cases.py"):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print(f"  Не знайдено {path}")
            continue
        result = subprocess.run([sys.executable, path], capture_output=True,
                                text=True, cwd=folder)
        output = (result.stderr or result.stdout).strip()
        if result.returncode == 0:
            print(f"  {name}: " + " · ".join(output.splitlines()[-3:]))
        else:
            print(f"  {name}: Є ПОМИЛКИ\n{output}")
