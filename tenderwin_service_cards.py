# -*- coding: utf-8 -*-
"""
=============================================================================
 TENDERWIN SERVICE CARDS v1.0.0 · СЛУЖБОВІ КАРТКИ ВІДХИЛЕНЬ І ПРОТОКОЛИ
=============================================================================

 ЩО ЦЕ

 Модуль генератора Lead & Mail (з версії 1.3.0). Рішення власника 04.10.2026:
 прогін має давати службову картку і протокол відхилення по кожній події,
 щоб з них можна було відібрати справи для аналізу.

 Для кожної події відхилення модуль:

   1. бере свіжий стан рішення з Prozorro: документи, статус, скарги;
   2. завантажує ДОКУМЕНТИ САМОГО РІШЕННЯ замовника (протокол, повідомлення
      про невідповідності) — і більше нічого;
   3. витягує з них текст посторінково, без OCR; сторінки без текстового
      шару називає поіменно;
   4. кладе в теку події файли, manifest.json і «Службову картку
      відхилення» (.docx — відкривається просто на Google Диску);
   5. складає перелік усіх подій (.xlsx) — з нього відбирають справи.

 ГАРАНТІЇ

   * Документи пропозиції і тендерна документація не завантажуються.
   * Жоден файл не перезаписується. Нова редакція документа — новий файл.
   * Документ, який не вдалося отримати чи прочитати, лишається в картці
     з кодом причини. «Не завантажилось» не стає «документа немає».
   * Картку, яку правила людина, скрипт не перезаписує: нова версія
     лягає поруч окремим файлом.
   * Текст у картці — дослівний витяг із файлу. Скрипт нічого не
     тлумачить і не класифікує.

 ЧОГО ТУТ НЕМАЄ СВІДОМО

   документів пропозиції і ТД · OCR · класифікації підстав · будь-якого AI ·
   оцінки перспективи. Картка — витяг фактів, а не аналіз.

 ЯК ВИКЛИКАЄТЬСЯ

   Модуль сам нічого не шукає і не знає про базу Lead & Mail. Події йому
   передає `tenderwin_lead_mail` (крок у M.go() і команда M.kartky()).
   Мережу йому теж передають: функцію, що читає закупівлю, і функцію, що
   завантажує документ. Так кожен крок перевіряється тестами без мережі.
=============================================================================
"""
from __future__ import annotations

import csv
import hashlib
import html as _html
import io
import json
import os
import re
import tempfile
import time
import traceback
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

VERSION = "1.0.0"
BUILD = "2026-10-04"
MANIFEST_SCHEMA = "tenderwin.service_card/1"
KYIV = ZoneInfo("Europe/Kyiv")

# ============================================================================
#  БЛОК 0. НАЛАШТУВАННЯ
# ============================================================================
#: Більший файл не завантажуємо: протокол рідко важить більше кількох МБ.
MAX_DOC_MB = 25
#: Скільки документів одного рішення завантажувати. Решта лишається в
#: картці зі станом SKIPPED_LIMIT — видно, а не зникає.
MAX_DOCS_PER_EVENT = 30
HTTP_TRIES = 3
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 90
#: Паралельні потоки: події обробляються одночасно, по одній на потік.
WORKERS = 6
FRESH_WORKERS = 8
#: Без даних про зображення (резервна бібліотека pypdf) сторінка, на якій
#: менше значущих символів, вважається сторінкою без текстового шару.
MIN_PAGE_CHARS = 25
#: Сторінка з великим зображенням (частка площі сторінки) і малою кількістю
#: тексту — це скан із накладеним штампом підпису, а не текстова сторінка.
SCAN_IMAGE_SHARE = 0.5
SCAN_TEXT_CHARS = 200
#: Межа сторінок PDF для витягу тексту. Необроблені сторінки картка називає.
MAX_PDF_PAGES = 300
#: DOCX — це ZIP. Захист від «бомби»: розпакований вміст не більше цього.
MAX_DOCX_UNZIPPED_MB = 200
#: Скільки символів тексту одного документа показує картка. Повний текст
#: лишається в manifest.json і у самому файлі; обрізання завжди підписане.
CARD_TEXT_LIMIT = 150_000
#: Довжина дослівних фрагментів у переліку.
INDEX_TEXT_LIMIT = 700
#: Документ, опублікований у межах цього часу від події, позначається
#: «у момент рішення».
SAME_MOMENT_MINUTES = 15
#: Сліпий відбір справ (рішення власника 04.10.2026: аналіз не привʼязувати
#: до рішень АМКУ). Картка і перелік показують, що скарга є і на якій вона
#: стадії, але не кажуть, чим скінчився розгляд. Повні дані лишаються в
#: manifest.json — для звірки прогнозу пізніше. False — показувати все.
BLIND_COMPLAINT_OUTCOMES = True
#: Межа довжини імені файла в байтах UTF-8 (кирилиця — 2 байти на літеру;
#: файлова система дозволяє 255).
MAX_NAME_BYTES = 150

MANIFEST_NAME = "manifest.json"
CARD_PREFIX = "Службова_картка_"
INDEX_PREFIX = "_ПЕРЕЛІК_"
NOT_ESTABLISHED = "НЕ ВСТАНОВЛЕНО"
PROZORRO_TENDER_URL = "https://prozorro.gov.ua/tender/{ua_id}"

#: Службові файли рішення: підписи, витяги ЄДР від бота, машинні формати.
#: Вони не є текстом рішення і не завантажуються, але лишаються в картці.
SERVICE_SUFFIXES = (".p7s", ".p7m", ".sig", ".asice", ".asics", ".yaml",
                    ".yml", ".json", ".xml")
SERVICE_PREFIXES = ("edr_", "edr-")
SERVICE_DOC_TYPES = {"registerExtract", "registerUSR"}
#: Хто завантажив документ. Документи рішення завантажує замовник
#: (tender_owner); витяги ЄДР — бот (bots). Документ будь-якого іншого автора
#: (скаржник, орган оскарження) документом рішення не є і не качається:
#: інакше рішення АМКУ потрапило б у картку як «протокол».
DECISION_AUTHORS = {"tender_owner"}
SERVICE_AUTHORS = {"bots"}
ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")

# ============================================================================
#  БЛОК 1. СТАНИ ДОКУМЕНТА
# ----------------------------------------------------------------------------
#  Коди — англійською (правило 23), пояснення — українською для картки.
# ============================================================================
ST_PROCESSED = "PROCESSED"
ST_PARTIAL = "PARTIAL_TEXT"
ST_OCR = "OCR_REQUIRED"
ST_EMPTY_TEXT = "EMPTY_TEXT"
ST_UNSUPPORTED = "UNSUPPORTED_FORMAT"
ST_ENCRYPTED = "ENCRYPTED"
ST_CORRUPTED = "CORRUPTED"
ST_EXTRACTION_FAILED = "EXTRACTION_FAILED"
ST_SERVICE = "SKIPPED_SERVICE_FILE"
ST_ARCHIVE = "SKIPPED_ARCHIVE"
ST_LIMIT = "SKIPPED_LIMIT"
ST_NOT_REQUESTED = "NOT_REQUESTED"
ST_NO_URL = "NO_URL"
ST_TOO_LARGE = "TOO_LARGE"
ST_EMPTY_FILE = "EMPTY_FILE"
ST_HTML = "HTML_NOT_DOCUMENT"
ST_WRITE_FAILED = "WRITE_FAILED"
ST_OTHER_AUTHOR = "SKIPPED_OTHER_AUTHOR"

#: Стани, у яких файл лежить у теці і його можна взяти повторно.
SAVED_STATES = {ST_PROCESSED, ST_PARTIAL, ST_OCR, ST_EMPTY_TEXT,
                ST_UNSUPPORTED, ST_ENCRYPTED, ST_CORRUPTED,
                ST_EXTRACTION_FAILED}
#: Стани, у яких є текст.
TEXT_STATES = {ST_PROCESSED, ST_PARTIAL}

STATUS_UA = {
    ST_PROCESSED: "завантажено, текст прочитано",
    ST_PARTIAL: "завантажено, текст прочитано частково",
    ST_OCR: "завантажено; текстового шару немає (скан)",
    ST_EMPTY_TEXT: "завантажено; тексту в файлі немає",
    ST_UNSUPPORTED: "завантажено; формат не читається автоматично",
    ST_ENCRYPTED: "завантажено; файл захищено паролем",
    ST_CORRUPTED: "завантажено; файл пошкоджений",
    ST_EXTRACTION_FAILED: "завантажено; текст не витягнуто",
    ST_SERVICE: "службовий файл (підпис, витяг ЄДР) — не завантажувався",
    ST_ARCHIVE: "архів — не завантажувався",
    ST_LIMIT: "не завантажувався: перевищено ліміт документів",
    ST_NOT_REQUESTED: "не завантажувався: завантаження вимкнене",
    ST_NO_URL: "у Prozorro немає посилання на файл",
    ST_TOO_LARGE: f"не завантажено: більше {MAX_DOC_MB} МБ",
    ST_EMPTY_FILE: "сервер віддав порожній файл",
    ST_HTML: "замість документа сервер віддав вебсторінку",
    ST_WRITE_FAILED: "не вдалося записати файл на Диск",
    ST_OTHER_AUTHOR: "документ не замовника — не завантажувався",
    "NETWORK_TIMEOUT": "не завантажено: сервер не відповів вчасно",
    "NETWORK_ERROR": "не завантажено: помилка мережі",
    "HTTP_403": "не завантажено: доступ заборонено (403)",
    "HTTP_404": "не завантажено: файл не знайдено (404)",
    "HTTP_410": "не завантажено: файл видалено (410)",
    "HTTP_429": "не завантажено: сервер обмежив запити (429)",
    "HTTP_5XX": "не завантажено: помилка сервера (5xx)",
}

COMPLAINT_TYPE_UA = {"complaint": "скарга до АМКУ", "claim": "вимога"}
COMPLAINT_STATUS_UA = {
    "draft": "чернетка", "pending": "очікує розгляду",
    "accepted": "прийнята до розгляду", "satisfied": "задоволена",
    "declined": "відхилена", "invalid": "залишена без розгляду",
    "stopping": "відкликається", "stopped": "розгляд припинено",
    "mistaken": "помилкова", "resolved": "вирішена",
    "answered": "надано відповідь", "cancelled": "скасована",
    "ignored": "без відповіді",
}

LEAD_STATE_UA = {
    "IN_LETTER": "лист складено",
    "NOT_ELIGIBLE": "лист не складається",
    "NO_COMPLAINT_ROUTE": "у Prozorro немає періоду оскарження — лист не складається",
    "REVIEW": "на ручний перегляд",
    "SUPPRESSED": "компанія у стоп-листі",
    "NEW": "новий",
}

#: Стан тексту рішення для переліку і картки.
PROTO_TEXT = "ТЕКСТ Є"
PROTO_PARTIAL = "ТЕКСТ Є ЧАСТКОВО"
PROTO_SCAN = "СКАН — ТЕКСТУ НЕМАЄ"
PROTO_FAILED = "НЕ ЗАВАНТАЖЕНО"
PROTO_NONE = "ДОКУМЕНТІВ У РІШЕННІ НЕМАЄ"
PROTO_UNKNOWN = NOT_ESTABLISHED
PROTO_UNREADABLE = "ФАЙЛ Є, ТЕКСТ НЕ ВИТЯГНУТО"
PROTO_ONLY_SERVICE = "ЛИШЕ ПІДПИСИ/АРХІВИ — ВІДКРИТИ ВРУЧНУ"


# ============================================================================
#  БЛОК 2. ЧАС, НАЗВИ ФАЙЛІВ, ДРІБНІ ПЕРЕТВОРЕННЯ
# ============================================================================
def now_kyiv() -> datetime:
    return datetime.now(KYIV)


def parse_dt(value: Any, assume_kyiv: bool = False) -> Optional[datetime]:
    """
    Час із Prozorro. Без часового поясу значення не приймається — крім
    старих записів бази, де дату обрізали до секунд: там це київський час,
    і на це треба явно погодитися параметром `assume_kyiv`.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else (value.replace(tzinfo=KYIV)
                                           if assume_kyiv else None)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed
    return parsed.replace(tzinfo=KYIV) if assume_kyiv else None


def fmt_dt(moment: Optional[datetime]) -> str:
    """«05.10.2026 14:37» за Києвом або порожньо."""
    if not moment:
        return ""
    return moment.astimezone(KYIV).strftime("%d.%m.%Y %H:%M")


def plural_ua(number: int, one: str, few: str, many: str) -> str:
    """1 доба · 2 доби · 5 діб."""
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def human_span(seconds: float) -> str:
    """«2 год 15 хв», «3 доби 4 год», «40 хв»."""
    seconds = int(abs(seconds))
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days} {plural_ua(days, 'доба', 'доби', 'діб')}")
    if hours:
        parts.append(f"{hours} год")
    if minutes and not days:
        parts.append(f"{minutes} хв")
    return " ".join(parts) or "менше хвилини"


def relative_to_event(doc_moment: Optional[datetime],
                      event_moment: Optional[datetime]) -> str:
    """Коли документ зʼявився відносно рішення. Лише факт, без здогадок."""
    if not doc_moment or not event_moment:
        return ""
    delta = (doc_moment.astimezone(timezone.utc)
             - event_moment.astimezone(timezone.utc)).total_seconds()
    if abs(delta) <= SAME_MOMENT_MINUTES * 60:
        return "у момент рішення"
    if delta < 0:
        return f"за {human_span(delta)} до рішення"
    return f"через {human_span(delta)} після рішення"


_UNSAFE_NAME = re.compile(r"[^\w\s.\-()№]+", flags=re.UNICODE)


def safe_name(text: str, limit: int = 90) -> str:
    """Імʼя файла для Диска: без шляхів, двокрапок і лапок, не довше
    MAX_NAME_BYTES байтів."""
    clean = _UNSAFE_NAME.sub("_", str(text or "").strip())
    clean = re.sub(r"\s+", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("._ ")
    clean = (clean or "dokument")[:limit]
    while len(clean.encode("utf-8")) > MAX_NAME_BYTES:
        clean = clean[:-1]
    return clean


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")


def xml_safe(text: Any) -> str:
    """
    Текст, який приймуть Word і Excel. Витяг із PDF часто містить керівні
    символи: python-docx на них падає, і через один символ картки б не було.
    """
    value = str(text if text is not None else "")
    value = value.encode("utf-8", "replace").decode("utf-8", "replace")
    return _XML_BAD.sub(" ", value)


def one_line(text: str, limit: int = 0) -> str:
    """Стиснути пробіли; обрізати з явною позначкою «…»."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit and len(clean) > limit:
        return clean[:limit - 1].rstrip() + "…"
    return clean


def money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NOT_ESTABLISHED
    if not number:
        return NOT_ESTABLISHED
    return f"{number:,.0f} грн".replace(",", " ")


def http_code(status: int) -> str:
    if status in (403, 404, 410, 429):
        return f"HTTP_{status}"
    if 500 <= status < 600:
        return "HTTP_5XX"
    return f"HTTP_{status}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def case_folder_name(row: dict) -> str:
    """
    Тека події: ID закупівлі + код учасника + початок ID рішення.

    Саме ідентифікатори, а не назви (правило 15): в одній закупівлі ту саму
    компанію можна відхилити двічі — у двох лотах, двома рішеннями.
    """
    name = (f"{row.get('ua_id') or 'bez-UA'}__{row.get('edrpou_norm') or 'bez-kodu'}"
            f"__{str(row.get('object_id') or 'bez-id')[:8]}")
    return re.sub(r"[^\w.\-]", "_", name)


def event_day(row: dict) -> str:
    moment = parse_dt(row.get("event_time"), assume_kyiv=True)
    return moment.astimezone(KYIV).date().isoformat() if moment else "bez-daty"


# ============================================================================
#  БЛОК 3. ДОКУМЕНТИ РІШЕННЯ: ПЕРЕЛІК І РЕДАКЦІЇ
# ============================================================================
@dataclass
class DocRecord:
    """Один документ рішення — як він є в Prozorro і що з ним сталося в нас."""
    seq: int
    document_id: str
    title: str
    url: str
    format: str = ""
    document_type: str = ""
    author: str = ""
    declared_hash: str = ""
    date_published: str = ""
    date_modified: str = ""
    is_latest: bool = True
    status: str = ""
    detail: str = ""
    real_type: str = ""
    file: str = ""
    sha256: str = ""
    md5_check: str = ""                 # OK / MISMATCH / NOT_DECLARED
    bytes: int = 0
    pages_total: int = 0
    pages_with_text: list = field(default_factory=list)
    pages_without_text: list = field(default_factory=list)
    pages_not_processed: list = field(default_factory=list)
    text: str = ""
    text_chars: int = 0
    text_engine: str = ""
    extractor: str = ""                 # версія модуля/рушія, що читав текст
    retrieved_at: str = ""
    reused: bool = False

    def identity(self) -> str:
        """Ключ редакції: той самий документ, та сама зміна, те саме посилання."""
        base = self.document_id or f"{self.title}|{self.date_published}"
        return f"{base}|{self.date_modified}|{self.url}"


#: «Протокол.pdf.p7s» — підписаний контейнер, у якому може лежати сам
#: протокол. Такий файл завантажуємо: на відміну від «sign.p7s», це не
#: окремий підпис, а можливо єдина копія рішення.
_SIGNED_DOCUMENT = re.compile(r"\.(pdf|docx?|odt|rtf|xlsx?|txt)\.(p7s|p7m)$")


def is_service_file(title: str, fmt: str = "", doc_type: str = "") -> bool:
    low = str(title or "").strip().lower()
    if doc_type in SERVICE_DOC_TYPES:
        return True
    if _SIGNED_DOCUMENT.search(low):
        return False
    if low.startswith(SERVICE_PREFIXES) or low.endswith(SERVICE_SUFFIXES):
        return True
    return "pkcs7" in str(fmt or "").lower()


def is_archive(title: str, fmt: str = "") -> bool:
    low = str(title or "").strip().lower()
    return low.endswith(ARCHIVE_SUFFIXES) or any(
        x in str(fmt or "").lower() for x in ("zip", "x-rar", "x-7z"))


def docs_from_api(documents: Any) -> list:
    """Документи обʼєкта рішення з Prozorro — у хронологічному порядку."""
    items = []
    for index, raw in enumerate(documents or []):
        if not isinstance(raw, dict):
            continue
        items.append((index, raw))
    items.sort(key=lambda pair: (str(pair[1].get("datePublished") or ""), pair[0]))
    records = []
    for seq, (_, raw) in enumerate(items, 1):
        records.append(DocRecord(
            seq=seq, document_id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""), url=str(raw.get("url") or ""),
            format=str(raw.get("format") or ""),
            document_type=str(raw.get("documentType") or ""),
            author=str(raw.get("author") or ""),
            declared_hash=str(raw.get("hash") or ""),
            date_published=str(raw.get("datePublished") or ""),
            date_modified=str(raw.get("dateModified") or "")))
    mark_latest(records)
    return records


def docs_from_stored(stored: Any) -> list:
    """
    Документи з бази Lead & Mail (якщо свіжого стану немає).

    Старі записи (до 1.3.0) мають дату, обрізану до секунд, і не мають id —
    тоді ключем редакції стає посилання, а дата читається як київська.
    """
    if isinstance(stored, str):
        try:
            stored = json.loads(stored or "[]")
        except ValueError:
            stored = []
    raw_items = []
    for item in stored or []:
        if not isinstance(item, dict):
            continue
        raw_items.append({
            "id": item.get("id") or "",
            "title": item.get("назва") or item.get("title") or "",
            "url": item.get("посилання") or item.get("url") or "",
            "format": item.get("формат") or item.get("format") or "",
            "documentType": item.get("тип") or item.get("documentType") or "",
            "hash": item.get("hash") or "",
            "datePublished": item.get("published_iso") or item.get("опубліковано") or "",
            "dateModified": item.get("modified_iso") or item.get("змінено") or "",
        })
    return docs_from_api(raw_items)


def mark_latest(records: list) -> None:
    """Для кожного id остання редакція — за dateModified, далі за порядком."""
    best: dict = {}
    for record in records:
        key = record.document_id or f"url:{record.url}"
        current = best.get(key)
        if current is None or (record.date_modified, record.seq) >= (
                current.date_modified, current.seq):
            best[key] = record
    for record in records:
        key = record.document_id or f"url:{record.url}"
        record.is_latest = best.get(key) is record


# ============================================================================
#  БЛОК 4. ЗАВАНТАЖЕННЯ: КОД ПОМИЛКИ ЗАМІСТЬ «НЕ ВДАЛОСЯ»
# ============================================================================
@dataclass
class Fetched:
    data: bytes = b""
    error: str = ""
    http_status: int = 0
    content_type: str = ""
    detail: str = ""


def default_session(user_agent: str = ""):
    """Сесія requests зі збільшеним пулом зʼєднань для паралельної роботи."""
    import requests                                               # noqa: PLC0415
    from requests.adapters import HTTPAdapter                     # noqa: PLC0415
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent or
                            f"TenderWin-ServiceCards/{VERSION}"})
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class HttpDocFetcher:
    """
    Завантаження одного файла за посиланням Prozorro.

    Повтор лише там, де він має сенс: 429, 5xx, таймаут, обрив. 403/404/410
    не повторюються — повтор нічого не змінить. Розмір обмежений і до, і під
    час завантаження: сервер може не назвати розмір заздалегідь.
    """

    def __init__(self, session=None, max_bytes: Optional[int] = None,
                 tries: Optional[int] = None, sleep: Callable = time.sleep):
        self.session = session or default_session()
        self.max_bytes = max_bytes or MAX_DOC_MB * 1024 * 1024
        self.tries = tries or HTTP_TRIES
        self.sleep = sleep

    def __call__(self, url: str) -> Fetched:
        import requests                                           # noqa: PLC0415
        last, detail = "NETWORK_ERROR", ""
        for attempt in range(self.tries):
            if attempt:
                self.sleep(min(2 + 3 * attempt, 15))
            try:
                response = self.session.get(
                    url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True,
                    headers={"Accept": "*/*"})
            except requests.exceptions.Timeout:
                last, detail = "NETWORK_TIMEOUT", "таймаут зʼєднання"
                continue
            except requests.exceptions.RequestException as exc:
                last, detail = "NETWORK_ERROR", type(exc).__name__
                continue
            try:
                status = response.status_code
                if status == 200:
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared and declared > self.max_bytes:
                        return Fetched(error=ST_TOO_LARGE, http_status=200,
                                       detail=f"{declared:,} байт".replace(",", " "))
                    chunks, total = [], 0
                    for chunk in response.iter_content(chunk_size=262_144):
                        total += len(chunk)
                        if total > self.max_bytes:
                            return Fetched(error=ST_TOO_LARGE, http_status=200,
                                           detail=f"понад {MAX_DOC_MB} МБ")
                        chunks.append(chunk)
                    return Fetched(data=b"".join(chunks), http_status=200,
                                   content_type=response.headers.get("Content-Type", ""))
                last, detail = http_code(status), f"HTTP {status}"
                if status == 429 or 500 <= status < 600:
                    retry_after = str(response.headers.get("Retry-After") or "")
                    if retry_after.isdigit():
                        self.sleep(min(int(retry_after), 30))
                    continue
                return Fetched(error=last, http_status=status, detail=detail)
            except requests.exceptions.Timeout:
                last, detail = "NETWORK_TIMEOUT", "таймаут під час читання"
            except requests.exceptions.RequestException as exc:
                last, detail = "NETWORK_ERROR", type(exc).__name__
            finally:
                response.close()
        return Fetched(error=last, detail=detail)


# ============================================================================
#  БЛОК 5. ТИП ФАЙЛА І ТЕКСТ — ПОСТОРІНКОВО, БЕЗ OCR
# ----------------------------------------------------------------------------
#  Тип визначається за вмістом, а не за назвою: «Протокол.pdf» цілком може
#  бути сторінкою помилки або підписаним контейнером.
# ============================================================================
REAL_EXT = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "odt": ".odt",
            "zip": ".zip", "ole": ".doc", "rtf": ".rtf", "html": ".html",
            "text": ".txt", "jpeg": ".jpg", "png": ".png", "tiff": ".tif",
            "p7s": ".p7s", "der": ".bin", "binary": ".bin"}


def sniff_type(data: bytes, declared: str = "", title: str = "") -> str:
    if not data:
        return "empty"
    if data[:5] == b"%PDF-" or b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                mimetype = (archive.read("mimetype")[:80]
                            if "mimetype" in names else b"")
        except (zipfile.BadZipFile, KeyError, OSError):
            return "corrupted"
        if "word/document.xml" in names:
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if b"opendocument.text" in mimetype:
            return "odt"
        return "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    if data[:5] == b"{\\rtf":
        return "rtf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    head = data[:2048].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") \
            or b"<html" in head[:512]:
        return "html"
    if data[:1] == b"\x30" and ("pkcs7" in declared.lower()
                                or title.lower().endswith(".p7s")):
        return "p7s"
    try:
        data[:4096].decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        pass
    if data[:1] == b"\x30":
        return "der"
    return "binary"


@dataclass
class TextResult:
    status: str
    detail: str = ""
    pages_total: int = 0
    pages_with_text: list = field(default_factory=list)
    pages_without_text: list = field(default_factory=list)
    pages_not_processed: list = field(default_factory=list)
    text: str = ""
    engine: str = ""


def _significant(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def page_ranges(pages: list) -> str:
    """[1, 2, 3, 7] -> «1–3, 7»."""
    pages = sorted(set(int(p) for p in pages))
    if not pages:
        return ""
    groups, start, prev = [], pages[0], pages[0]
    for page in pages[1:]:
        if page == prev + 1:
            prev = page
            continue
        groups.append(f"{start}–{prev}" if prev > start else f"{start}")
        start = prev = page
    groups.append(f"{start}–{prev}" if prev > start else f"{start}")
    return ", ".join(groups)


def _big_image(page) -> Optional[bool]:
    """Чи займає зображення більшу частину сторінки (ознака скану)."""
    try:
        area = float(page.width) * float(page.height)
        if area <= 0:
            return None
        for image in page.images or []:
            width = abs(float(image.get("x1", 0)) - float(image.get("x0", 0)))
            height = abs(float(image.get("bottom", 0)) - float(image.get("top", 0)))
            if width * height >= SCAN_IMAGE_SHARE * area:
                return True
        return False
    except (TypeError, ValueError, AttributeError, KeyError):
        return None


def page_kind(text: str, big_image: Optional[bool]) -> str:
    """
    «text» — текстова сторінка; «no_text» — тексту немає зовсім; «scan» —
    велике зображення і мало тексту (скан зі штампом КЕП). Без даних про
    зображення діє простий поріг символів.
    """
    count = _significant(text)
    if count == 0:
        return "no_text"
    if big_image and count < SCAN_TEXT_CHARS:
        return "scan"
    if big_image is None and count < MIN_PAGE_CHARS:
        return "no_text"
    return "text"


def _pdf_pages(data: bytes) -> tuple:
    """(усього сторінок, [(текст, чи є велике зображення)], рушій, код, деталі)."""
    plumber_error = ""
    try:
        import pdfplumber                                         # noqa: PLC0415
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                total = len(pdf.pages)
                texts = []
                for page in pdf.pages[:MAX_PDF_PAGES]:
                    texts.append((page.extract_text() or "", _big_image(page)))
                    try:
                        page.close()
                    except AttributeError:
                        pass
                return total, texts, "pdfplumber", "", ""
        except Exception as exc:                                  # noqa: BLE001
            # Бібліотека загортає помилки pdfminer у свої класи, тому
            # розрізняємо за назвою і текстом, а не за типом.
            marker = f"{type(exc).__name__} {exc}".lower()
            if "password" in marker or "encrypt" in marker:
                return 0, [], "pdfplumber", ST_ENCRYPTED, "PDF захищено паролем"
            plumber_error = type(exc).__name__
    try:
        from pypdf import PdfReader                               # noqa: PLC0415
    except ImportError:
        if pdfplumber is None:
            return 0, [], "", ST_EXTRACTION_FAILED, (
                "немає бібліотеки для читання PDF (pdfplumber або pypdf): "
                "виконайте клітинку 1 ноутбука")
        return 0, [], "pdfplumber", ST_CORRUPTED, (
            f"PDF не читається ({plumber_error})")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception:                                     # noqa: BLE001
                opened = 0
            if not opened:
                return 0, [], "pypdf", ST_ENCRYPTED, "PDF захищено паролем"
        total = len(reader.pages)
        texts = [(page.extract_text() or "", None)
                 for page in reader.pages[:MAX_PDF_PAGES]]
        return total, texts, "pypdf", "", ""
    except Exception as exc:                                      # noqa: BLE001
        why = type(exc).__name__ + (f"; pdfplumber: {plumber_error}"
                                    if plumber_error else "")
        return 0, [], "pypdf", ST_CORRUPTED, f"PDF не читається ({why})"


def _pdf_text(data: bytes) -> TextResult:
    total, texts, engine, error, detail = _pdf_pages(data)
    if error:
        return TextResult(status=error, detail=detail, engine=engine)
    if total == 0:
        return TextResult(status=ST_CORRUPTED, detail="у PDF немає сторінок",
                          engine=engine)
    kinds = [page_kind(text, big) for text, big in texts]
    with_text = [i + 1 for i, kind in enumerate(kinds) if kind == "text"]
    without = [i + 1 for i, kind in enumerate(kinds) if kind != "text"]
    not_processed = list(range(len(texts) + 1, total + 1))
    parts = []
    for index, ((page_text, _), kind) in enumerate(zip(texts, kinds), 1):
        if kind == "text":
            parts.append(f"[сторінка {index}]\n{page_text.strip()}")
        elif kind == "scan":
            # Те, що є (зазвичай штамп підпису), не ховаємо — але й текстом
            # сторінки не називаємо.
            parts.append(f"[сторінка {index} — схоже на скан: велике зображення і "
                         f"{_significant(page_text)} симв. тексту]\n{page_text.strip()}")
        else:
            parts.append(f"[сторінка {index} — текстового шару немає]")
    if not_processed:
        parts.append(f"[сторінки {page_ranges(not_processed)} — не оброблялися: "
                     f"межа {MAX_PDF_PAGES} сторінок]")
    notes = []
    if without:
        notes.append(f"без тексту або скан: стор. {page_ranges(without)}")
    if not_processed:
        notes.append(f"не оброблено: стор. {page_ranges(not_processed)}")
    if not with_text:
        status = ST_OCR
    elif without or not_processed:
        status = ST_PARTIAL
    else:
        status = ST_PROCESSED
    return TextResult(status=status, detail="; ".join(notes), pages_total=total,
                      pages_with_text=with_text, pages_without_text=without,
                      pages_not_processed=not_processed, text="\n".join(parts),
                      engine=engine)


def _docx_text(data: bytes) -> TextResult:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        return TextResult(status=ST_CORRUPTED, detail="DOCX не відкривається як архів")
    if unpacked > MAX_DOCX_UNZIPPED_MB * 1024 * 1024:
        return TextResult(status=ST_UNSUPPORTED,
                          detail="DOCX після розпакування завеликий — відкрийте вручну")
    try:
        import docx                                               # noqa: PLC0415
        from docx.table import Table                              # noqa: PLC0415
        from docx.text.paragraph import Paragraph                 # noqa: PLC0415
    except ImportError:
        return TextResult(status=ST_EXTRACTION_FAILED,
                          detail="немає бібліотеки python-docx: виконайте клітинку 1")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:                                      # noqa: BLE001
        return TextResult(status=ST_CORRUPTED,
                          detail=f"DOCX не читається ({type(exc).__name__})")
    parts = []
    # Порядок абзаців і таблиць зберігаємо: підстави часто стоять у таблиці
    # посередині протоколу, і текст без порядку читався б навпаки.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            parts.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells, previous = [], None
                for cell in row.cells:
                    value = cell.text.strip()
                    if value != previous:              # обʼєднані клітинки
                        cells.append(value)
                    previous = value
                parts.append(" | ".join(cells))
    text = "\n".join(parts).strip()
    if _significant(text) < MIN_PAGE_CHARS:
        return TextResult(status=ST_EMPTY_TEXT, engine="python-docx",
                          detail="у DOCX немає тексту — можливо, вставлене зображення")
    return TextResult(status=ST_PROCESSED, text=text, engine="python-docx")


def _odt_text(data: bytes) -> TextResult:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("content.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        return TextResult(status=ST_CORRUPTED, detail=f"ODT не читається ({type(exc).__name__})")
    xml = re.sub(r"</text:(p|h)>", "\n", xml)
    xml = re.sub(r"<text:(tab|s)\b[^>]*/>", " ", xml)
    text = _html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()
    if _significant(text) < MIN_PAGE_CHARS:
        return TextResult(status=ST_EMPTY_TEXT, detail="в ODT немає тексту", engine="odt")
    return TextResult(status=ST_PROCESSED, text=text, engine="odt")


def _xlsx_text(data: bytes) -> TextResult:
    try:
        import openpyxl                                           # noqa: PLC0415
    except ImportError:
        return TextResult(status=ST_EXTRACTION_FAILED, detail="немає бібліотеки openpyxl")
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:                                      # noqa: BLE001
        return TextResult(status=ST_CORRUPTED, detail=f"XLSX не читається ({type(exc).__name__})")
    parts = []
    for sheet in book.worksheets:
        parts.append(f"[аркуш {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            values = ["" if v is None else str(v) for v in row]
            if any(v.strip() for v in values):
                parts.append(" | ".join(values))
    book.close()
    text = "\n".join(parts)
    status = ST_PROCESSED if _significant(text) >= MIN_PAGE_CHARS else ST_EMPTY_TEXT
    return TextResult(status=status, text=text if status == ST_PROCESSED else "",
                      engine="openpyxl")


def _plain_text(data: bytes, is_html: bool) -> TextResult:
    for encoding in ("utf-8", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    if is_html:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = text.strip()
    if _significant(text) < MIN_PAGE_CHARS:
        return TextResult(status=ST_EMPTY_TEXT, detail="тексту в файлі немає", engine="text")
    return TextResult(status=ST_PROCESSED, text=text, engine="text")


UNSUPPORTED_WHY = {
    "ole": "старий формат Word (.doc) — відкрийте файл вручну",
    "rtf": "формат RTF — відкрийте файл вручну",
    "zip": "архів усередині — відкрийте вручну",
    "p7s": "підписаний контейнер (.p7s) — вміст не розкривався",
    "der": "підписаний контейнер — вміст не розкривався",
    "binary": "невідомий двійковий формат",
}


def extract_text(data: bytes, real_type: str) -> TextResult:
    """Текст файла. Жодного OCR: скан так і називається сканом."""
    if real_type == "pdf":
        return _pdf_text(data)
    if real_type == "docx":
        return _docx_text(data)
    if real_type == "odt":
        return _odt_text(data)
    if real_type == "xlsx":
        return _xlsx_text(data)
    if real_type in ("text", "html"):
        return _plain_text(data, real_type == "html")
    if real_type in ("jpeg", "png", "tiff"):
        return TextResult(status=ST_OCR, detail="зображення (скан) — OCR не виконувався")
    if real_type == "corrupted":
        return TextResult(status=ST_CORRUPTED, detail="файл не відкривається як архів")
    return TextResult(status=ST_UNSUPPORTED,
                      detail=UNSUPPORTED_WHY.get(real_type, f"формат «{real_type}»"))


# ============================================================================
#  БЛОК 6. ЗАПИС НА ДИСК БЕЗ ПЕРЕЗАПИСУ
# ============================================================================
def atomic_write(path: str, data: bytes) -> None:
    """Пишемо у тимчасовий файл поруч і лише потім підміняємо: обрив
    посеред запису не залишить напівфайла під справжньою назвою."""
    folder = os.path.dirname(path) or "."
    handle, tmp = tempfile.mkstemp(prefix=".tmp_", dir=folder)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def file_sha(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return sha256_hex(fh.read())
    except OSError:
        return ""


def target_name(record: DocRecord, real_type: str, sha: str) -> str:
    """«02_Протокол_№_45.pdf». Розширення додається, якщо назва бреше."""
    stem = safe_name(record.title or f"dokument_{record.seq}", 90)
    ext = REAL_EXT.get(real_type, "")
    if ext and not stem.lower().endswith(ext):
        stem += ext
    if not record.is_latest:
        base, dot, tail = stem.rpartition(".")
        stem = (f"{base}__попередня_редакція_{sha[:8]}.{tail}" if dot
                else f"{stem}__попередня_редакція_{sha[:8]}")
    return f"{record.seq:02d}_{stem}"


def store_file(folder: str, record: DocRecord, data: bytes, real_type: str,
               sha: str) -> str:
    """
    Кладе файл у теку події. Наявний файл з іншим вмістом не чіпається:
    новий лягає поруч із хвостом контрольної суми.
    """
    name = target_name(record, real_type, sha)
    path = os.path.join(folder, name)
    if os.path.exists(path):
        if file_sha(path) == sha:
            return name
        base, dot, tail = name.rpartition(".")
        name = f"{base}__{sha[:8]}.{tail}" if dot else f"{name}__{sha[:8]}"
        path = os.path.join(folder, name)
        if os.path.exists(path) and file_sha(path) == sha:
            return name
    atomic_write(path, data)
    return name


def md5_check(data: bytes, declared: str) -> str:
    """Prozorro подає hash як «md5:…». Звіряємо, якщо він є."""
    declared = str(declared or "").strip().lower()
    if not declared.startswith("md5:"):
        return "NOT_DECLARED"
    return "OK" if hashlib.md5(data).hexdigest() == declared[4:] else "MISMATCH"


def obtain_document(record: DocRecord, folder: str,
                    fetcher: Optional[Callable], previous: dict,
                    now_fn: Callable = now_kyiv) -> DocRecord:
    """Один документ: службовий — пропустити, наявний — узяти, інакше — завантажити."""
    if record.author in SERVICE_AUTHORS or is_service_file(
            record.title, record.format, record.document_type):
        record.status = ST_SERVICE
        return record
    if record.author and record.author not in DECISION_AUTHORS:
        record.status = ST_OTHER_AUTHOR
        record.detail = f"автор у Prozorro: {record.author}"
        return record
    if is_archive(record.title, record.format):
        record.status = ST_ARCHIVE
        return record
    if not record.url:
        record.status = ST_NO_URL
        return record

    known = previous.get(record.identity())
    if known and known.get("status") in SAVED_STATES and known.get("file"):
        path = os.path.join(folder, known["file"])
        if os.path.exists(path) and file_sha(path) == known.get("sha256"):
            restorable = {f.name for f in fields(DocRecord)} - {"seq", "is_latest",
                                                                "reused"}
            for key, value in known.items():
                if key in restorable:
                    setattr(record, key, value)
            record.reused = True
            # Кеш дійсний лише для тієї самої версії читача (правило 45):
            # якщо текст читала інша версія або читання не вдалося через
            # бібліотеку — перечитуємо з файла на Диску, без мережі.
            if (record.status == ST_EXTRACTION_FAILED
                    or str(record.extractor).split("/")[0] != VERSION):
                with open(path, "rb") as fh:
                    _apply_text(record, extract_text(fh.read(), record.real_type))
            return record

    if fetcher is None:
        record.status = ST_NOT_REQUESTED
        return record
    fetched = fetcher(record.url)
    record.retrieved_at = now_fn().isoformat(timespec="seconds")
    if fetched.error:
        record.status, record.detail = fetched.error, fetched.detail
        return record
    data = fetched.data or b""
    if not data:
        record.status = ST_EMPTY_FILE
        return record
    real_type = sniff_type(data, record.format, record.title)
    declared_html = record.title.lower().endswith((".htm", ".html")) or \
        "html" in record.format.lower()
    if real_type == "html" and not declared_html:
        record.status = ST_HTML
        record.detail = "замість файла отримано вебсторінку — відкрийте посилання вручну"
        return record

    sha = sha256_hex(data)
    record.sha256, record.bytes, record.real_type = sha, len(data), real_type
    record.md5_check = md5_check(data, record.declared_hash)
    try:
        record.file = store_file(folder, record, data, real_type, sha)
    except OSError as exc:
        record.status, record.detail = ST_WRITE_FAILED, f"{type(exc).__name__}: {exc}"
        return record

    _apply_text(record, extract_text(data, real_type))
    return record


def _apply_text(record: DocRecord, result: TextResult) -> None:
    record.status, record.detail = result.status, result.detail
    record.pages_total = result.pages_total
    record.pages_with_text = result.pages_with_text
    record.pages_without_text = result.pages_without_text
    record.pages_not_processed = result.pages_not_processed
    record.text = result.text
    record.text_chars = len(result.text)
    record.text_engine = result.engine
    record.extractor = f"{VERSION}/{result.engine or '-'}"


# ============================================================================
#  БЛОК 7. СТАН РІШЕННЯ, СКАРГИ, ФРАГМЕНТИ
# ============================================================================
def find_decision(tender: Optional[dict], stage: str, object_id: str) -> Optional[dict]:
    for obj in (tender or {}).get(stage) or []:
        if str(obj.get("id") or "") == str(object_id or ""):
            return obj
    return None


def complaints_of(obj: Optional[dict]) -> list:
    out = []
    for item in (obj or {}).get("complaints") or []:
        if not isinstance(item, dict):
            continue
        out.append({"id": item.get("complaintID") or item.get("id") or "",
                    "type": str(item.get("type") or ""),
                    "status": str(item.get("status") or ""),
                    "submitted": str(item.get("dateSubmitted") or item.get("date") or "")})
    return out


#: Кінцеві стани скарги до органу оскарження: з них видно результат розгляду.
DECIDED_COMPLAINT_STATUSES = {"satisfied", "declined", "resolved", "invalid",
                              "stopped", "mistaken"}


def complaint_label(item: dict) -> str:
    kind = item.get("type") or ""
    status = item.get("status") or ""
    if (BLIND_COMPLAINT_OUTCOMES and kind == "complaint"
            and status in DECIDED_COMPLAINT_STATUSES):
        shown = "розгляд завершено (результат приховано: сліпий відбір)"
    else:
        shown = COMPLAINT_STATUS_UA.get(status, status or "статус невідомий")
    return f"{COMPLAINT_TYPE_UA.get(kind, kind or 'скарга')} — {shown}"


def complaints_text(items: list, fresh: bool) -> str:
    if not fresh:
        return f"{NOT_ESTABLISHED} (свіжий стан не отримано)"
    if not items:
        return "немає"
    counts = Counter(complaint_label(i) for i in items)
    return f"{len(items)}: " + "; ".join(
        f"{label} ×{n}" if n > 1 else label for label, n in counts.items())


def milestones_24h(obj: Optional[dict]) -> list:
    """
    Вимоги про усунення невідповідностей (milestone з кодом 24h). Опис —
    дослівний перелік невідповідностей, який замовник опублікував учаснику.
    """
    out = []
    for item in (obj or {}).get("milestones") or []:
        if not isinstance(item, dict) or str(item.get("code") or "") != "24h":
            continue
        out.append({"date": str(item.get("date") or ""),
                    "due": str(item.get("dueDate") or ""),
                    "met": str(item.get("dateMet") or ""),
                    "description": str(item.get("description") or "")})
    return out


def milestones_text(items: list, fresh: bool) -> str:
    if not fresh:
        return f"{NOT_ESTABLISHED} (свіжий стан не отримано)"
    if not items:
        return "немає"
    first = items[0]
    return (f"так: {fmt_dt(parse_dt(first['date'])) or 'дата невідома'}, строк до "
            f"{fmt_dt(parse_dt(first['due'])) or 'невідомо'}"
            + (f" (усього вимог: {len(items)})" if len(items) > 1 else ""))


#: Ознаки фактичної претензії. Це лише пошук місця для дослівного фрагмента
#: у переліку — не класифікація і не висновок.
_DEFICIENCY = re.compile(
    r"не\s+відповіда|не\s+надан|не\s+надав|не\s+подан|не\s+підтвердж|"
    r"не\s+містить|невідповідн|не\s+усун|не\s+виправ|не\s+завантаж|відсутн",
    re.IGNORECASE)
_QUOTE_LEAD = re.compile(
    r"відповідно\s+до|згідно\s+з|згідно\s+із|керуючись|передбачен|визначен|"
    r"встановлен\w*\s+(пункт|стат)", re.IGNORECASE)
_DECISION = re.compile(r"відхил", re.IGNORECASE)
_PAGE_MARK = re.compile(r"\[сторінка (\d+)[^\]]*\]")


def key_fragment(text: str, before: int = 200, after: int = 500) -> tuple:
    """
    (дослівний фрагмент, сторінка). Шукаємо перше фактичне твердження про
    недолік, оминаючи речення, що лише переписують норму («відповідно до…»).
    """
    if not text:
        return "", 0
    chosen = None
    for match in _DEFICIENCY.finditer(text):
        sentence_start = max(text.rfind(".", 0, match.start()),
                             text.rfind("\n", 0, match.start())) + 1
        if not _QUOTE_LEAD.search(text[sentence_start:match.start()]):
            chosen = match
            break
        chosen = chosen or match
    chosen = chosen or _DECISION.search(text)
    if not chosen:
        return "", 0
    # Фрагмент починається з речення, у якому знайдено твердження: так у
    # переліку видно саму претензію, а не шапку протоколу.
    sentence_start = max(text.rfind(".", 0, chosen.start()),
                         text.rfind("\n", 0, chosen.start()),
                         text.rfind(":", 0, chosen.start())) + 1
    start = max(sentence_start, chosen.start() - before, 0)
    end = min(len(text), chosen.start() + after)
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    page = 0
    for mark in _PAGE_MARK.finditer(text, 0, chosen.start()):
        page = int(mark.group(1))
    fragment = one_line(_PAGE_MARK.sub(" ", text[start:end]))
    return (("…" if start > 0 else "") + fragment + ("…" if end < len(text) else "")), page


def protocol_state(records: list, docs_known: bool) -> tuple:
    """(код стану, пояснення) — для бейджа картки і колонки переліку."""
    if not docs_known:
        return PROTO_UNKNOWN, "документи рішення не встановлено: свіжого стану немає"
    content = [r for r in records if r.status not in (ST_SERVICE, ST_ARCHIVE,
                                                      ST_OTHER_AUTHOR)]
    if not records:
        return PROTO_NONE, "у рішенні в Prozorro немає жодного документа"
    if not content:
        if any(r.status == ST_ARCHIVE for r in records):
            return PROTO_ONLY_SERVICE, "у рішенні лише архіви й службові файли"
        return PROTO_ONLY_SERVICE, "у рішенні лише підписи та службові файли"
    with_text = [r for r in content if r.status in TEXT_STATES]
    partial = [r for r in with_text if r.status == ST_PARTIAL]
    scans = [r for r in content if r.status == ST_OCR]
    saved = [r for r in content if r.status in SAVED_STATES]
    failed = [r for r in content if r.status not in SAVED_STATES]
    if with_text and not partial and not scans:
        state = PROTO_TEXT
    elif with_text:
        state = PROTO_PARTIAL
    elif scans:
        state = PROTO_SCAN
    elif saved:
        state = PROTO_UNREADABLE
    else:
        state = PROTO_FAILED
    notes = []
    if with_text:
        notes.append(f"з текстом: {len(with_text)}")
    if scans:
        notes.append(f"скан без тексту: {len(scans)}")
    if failed:
        notes.append("не завантажено: " + ", ".join(sorted({r.status for r in failed})))
    return state, "; ".join(notes)


def best_fragment(records: list, event_moment: Optional[datetime]) -> tuple:
    """
    Фрагмент для переліку. Спершу документ, опублікований найближче до
    моменту рішення: саме він найімовірніше є протоколом. Повідомлення про
    невідповідності, опубліковане раніше, — лише після нього.
    """
    def distance(record: DocRecord) -> float:
        moment = parse_dt(record.date_published, assume_kyiv=True)
        if not moment or not event_moment:
            return float("inf")
        return abs((moment.astimezone(timezone.utc)
                    - event_moment.astimezone(timezone.utc)).total_seconds())

    for record in sorted([r for r in records if r.status in TEXT_STATES], key=distance):
        fragment, page = key_fragment(record.text)
        if fragment:
            where = f"док. №{record.seq}" + (f", стор. {page}" if page else "")
            return fragment, where
    return "", ""


# ============================================================================
#  БЛОК 8. СЛУЖБОВА КАРТКА (.docx)
# ============================================================================
def _add_hyperlink(paragraph, url: str, text: str) -> None:
    """Клікабельне посилання. python-docx не має для цього готового методу."""
    from docx.oxml import OxmlElement                             # noqa: PLC0415
    from docx.oxml.ns import qn                                   # noqa: PLC0415
    from docx.opc.constants import RELATIONSHIP_TYPE as RT        # noqa: PLC0415
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    run.append(props)
    node = OxmlElement("w:t")
    node.text = xml_safe(text)
    node.set(qn("xml:space"), "preserve")
    run.append(node)
    link.append(run)
    paragraph._p.append(link)                                     # noqa: SLF001


def render_card_docx(ctx: dict) -> bytes:
    """Картка як байти .docx. Усе, що прийшло ззовні, проходить xml_safe."""
    import docx                                                   # noqa: PLC0415
    from docx.shared import Inches, Pt, RGBColor                  # noqa: PLC0415

    document = docx.Document()
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    def widths(grid, inches: tuple) -> None:
        """Ширини колонок: Word і Google Диск беруть їх із кожної клітинки."""
        grid.autofit = False
        for row in grid.rows:
            for cell, width in zip(row.cells, inches):
                cell.width = Inches(width)

    def para(text: str = "", bold: bool = False, italic: bool = False,
             color: Optional[tuple] = None):
        p = document.add_paragraph()
        run = p.add_run(xml_safe(text))
        run.bold, run.italic = bold, italic
        if color:
            run.font.color.rgb = RGBColor(*color)
        return p

    def table(rows: list) -> None:
        grid = document.add_table(rows=0, cols=2)
        grid.style = "Table Grid"
        for left, right in rows:
            cells = grid.add_row().cells
            cells[0].text = xml_safe(left)
            if isinstance(right, tuple) and right and right[0] == "link":
                cells[1].text = ""
                _add_hyperlink(cells[1].paragraphs[0], right[1], right[2])
            else:
                value = right if right not in (None, "") else NOT_ESTABLISHED
                cells[1].text = xml_safe(value)
        widths(grid, (2.1, 4.4))

    ev = ctx["event"]
    document.add_heading("СЛУЖБОВА КАРТКА ВІДХИЛЕННЯ", level=1)
    para(f"{ev['ua_id']} · {ev['company']} ({ev['code_label']} {ev['code']}) · "
         f"подія {ev['event_time_h'] or NOT_ESTABLISHED}")
    para(f"Строк оскарження: {ctx['complaint_state']} · Текст рішення: "
         f"{ctx['protocol_state']} · Скарги: {ctx['complaints_h']} · "
         f"Лід: {ev['lead_state_h']}", bold=True)
    if ctx.get("alerts"):
        for alert in ctx["alerts"]:
            para(f"УВАГА: {alert}", color=(0x9C, 0x27, 0x06))

    document.add_heading("1. Подія", level=2)
    table([
        ("Закупівля", ("link", ev["prozorro_url"], ev["ua_id"])),
        ("Предмет", ev["title"]),
        ("Замовник", f"{ev['buyer']} (ЄДРПОУ {ev['buyer_code'] or '—'})"),
        ("Тип процедури", ev["method_type"]),
        ("CPV", ev["cpv"]),
        ("Учасник (відхилений)", ev["company"]),
        (f"{ev['code_label']} учасника", ev["code"]),
        ("Зіставлення учасника", ev["identity_note"]),
        ("Спільна пропозиція", "так" if ev["joint_bid"] else "ні"),
        ("Стадія рішення", ev["stage_h"]),
        ("ID рішення / пропозиції / лота",
         f"{ev['object_id']} / {ev['bid_id'] or '—'} / {ev['lot_id'] or '—'}"),
        ("Час події", ev["event_time_h"]),
        ("Джерело і надійність часу",
         f"{ev['time_source']} · {ev['time_reliability']} · {ev['time_basis']}"),
        ("Статус рішення", ctx["object_status_h"]),
        ("Очікувана вартість лота / закупівлі",
         f"{money(ev['lot_value'])} / {money(ev['tender_value'])}"),
        ("Ціна пропозиції учасника", money(ev["bid_value"])),
        ("Переможець і його ціна",
         f"{ev['winner_name'] or NOT_ESTABLISHED} · {money(ev['winner_value'])}"),
        ("Період оскарження (Prozorro)",
         f"{ev['complaint_start_h'] or '—'} — {ev['complaint_end_h'] or '—'}"),
        ("Скарги і вимоги до рішення", ctx["complaints_h"]),
        ("Вимога на 24 години", ctx["milestones_h"]),
        ("Лист (тестовий режим)", ev["letter_h"]),
    ])
    para("Строк оскарження — це поле системи Prozorro, а не юридичний висновок.",
         italic=True)

    document.add_heading("2. Що написав замовник у полі рішення (дослівно з Prozorro)",
                         level=2)
    same = (one_line(ctx["decision_title"]) == one_line(ctx["decision_description"]))
    if ctx["decision_title"] or ctx["decision_description"]:
        if ctx["decision_title"]:
            para("Назва рішення" + (" (опис рішення дослівно такий самий):" if same
                                     else ":"), bold=True)
            para(ctx["decision_title"])
        if ctx["decision_description"] and not same:
            para("Опис рішення:", bold=True)
            for line in ctx["decision_description"].splitlines():
                para(line)
    else:
        para(f"{NOT_ESTABLISHED}: у полях рішення тексту немає. Підстави — "
             f"у документах рішення нижче.", italic=True)

    document.add_heading("3. Вимога про усунення невідповідностей (24 години), "
                         "дослівно з Prozorro", level=2)
    if not ctx["milestones_known"]:
        para(f"{NOT_ESTABLISHED}: свіжого стану рішення немає, тому вимоги не видно.",
             italic=True)
    elif not ctx["milestones"]:
        para("У рішенні вимоги на 24 години немає (станом на отримання свіжого "
             "стану). Це факт системи Prozorro; чи мала вона бути — питання аналізу.",
             italic=True)
    for number, item in enumerate(ctx["milestones"], 1):
        para(f"Вимога {number}: опубліковано {fmt_dt(parse_dt(item['date'])) or '—'}, "
             f"строк до {fmt_dt(parse_dt(item['due'])) or '—'}"
             + (f", виконання позначено {fmt_dt(parse_dt(item['met']))}"
                if item.get("met") else ""), bold=True)
        for line in (item["description"] or "(опису немає)").splitlines():
            if line.strip():
                para(line)

    document.add_heading("4. Документи рішення", level=2)
    para(ctx["docs_source_h"], italic=True)
    records = ctx["records"]
    if not records:
        para(ctx["no_docs_h"])
    else:
        grid = document.add_table(rows=1, cols=5)
        grid.style = "Table Grid"
        head = grid.rows[0].cells
        for cell, label in zip(head, ("№", "Документ", "Опубліковано",
                                      "Стан", "Файл у теці")):
            cell.text = label
        for record in records:
            cells = grid.add_row().cells
            cells[0].text = str(record.seq)
            cells[1].text = ""
            first = cells[1].paragraphs[0]
            first.add_run(xml_safe(record.title or "(без назви)"))
            if record.url:
                link_par = cells[1].add_paragraph()
                _add_hyperlink(link_par, record.url, "відкрити в Prozorro")
            if not record.is_latest:
                cells[1].add_paragraph("попередня редакція документа")
            published = parse_dt(record.date_published, assume_kyiv=True)
            cells[2].text = xml_safe(
                f"{fmt_dt(published) or '—'}\n"
                f"{relative_to_event(published, ctx['event_moment'])}".strip())
            state = STATUS_UA.get(record.status, record.status)
            extra = []
            if record.pages_total:
                extra.append(f"сторінок {record.pages_total}")
            if record.pages_without_text and not record.detail:
                extra.append(f"без тексту: {page_ranges(record.pages_without_text)}")
            if record.md5_check == "MISMATCH":
                extra.append("УВАГА: не збігається контрольна сума Prozorro")
            if record.detail and record.status not in (ST_PROCESSED,):
                extra.append(record.detail)
            cells[3].text = xml_safe(state + ("\n" + "; ".join(extra) if extra else ""))
            cells[4].text = xml_safe(
                (record.file or "—") + (f"\nSHA-256 {record.sha256[:16]}…"
                                        if record.sha256 else ""))
        widths(grid, (0.35, 1.9, 1.25, 1.65, 1.35))

    document.add_heading("5. Текст документів рішення (дослівно, як витягнуто з файлу)",
                         level=2)
    shown = 0
    for record in records:
        if record.status not in TEXT_STATES:
            continue
        shown += 1
        published = parse_dt(record.date_published, assume_kyiv=True)
        document.add_heading(
            xml_safe(f"Документ №{record.seq}: {record.title or '(без назви)'} · "
                     f"{fmt_dt(published) or 'дата невідома'}"
                     + (f" ({relative_to_event(published, ctx['event_moment'])})"
                        if published else "")), level=3)
        text = record.text
        cut = len(text) > CARD_TEXT_LIMIT
        if cut:
            text = text[:CARD_TEXT_LIMIT]
        blank = False
        for line in text.splitlines():
            if not line.strip():
                if not blank:
                    para("")
                blank = True
                continue
            blank = False
            if _PAGE_MARK.fullmatch(line.strip()):
                para(line.strip(), bold=True,
                     color=(0x9C, 0x27, 0x06) if ("немає" in line or "скан" in line)
                     else None)
            else:
                para(line)
        if cut:
            para(f"[Показано {CARD_TEXT_LIMIT:,} з {len(record.text):,} символів. "
                 f"Повний текст — у файлі {record.file}.]".replace(",", " "),
                 italic=True)
    if not shown:
        para("Тексту, витягнутого з документів, немає. Причина — у таблиці вище; "
             "відкрийте файл або посилання вручну.", italic=True)

    document.add_heading("6. Що перевірити вручну", level=2)
    for item in ctx["to_check"]:
        document.add_paragraph(xml_safe(item), style="List Bullet")

    document.add_heading("7. Нотатки", level=2)
    para("(Пишіть тут. Картку з вашими правками скрипт не перезапише — "
         "нова версія ляже поруч окремим файлом.)", italic=True)
    para("")

    document.add_heading("8. Службові дані", level=2)
    table([
        ("Прогін", ctx["run_label"]),
        ("Сформовано", ctx["generated_h"]),
        ("Свіжий стан Prozorro", ctx["fresh_h"]),
        ("Тека події", ctx["folder"]),
        ("Опис документів", MANIFEST_NAME),
        ("Версія", f"TenderWin Service Cards {VERSION} · Lead & Mail "
                   f"{ctx.get('lead_mail_version') or ''}".strip()),
    ])
    para("Це автоматичний витяг фактів із Prozorro і файлів рішення, а не "
         "юридичний висновок. Підстави відхилення читайте в оригіналі протоколу.",
         italic=True)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def write_card(folder: str, ua_id: str, payload: bytes, recorded_sha: str,
               now_fn: Callable = now_kyiv) -> tuple:
    """
    (імʼя файла картки, що сталося). Картку з правками людини не чіпаємо:
    якщо файл відрізняється від того, що ми записали минулого разу, нова
    версія лягає поруч з датою в назві.
    """
    name = f"{CARD_PREFIX}{safe_name(ua_id, 60)}.docx"
    path = os.path.join(folder, name)
    if os.path.exists(path):
        current = file_sha(path)
        if not recorded_sha or current != recorded_sha:
            alt = name[:-5] + f"__onovleno_{now_fn():%Y%m%d-%H%M%S}.docx"
            atomic_write(os.path.join(folder, alt), payload)
            return alt, "KEPT_HUMAN_EDIT"
        atomic_write(path, payload)
        return name, "UPDATED"
    atomic_write(path, payload)
    return name, "CREATED"


# ============================================================================
#  БЛОК 9. ОДНА ПОДІЯ: ВІД РЯДКА БАЗИ ДО ТЕКИ З КАРТКОЮ
# ============================================================================
def load_manifest(folder: str, now_fn: Callable = now_kyiv) -> dict:
    """Попередній опис теки. Пошкоджений файл не видаляємо — відкладаємо."""
    path = os.path.join(folder, MANIFEST_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        try:
            os.replace(path, path + f".broken_{now_fn():%Y%m%d-%H%M%S}")
        except OSError:
            pass
        return {}


def _previously_seen(manifest_prev: dict, records: list) -> list:
    current = {r.identity() for r in records}
    kept = []
    for item in list(manifest_prev.get("documents") or []) + list(
            manifest_prev.get("previously_seen") or []):
        if isinstance(item, dict) and item.get("identity") and \
                item["identity"] not in current and \
                item["identity"] not in {k.get("identity") for k in kept}:
            kept.append({k: item.get(k) for k in ("identity", "title", "url", "file",
                                                  "sha256", "status", "date_published",
                                                  "date_modified")})
    return kept


def _code_label(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    return {8: "ЄДРПОУ", 10: "ІПН"}.get(len(digits), "код")


def _event_view(row: dict, now_moment: datetime) -> dict:
    event_moment = parse_dt(row.get("event_time"), assume_kyiv=True)
    start = parse_dt(row.get("complaint_start"), assume_kyiv=True)
    end = parse_dt(row.get("complaint_end"), assume_kyiv=True)
    state = str(row.get("state") or "")
    letter = str(row.get("letter_status") or "")
    letter_h = (f"{letter}" + (f" · шаблон {row.get('letter_template')}"
                               if row.get("letter_template") else "")
                if letter else "листа про цю подію немає")
    return {
        "event_id": row.get("event_id") or "",
        "ua_id": row.get("ua_id") or "",
        "prozorro_url": PROZORRO_TENDER_URL.format(ua_id=row.get("ua_id") or ""),
        "tender_id": row.get("tender_id") or "",
        "title": row.get("title") or "",
        "buyer": row.get("buyer_name") or "",
        "buyer_code": row.get("buyer_edrpou") or "",
        "cpv": row.get("cpv") or "",
        "method_type": row.get("method_type") or "",
        "company": row.get("company_name") or "",
        "code": row.get("edrpou_norm") or "",
        "code_label": _code_label(row.get("edrpou_norm") or ""),
        "identity_note": row.get("identity_note") or "",
        "joint_bid": bool(row.get("joint_bid")),
        "stage": row.get("stage") or "",
        "stage_h": ("рішення після оцінки (award)" if row.get("stage") == "awards"
                    else "прекваліфікація (qualification)"),
        "object_id": row.get("object_id") or "",
        "bid_id": row.get("bid_id") or "",
        "lot_id": row.get("lot_id") or "",
        "event_moment": event_moment,
        "event_time_h": fmt_dt(event_moment),
        "time_source": row.get("event_time_source") or "",
        "time_reliability": row.get("event_time_reliability") or "",
        "time_basis": row.get("event_time_basis") or "",
        "object_status": row.get("object_status") or "",
        "lot_value": row.get("lot_value"),
        "tender_value": row.get("tender_value"),
        "bid_value": row.get("bid_value"),
        "winner_value": row.get("winner_value"),
        "winner_name": row.get("winner_name") or "",
        "complaint_start_h": fmt_dt(start),
        "complaint_end_h": fmt_dt(end),
        "complaint_end": end,
        "complaint_state": ("ВІДКРИТИЙ до " + fmt_dt(end) if end and end > now_moment
                            else ("ЗАКРИТИЙ (" + fmt_dt(end) + ")" if end
                                  else NOT_ESTABLISHED)),
        "complaint_short": ("ВІДКРИТИЙ" if end and end > now_moment
                            else ("ЗАКРИТИЙ" if end else NOT_ESTABLISHED)),
        "lead_state": state,
        "lead_state_h": f"{state} — {LEAD_STATE_UA.get(state, '')}".strip(" —"),
        "state_reason": row.get("state_reason") or "",
        "letter_h": letter_h,
        "reason_raw": row.get("reason_raw") or "",
    }


def build_event(row: dict, fresh: Optional[tuple], root: str, run_label: str,
                fetcher: Optional[Callable], now_fn: Callable = now_kyiv,
                lead_mail_version: str = "") -> dict:
    """
    Одна подія. Повертає рядок для переліку. Помилка тут не зупиняє інші
    події — її ловить оркестратор і записує в журнал.
    """
    moment = now_fn()
    ev = _event_view(row, moment)
    day = event_day(row)
    folder = os.path.join(root, day, case_folder_name(row))
    os.makedirs(folder, exist_ok=True)
    manifest_prev = load_manifest(folder, now_fn)
    previous = {d.get("identity"): d for d in manifest_prev.get("documents", [])
                if isinstance(d, dict) and d.get("identity")}

    # --- свіжий стан рішення ------------------------------------------------
    tender, fresh_error, fetched_at = (fresh or (None, "NOT_REQUESTED", ""))
    decision = find_decision(tender, ev["stage"], ev["object_id"]) if tender else None
    alerts, to_check = [], []
    if decision is not None:
        records = docs_from_api(decision.get("documents"))
        docs_source = (f"Документи — зі свіжого стану Prozorro "
                       f"({fmt_dt(parse_dt(fetched_at)) or fetched_at}).")
        docs_known = True
    else:
        records = docs_from_stored(row.get("docs_decision"))
        if tender is not None:
            why = "у свіжому стані закупівлі цього рішення немає"
        elif fresh_error == "NOT_REQUESTED":
            why = "свіжий стан не запитувався (налаштування)"
        else:
            why = f"свіжий стан не отримано ({fresh_error or 'невідома причина'})"
        docs_source = (f"Документи — з даних сканування Lead & Mail: {why}. "
                       f"Документи, додані пізніше, тут не видно.")
        docs_known = bool(records)
        to_check.append(f"Свіжий стан рішення не отримано: {why}. Перевірте "
                        f"рішення в Prozorro за посиланням.")

    # --- документи ----------------------------------------------------------
    downloadable = 0
    for record in records:
        if (not is_service_file(record.title, record.format, record.document_type)
                and not is_archive(record.title, record.format)
                and record.author not in SERVICE_AUTHORS
                and (not record.author or record.author in DECISION_AUTHORS)):
            downloadable += 1
            if downloadable > MAX_DOCS_PER_EVENT:
                record.status = ST_LIMIT
                continue
        obtain_document(record, folder, fetcher, previous, now_fn)

    # --- стан рішення і скарги ----------------------------------------------
    status_now = str((decision or {}).get("status") or "")
    if decision is not None:
        object_status_h = (f"зараз «{status_now}»" + (
            "" if status_now == "unsuccessful" else
            " — рішення змінилося після відхилення, перевірте"))
        if status_now and status_now != "unsuccessful":
            alerts.append(f"Рішення зараз у статусі «{status_now}», а не "
                          f"«unsuccessful»: відхилення могли скасувати.")
            to_check.append("Статус рішення змінився після відхилення — "
                            "перевірте історію рішення в Prozorro.")
    else:
        object_status_h = (f"на момент сканування «{ev['object_status']}»; "
                           f"свіжого стану немає")
    complaints = complaints_of(decision)
    complaints_h = complaints_text(complaints, decision is not None)
    milestones = milestones_24h(decision)
    milestones_h = milestones_text(milestones, decision is not None)
    if complaints:
        alerts.append(f"До рішення подано скарги/вимоги: {complaints_h}.")
        to_check.append("До рішення є скарги чи вимоги — їхні документи в "
                        "картку не завантажувались.")

    proto_state, proto_note = protocol_state(records, docs_known)
    for record in records:
        if record.status == ST_OCR:
            to_check.append(f"Документ №{record.seq}: текстового шару немає (скан) — "
                            f"прочитайте файл {record.file or 'за посиланням'}.")
        elif record.status == ST_PARTIAL:
            missing_pages = record.pages_without_text + record.pages_not_processed
            several = len(missing_pages) > 1
            to_check.append(f"Документ №{record.seq}: {'сторінки' if several else 'сторінка'} "
                            f"{page_ranges(missing_pages)} без тексту — дочитайте "
                            f"{'їх' if several else 'її'} у файлі {record.file}.")
        elif record.status in (ST_UNSUPPORTED, ST_ENCRYPTED, ST_CORRUPTED,
                               ST_EXTRACTION_FAILED, ST_EMPTY_TEXT):
            to_check.append(f"Документ №{record.seq}: {STATUS_UA.get(record.status)}"
                            f" ({record.detail}) — відкрийте файл {record.file} вручну.")
        elif record.status not in TEXT_STATES and record.status not in (ST_SERVICE,):
            to_check.append(f"Документ №{record.seq} «{record.title}»: "
                            f"{STATUS_UA.get(record.status, record.status)}"
                            f"{' (' + record.detail + ')' if record.detail else ''}"
                            f" — відкрийте за посиланням у Prozorro.")
        if record.md5_check == "MISMATCH":
            to_check.append(f"Документ №{record.seq}: вміст не збігається з "
                            f"контрольною сумою Prozorro — звірте з оригіналом.")
    if docs_known and not records:
        to_check.append("У рішенні немає документів — підстави видно лише в "
                        "полі рішення. Протокол міг бути не опублікований або "
                        "опублікований не в рішенні, а серед документів закупівлі "
                        "— перевірте в Prozorro.")
    if proto_state == PROTO_ONLY_SERVICE:
        to_check.append("У рішенні лише підписи, витяги ЄДР чи архіви. Протокол "
                        "може бути в архіві — відкрийте рішення в Prozorro.")
    if ev["complaint_end"] and ev["complaint_end"] <= moment:
        to_check.append(f"Строк оскарження в Prozorro вже минув "
                        f"({ev['complaint_end_h']}).")
    to_check.append("Звірте з оригіналом протоколу: картка — автоматичний витяг.")

    title = str((decision or {}).get("title") or "") if decision is not None else ""
    description = (str((decision or {}).get("description") or "")
                   if decision is not None else "")
    if decision is None:
        lines = [x for x in ev["reason_raw"].splitlines() if x.strip()]
        title = lines[0] if lines else ""
        description = "\n".join(lines[1:])

    ctx = {
        "event": ev, "event_moment": ev["event_moment"], "records": records,
        "complaint_state": ev["complaint_state"], "protocol_state": proto_state,
        "complaints_h": complaints_h, "alerts": alerts, "to_check": to_check,
        "milestones": milestones, "milestones_known": decision is not None,
        "milestones_h": milestones_h,
        "object_status_h": object_status_h, "decision_title": title,
        "decision_description": description, "docs_source_h": docs_source,
        "no_docs_h": ("У рішенні в Prozorro немає документів."
                      if docs_known else f"{NOT_ESTABLISHED}: документів рішення не "
                                         f"видно, бо свіжий стан не отримано."),
        "run_label": run_label, "generated_h": fmt_dt(moment),
        "fresh_h": (f"отримано {fmt_dt(parse_dt(fetched_at))}" if decision is not None
                    else f"не отримано: {fresh_error or 'невідомо'}"),
        "folder": os.path.join(day, os.path.basename(folder)),
        "lead_mail_version": lead_mail_version,
    }
    payload = render_card_docx(ctx)
    card_name, card_action = write_card(folder, ev["ua_id"], payload,
                                        (manifest_prev.get("card") or {}).get("sha256", ""),
                                        now_fn)

    history = list(manifest_prev.get("history") or [])
    history.append({"run": run_label, "at": moment.isoformat(timespec="seconds"),
                    "card": card_action, "fresh": decision is not None})
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generator": f"TenderWin Service Cards {VERSION}",
        "event": {k: (v.isoformat() if isinstance(v, datetime) else v)
                  for k, v in ev.items()},
        "fresh_state": {"fetched_at": fetched_at, "error": None if decision is not None
                        else (fresh_error or "NOT_FOUND"),
                        "object_status": status_now, "complaints": complaints,
                        "milestones_24h": milestones},
        "decision_object_raw": decision,
        "documents": [dict(asdict(r), identity=r.identity()) for r in records],
        # Документи, які бачили раніше, а тепер у Prozorro їх немає: файли
        # лишаються в теці, і опис про них не забуває (правило 17).
        "previously_seen": _previously_seen(manifest_prev, records),
        "card": {"file": card_name,
                 "sha256": (sha256_hex(payload) if card_action != "KEPT_HUMAN_EDIT"
                            else (manifest_prev.get("card") or {}).get("sha256", "")),
                 "action": card_action, "written_at": moment.isoformat(timespec="seconds")},
        "history": history,
    }
    atomic_write(os.path.join(folder, MANIFEST_NAME),
                 json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))

    fragment, where = best_fragment(records, ev["event_moment"])
    same_text = one_line(title) == one_line(description)
    reason_for_index = one_line("\n".join(x for x in (
        (title,) if same_text else (title, description)) if x), INDEX_TEXT_LIMIT)
    counts = Counter(r.status for r in records)
    return {
        "ok": True, "day": day, "folder": os.path.join(day, os.path.basename(folder)),
        "card": card_name, "card_action": card_action,
        "ua_id": ev["ua_id"], "prozorro_url": ev["prozorro_url"],
        "event_moment": ev["event_moment"], "buyer": ev["buyer"],
        "company": ev["company"], "code": ev["code"], "title": ev["title"],
        "lot_value": ev["lot_value"] or ev["tender_value"], "bid_value": ev["bid_value"],
        "method_type": ev["method_type"], "complaint_end": ev["complaint_end"],
        "complaint_state": ev["complaint_short"],
        "complaints": complaints_h, "milestone": milestones_h,
        "lead_state": ev["lead_state"],
        "state_reason": ev["state_reason"], "reason": reason_for_index,
        "protocol_state": proto_state, "protocol_note": proto_note,
        "fragment": one_line(fragment, INDEX_TEXT_LIMIT + 2),
        "fragment_where": where, "docs_total": len(records),
        "statuses": dict(counts), "reused": sum(1 for r in records if r.reused),
        "fresh": decision is not None,
    }


# ============================================================================
#  БЛОК 10. ПЕРЕЛІК ДЛЯ ВІДБОРУ (.xlsx; без openpyxl — .csv)
# ============================================================================
INDEX_COLUMNS = (
    ("№", 5), ("Закупівля", 24), ("Подія (Київ)", 16), ("Замовник", 30),
    ("Учасник", 28), ("Код учасника", 12), ("Предмет", 40),
    ("Вартість лота, грн", 15), ("Ціна учасника, грн", 15), ("Тип процедури", 16),
    ("Строк оскарження до", 16), ("Строк", 11), ("Скарги на рішення", 18),
    ("Вимога 24 год", 22),
    ("Лід", 16), ("Чому такий стан ліда", 26),
    ("Поле рішення в Prozorro (дослівно)", 60),
    ("Текст документів рішення", 18), ("Що з документами", 26),
    ("Фрагмент документа (дослівно)", 70), ("Де фрагмент", 14),
    ("Тека події", 40), ("Цікаво? (так/ні)", 10), ("Нотатки", 30),
)


def _index_rows(summaries: list) -> list:
    rows = []
    def moment_key(item: dict) -> float:
        moment = item.get("event_moment")
        return moment.timestamp() if moment else float("inf")

    ordered = sorted(summaries, key=moment_key)
    for number, item in enumerate(ordered, 1):
        moment = item.get("event_moment")
        end = item.get("complaint_end")
        def text(key: str) -> str:
            return str(item.get(key) or "")

        rows.append([
            number, text("ua_id"),
            moment.astimezone(KYIV).replace(tzinfo=None) if moment else "",
            text("buyer"), text("company"), text("code"), text("title"),
            item.get("lot_value") or "", item.get("bid_value") or "",
            text("method_type"),
            end.astimezone(KYIV).replace(tzinfo=None) if end else "",
            text("complaint_state"), text("complaints"), text("milestone"),
            text("lead_state"), text("state_reason"), text("reason"),
            text("protocol_state") if item.get("ok") else "КАРТКУ НЕ СФОРМОВАНО",
            text("protocol_note") if item.get("ok") else text("error"),
            text("fragment"), text("fragment_where"), text("folder"), "", "",
        ])
    return rows


def write_index(folder: str, label: str, summaries: list) -> str:
    """Перелік подій: один рядок — одне відхилення. Імʼя файла з міткою
    прогону, тож попередні переліки не перезаписуються."""
    os.makedirs(folder, exist_ok=True)
    rows = _index_rows(summaries)
    try:
        import openpyxl                                           # noqa: PLC0415
        from openpyxl.styles import Alignment, Font               # noqa: PLC0415
        from openpyxl.utils import get_column_letter              # noqa: PLC0415
    except ImportError:
        path = unused_path(os.path.join(folder, f"{INDEX_PREFIX}{label}.csv"))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([c for c, _ in INDEX_COLUMNS])
        for row in rows:
            writer.writerow([fmt_dt(v.replace(tzinfo=KYIV)) if isinstance(v, datetime)
                             else v for v in row])
        atomic_write(path, buffer.getvalue().encode("utf-8-sig"))
        return path

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Відхилення"
    sheet.append([c for c, _ in INDEX_COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        sheet.append([xml_safe(v) if isinstance(v, str) else v for v in row])
    for index, (_, width) in enumerate(INDEX_COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row_cells in sheet.iter_rows(min_row=2):
        if row_cells[1].value:
            row_cells[1].hyperlink = PROZORRO_TENDER_URL.format(ua_id=row_cells[1].value)
            row_cells[1].style = "Hyperlink"
        for cell in row_cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for col in (2, 10):
            row_cells[col].number_format = "dd.mm.yyyy hh:mm"
        for col in (7, 8):
            row_cells[col].number_format = "#,##0"
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions

    notes = book.create_sheet("Як читати")
    for line in (
            "Один рядок — одна подія відхилення (компанія + рішення + лот).",
            "«Поле рішення» — дослівно з полів рішення в Prozorro.",
            "«Фрагмент документа» — дослівний уривок із документа рішення, "
            "автоматично взятий біля першого твердження про недолік. Це лише "
            "підказка, де читати: повний текст — у службовій картці.",
            "«ТЕКСТ Є ЧАСТКОВО» і «СКАН — ТЕКСТУ НЕМАЄ» означають, що частину "
            "сторінок треба прочитати у файлі самостійно. OCR не виконувався.",
            "Строк оскарження — поле Prozorro, а не юридичний висновок.",
            "Колонки «Цікаво?» і «Нотатки» — для вас. Скрипт цей файл більше "
            "не змінює: кожен прогін створює новий перелік."):
        notes.append([line])
    notes.column_dimensions["A"].width = 120

    path = unused_path(os.path.join(folder, f"{INDEX_PREFIX}{label}.xlsx"))
    buffer = io.BytesIO()
    book.save(buffer)
    atomic_write(path, buffer.getvalue())
    return path


def unused_path(path: str) -> str:
    """Перелік з тією самою міткою вже є (повторний M.kartky): у ньому можуть
    бути ваші позначки, тож новий лягає поруч із часом у назві."""
    if not os.path.exists(path):
        return path
    base, dot, ext = path.rpartition(".")
    stamp = datetime.now(KYIV).strftime("%Y%m%d-%H%M%S")
    candidate = f"{base}__{stamp}.{ext}"
    counter = 2
    while os.path.exists(candidate):
        candidate = f"{base}__{stamp}_{counter}.{ext}"
        counter += 1
    return candidate


# ============================================================================
#  БЛОК 11. ОРКЕСТРАТОР
# ============================================================================
def empty_stats(root: str) -> dict:
    return {"подій": 0, "карток": {"CREATED": 0, "UPDATED": 0, "KEPT_HUMAN_EDIT": 0,
                                   "FAILED": 0},
            "документів": 0, "стани_документів": {}, "текст_рішення": {},
            "свіжий_стан": {"отримано": 0, "не_отримано": 0},
            "перелік": [], "тека": root, "помилки": [], "журнал_помилок": "",
            "тривалість_с": 0.0}


def build_service_cards(rows: list, *, root: str, run_label: str,
                        tender_fetcher: Optional[Callable] = None,
                        doc_fetcher: Optional[Callable] = None,
                        workers: int = 0, verbose: bool = True,
                        now_fn: Callable = now_kyiv,
                        lead_mail_version: str = "") -> dict:
    """
    Службові картки для переданих подій.

    rows            — рядки подій (dict) від Lead & Mail;
    tender_fetcher  — uid -> (закупівля або None, код помилки); None — без
                      свіжого стану, лише дані сканування;
    doc_fetcher     — url -> Fetched; None — без завантаження (у картці так і
                      буде написано).
    """
    started = time.time()
    stats = empty_stats(root)
    rows = [dict(r) for r in rows]
    stats["подій"] = len(rows)
    if not rows:
        return stats
    os.makedirs(root, exist_ok=True)

    fresh: dict = {}
    if tender_fetcher is not None:
        uids = sorted({str(r.get("tender_id") or "") for r in rows if r.get("tender_id")})
        if verbose:
            print(f"    свіжий стан рішень: закупівель {len(uids)}…", flush=True)

        def take(uid: str) -> tuple:
            try:
                data, error = tender_fetcher(uid)
            except Exception as exc:                              # noqa: BLE001
                data, error = None, f"FETCH_EXCEPTION:{type(exc).__name__}"
            return uid, data, error, now_fn().isoformat(timespec="seconds")

        with ThreadPoolExecutor(max_workers=FRESH_WORKERS) as pool:
            for uid, data, error, at in pool.map(take, uids):
                fresh[uid] = (data, error or ("" if data else "EMPTY"), at)
                stats["свіжий_стан"]["отримано" if data else "не_отримано"] += 1

    summaries, failures = [], []
    if verbose:
        print(f"    документи рішень і картки: подій {len(rows)}…", flush=True)
    with ThreadPoolExecutor(max_workers=workers or WORKERS) as pool:
        futures = {pool.submit(build_event, row, fresh.get(str(row.get("tender_id") or "")),
                               root, run_label, doc_fetcher, now_fn,
                               lead_mail_version): row for row in rows}
        for done, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                summary = future.result()
            except Exception as exc:                              # noqa: BLE001
                # Межа оркестратора: одна зламана подія не зупиняє решту.
                trace = traceback.format_exc()
                failures.append((row, trace))
                summary = {"ok": False, "day": event_day(row),
                           "ua_id": row.get("ua_id") or "",
                           "event_moment": parse_dt(row.get("event_time"), True),
                           "buyer": row.get("buyer_name") or "",
                           "company": row.get("company_name") or "",
                           "code": row.get("edrpou_norm") or "",
                           "title": row.get("title") or "",
                           "lead_state": row.get("state") or "",
                           "error": f"CARD_FAILED: {type(exc).__name__}: {exc}"[:300],
                           "folder": os.path.join(event_day(row), case_folder_name(row))}
                stats["карток"]["FAILED"] += 1
                stats["помилки"].append(f"{row.get('ua_id')}: {type(exc).__name__}")
            else:
                stats["карток"][summary["card_action"]] += 1
                stats["документів"] += summary["docs_total"]
                for status, count in summary["statuses"].items():
                    stats["стани_документів"][status] = (
                        stats["стани_документів"].get(status, 0) + count)
                state = summary["protocol_state"]
                stats["текст_рішення"][state] = stats["текст_рішення"].get(state, 0) + 1
            summaries.append(summary)
            if verbose and (done % 10 == 0 or done == len(rows)):
                print(f"    оброблено {done}/{len(rows)}", flush=True)

    by_day: dict = {}
    for summary in summaries:
        by_day.setdefault(summary.get("day") or "bez-daty", []).append(summary)
    for day, items in sorted(by_day.items()):
        try:
            stats["перелік"].append(write_index(os.path.join(root, day), run_label, items))
        except Exception as exc:                                  # noqa: BLE001
            stats["помилки"].append(f"перелік {day}: {type(exc).__name__}: {exc}")

    if failures:
        log_path = os.path.join(root, f"_pomylky_{run_label}.log")
        lines = [f"TenderWin Service Cards {VERSION} · прогін {run_label}\n"]
        for row, trace in failures:
            lines.append(f"\n=== {row.get('ua_id')} · подія {row.get('event_id')}\n{trace}")
        try:
            atomic_write(log_path, "".join(lines).encode("utf-8"))
            stats["журнал_помилок"] = log_path
        except OSError:
            pass
    stats["тривалість_с"] = round(time.time() - started, 1)
    return stats


def summary_lines(stats: dict) -> list:
    """Підсумок кроку для людини: що зроблено і чого бракує."""
    events = stats.get("подій", 0)
    if not events:
        return ["Подій для службових карток немає."]
    cards = stats.get("карток", {})
    parts = [f"нових {cards.get('CREATED', 0)}"]
    if cards.get("UPDATED"):
        parts.append(f"оновлено {cards['UPDATED']}")
    if cards.get("KEPT_HUMAN_EDIT"):
        parts.append(f"з вашими правками (нова версія поруч) {cards['KEPT_HUMAN_EDIT']}")
    if cards.get("FAILED"):
        parts.append(f"НЕ СФОРМОВАНО {cards['FAILED']}")
    lines = [f"Подій: {events} · карток: " + ", ".join(parts)]
    fresh = stats.get("свіжий_стан", {})
    if fresh.get("отримано") or fresh.get("не_отримано"):
        lines.append(f"Свіжий стан рішень: закупівель {fresh.get('отримано', 0)}"
                     + (f"; НЕ отримано {fresh['не_отримано']} — для них документи "
                        f"зі сканування" if fresh.get("не_отримано") else ""))
    states = stats.get("стани_документів", {})
    saved = sum(v for k, v in states.items() if k in SAVED_STATES)
    service = states.get(ST_SERVICE, 0) + states.get(ST_ARCHIVE, 0)
    missing = sum(v for k, v in states.items()
                  if k not in SAVED_STATES and k not in (ST_SERVICE, ST_ARCHIVE))
    lines.append(f"Документів рішень: {stats.get('документів', 0)} · у теках {saved}"
                 f" · підписи, витяги ЄДР, архіви {service}"
                 + (f" · НЕ ЗАВАНТАЖЕНО {missing}" if missing else ""))
    text = stats.get("текст_рішення", {})
    order = (PROTO_TEXT, PROTO_PARTIAL, PROTO_SCAN, PROTO_UNREADABLE, PROTO_FAILED,
             PROTO_ONLY_SERVICE, PROTO_NONE, PROTO_UNKNOWN)
    shown = [f"{key.lower()}: {text[key]}" for key in order if text.get(key)]
    if shown:
        lines.append("Текст рішення по подіях — " + " · ".join(shown))
    lines.append(f"Тека: {stats.get('тека')}")
    for path in stats.get("перелік", []):
        lines.append(f"Перелік для відбору: {path}")
    if stats.get("журнал_помилок"):
        lines.append(f"Журнал помилок: {stats['журнал_помилок']}")
    for error in (stats.get("помилки") or [])[:5]:
        lines.append(f"   ! {error}")
    lines.append(f"Тривалість кроку: {stats.get('тривалість_с', 0)} с")
    lines.append("ⓘ Завантажуються лише документи самого рішення замовника. OCR не "
                 "виконувався: скановані сторінки названо в картці поіменно.")
    return lines


def dependency_report() -> dict:
    """Що з бібліотек є. Без них документи завантажуються, але не читаються."""
    report = {}
    for name in ("pdfplumber", "pypdf", "docx", "openpyxl", "requests"):
        try:
            __import__(name)
            report[name] = True
        except ImportError:
            report[name] = False
    return report
