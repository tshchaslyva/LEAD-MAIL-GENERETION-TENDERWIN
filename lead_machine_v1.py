# -*- coding: utf-8 -*-
"""
=============================================================================
 LEAD MACHINE v1 · ЯДРО ДЛЯ TENDERWIN LEAD ENGINE
=============================================================================

 ЩО ЦЕ

 Урізана версія робочого лід-генератора. Містить рівно те, що потрібне
 движку `tenderwin_lead_engine.py`, і нічого зайвого.

 Код збережених функцій НЕ переписувався — він скопійований дослівно з
 `05-tenderwin_lead_machine_v1.py` (SHA-256 33f58c75…be5fee9b) автоматичним
 витягом із транзитивними залежностями. Це навмисно: поведінка того, що
 працювало, має лишитись такою самою (правило 10).

 ЩО ЗАЛИШИЛОСЬ

   пошук відхилень за день   scan_day, day_rejections, all_rejections
   ідентифікація учасника    tenderer_by_bid, participant_contacts, bid_docs_for
   текст рішення замовника   download_protocol
   розбір претензій          split_claims, classify, classify_claims,
                             claim_specificity, main_ground
   кличний відмінок          parse_pib, greeting, voc_*
   довідники                 CODE_HUMAN, NORMA, CPV_RULES

 ЩО ПРИБРАНО І ЧОМУ

   Snov.io, 19 функцій       движок надсилає через Gmail сам
   listи, CRM, EML, XLSX     цим тепер займається движок
   застарілі шаблони листів  текст листа живе в движку, з версіонуванням
   оцінка перспективи        у холодний лист вона більше не потрапляє
   формування скарг і цін    поза межами першого контакту

 ЩО ЗАЛИШИЛОСЬ, АЛЕ МОВЧИТЬ

   resolve_ua                скрейпінг Clarity. Движок його НЕ викликає
                             (правило 34). Лишений тільки для ручного
                             розбору окремої закупівлі.

 ЗАПУСКАТИ ЦЕЙ ФАЙЛ НЕ ТРЕБА. Він імпортується движком.
=============================================================================
"""
from __future__ import annotations

# ============================================================================
#  НАЛАШТУВАННЯ ПОШУКУ
# ============================================================================

# Що шукаємо: (префікс CPV, мінімальна очікувана вартість лоту, грн).
# Пороги — бізнес-фільтр, не закон.
CPV_RULES = [
    ("45",  1_500_000),      # будівництво
    ("712",   400_000),      # проєктування
    ("713",   400_000),
    ("715",   400_000),      # технагляд
    ("716",   400_000),
]

API = "https://public-api.prozorro.gov.ua/api/2.5/tenders"
FEED_PAGE = 100                  # закупівель на сторінку фіда
MAX_FEED_PAGES = 400             # запобіжник від нескінченного проходу
MAX_DOC_MB = 25                  # більший файл не качаємо
DOWNLOAD_PROTOCOL = True         # текст рішення замовника — джерело цитати
ANALYSE_BID = True               # дивитись склад пропозиції учасника
CASE_DIR = "sprava"              # куди складати завантажені документи
HTTP_TRIES = 4
HTTP_TIMEOUT = 60
INCLUDE_CLOSED_TENDERS = True
INCLUDE_NEGOTIATION = False
ZVERTANNYA_BEZ_PB = "лише_імя"   # без по батькові безпечніше звертатись на ім'я
GREETING_FALLBACK = "Добрий день!"

import csv, glob, json, math, os, re, shutil, subprocess, sys, time, unicodedata

# --- requests обовʼязковий: без нього немає доступу до Prozorro -------------
# Падаємо одразу і зрозуміло, а не NameError десь усередині виклику.
try:
    import requests
except ImportError as _exc:                                  # pragma: no cover
    raise ImportError(
        "Потрібна бібліотека requests. У Colab виконайте клітинку 1, "
        "або локально: pip install requests"
    ) from _exc

# --- решта необовʼязкові ----------------------------------------------------
# Без них частина документів не прочитається (read_text поверне порожньо),
# але пошук відхилень і розбір претензій працюють. Движок у такому разі
# візьме підставу з опису рішення і позначить це в source_locator.
try:
    from pypdf import PdfReader
except ImportError:                                          # pragma: no cover
    PdfReader = None
try:
    import docx as docxlib
except ImportError:                                          # pragma: no cover
    docxlib = None
try:
    import openpyxl
except ImportError:                                          # pragma: no cover
    openpyxl = None

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Часовий пояс. У монолiті тут стояв фіксований timezone(timedelta(hours=3)).
# Наприкінці жовтня Україна переходить на UTC+2, і фіксований зсув зсуває
# межі доби та строки оскарження на годину. ZoneInfo це враховує.
KYIV = ZoneInfo("Europe/Kyiv")


NORMA = {
    # ключ                : як це буде виглядати в листі та звіті
    "особливості"  : "Особливостей, затверджених постановою КМУ від 12.10.2022 № 1178",
    "особливості_к": "Особливостей № 1178",
    "п24год"       : "пункт 43 Особливостей",
    "п24год_р"     : "пункту 43 Особливостей",
    "пвідхилення"  : "пункт 44 Особливостей",
    "пвідхилення_р": "пункту 44 Особливостей",
    "пст17"        : "пункт 47 Особливостей",
    "пст17_р"      : "пункту 47 Особливостей",
    "закон"        : "Закону України «Про публічні закупівлі»",
    "ст22"         : "стаття 22 Закону України «Про публічні закупівлі»",
    "ст16"         : "стаття 16 Закону України «Про публічні закупівлі»",
    "плата"        : "постановою КМУ від 22.04.2020 № 292",
    "п46"          : "пункт 46 Особливостей",
    "п46_р"        : "пунктом 46 Особливостей",
    "п56"          : "пункт 56 Особливостей",
    "п56_р"        : "пунктом 56 Особливостей",
    "п59"          : "пункт 59 Особливостей",
    "п59_р"        : "пунктом 59 Особливостей",
    "остання_зміна": "ПКМУ від 15.07.2026 № 957 (чинна з 01.09.2026)",
}



ONLY_OPEN_WINDOW = False  # True = пропускати ті, де вікно скарги вже закрите



WORKERS = 8

PRICE_FROM_LOT = True   # True = рахувати від вартості ЛОТУ (правильно),





CLARITY = "https://clarity-project.info/tender/{ua}"

KEEP_STATUSES = {"active.pre-qualification", "active.pre-qualification.stand-still",
                 "active.qualification", "active.qualification.stand-still",
                 "active.awarded"}

KEEP_TYPES = {"aboveThreshold", "aboveThresholdUA", "aboveThresholdEU",
              "competitiveDialogueUA", "competitiveDialogueEU",
              "competitiveDialogueUA.stage2", "competitiveDialogueEU.stage2",
              "esco", "closeFrameworkAgreementUA"}

_KEEP_CHARS = {"№": "\ue000", "₴": "\ue001", "©": "\ue002", "™": "\ue003"}

def clean_spaces(s):
    t = str(s or "")
    for ch, holder in _KEEP_CHARS.items():
        t = t.replace(ch, holder)
    t = unicodedata.normalize("NFKC", t)
    for ch, holder in _KEEP_CHARS.items():
        t = t.replace(holder, ch)
    return re.sub(r"\s+", " ", t).strip()

def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s))
    except ValueError:
        try: return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except ValueError: return None

RULES = [
    # ВАЖЛИВО: R04 звужено навмисно. У протоколах майже завжди є фраза
    # «розміщує повідомлення з вимогою про усунення невідповідностей» — це
    # цитата норми, а не претензія. Тому потрібне заперечення поруч.
    ("R04", f"Не усунуто невідповідності за 24 год ({NORMA['п24год']})",
     r"(?:не\s+усун\w+|не\s+виправ\w+|не\s+завантаж\w+|не\s+надав\w*)[^.;]{0,90}невідповідн"
     r"|невідповідн[^.;]{0,90}(?:не\s+усун\w+|не\s+виправ\w+|не\s+булo?\s+усунут)"
     r"|протягом\s+24\s*(?:-х|-ти)?\s*годин[^.;]{0,80}не\s+"
     r"|24\s*(?:-х|-ти)?\s*годин[^.;]{0,40}(?:не\s+усун|не\s+виправ|не\s+наді)"),
    ("R08", "Конфіденційність інформації", r"конфіденцій"),
    ("R06", "Забезпечення пропозиції / банківська гарантія",
     r"забезпеченн\w*\s+тендерн|банківськ\w*\s+гарант|гарантійн\w*\s+лист"),
    ("R07", "Аномально низька ціна", r"аномальн\w*\s+низьк|\bАНЦ\b"),
    # УВАГА: сюди НЕ можна вносити «додаток N» — посилання на додаток є майже
    # в кожній претензії будь-якої підстави, і R03 забирав би все підряд.
    ("R03", "Невідповідність техспецифікації / ДБН / кошторису",
     r"технічн\w*\s+(специфікац|вимог|завданн)|дефектн\w*\s+акт|кошторис"
     r"|\bДБН\b|\bДСТУ\b|обсяг\w*\s+робіт|відомост\w*\s+обсяг"),
    ("R09", "Ліцензія / дозвіл / сертифікат", r"ліценз|дозвільн|дозвіл\b|сертифікат\w*\s+відповідн"),
    ("R01", "Кваліфікаційні критерії (досвід, МТБ, працівники)",
     r"кваліфікаційн|аналогічн\w*\s+договор|матеріально[-\s]техн|працівник|обладнанн|досвід\s+виконанн"),
    ("R02", f"Підстави ст. 17 Закону / {NORMA['пст17']}",
     r"стат\w*\s*17|пункт\w*\s*47|п\.?\s*47\s+Особливост|судим|корупц|банкрут|заборгован\w*\s+(зі|з)\s+сплат"),
    ("R05", "Недостовірна інформація", r"недостовірн"),
    ("R11", "Пропозиція іншою мовою", r"інш\w*\s+мов|не\s+українськ\w*\s+мов"),
    ("R12", "Сплив строк дії пропозиції", r"строк\w*\s+ді[її]\s+тендерн\w*\s+пропозиц"),
    ("R13", "Відмова від підписання договору", r"відмов\w*\s+від\s+підписанн|не\s+підписа\w*\s+договор"),
    ("R14", "Немає забезпечення виконання договору", r"забезпеченн\w*\s+виконанн\w*\s+договор"),
    ("R10", "Ненадання документа / формальна невідповідність",
     r"не\s+надав|не\s+нада\w+|ненаданн|не\s+заванта\w+|відсутн\w*\s+документ|не\s+містить|не\s+відповіда"),
]

COMPILED = [(c, l, re.compile(p, re.IGNORECASE)) for c, l, p in RULES]

def classify(text):
    t = text or ""
    for code, label, rx in COMPILED:
        if rx.search(t): return code, label
    return "R99", "Не класифіковано — потрібен ручний перегляд"

BASE_PROSPECT = {"R04": 0.72, "R08": 0.70, "R10": 0.55, "R03": 0.52, "R06": 0.50,
                 "R01": 0.45, "R11": 0.45, "R09": 0.40, "R07": 0.40, "R05": 0.30,
                 "R02": 0.25, "R12": 0.25, "R14": 0.22, "R13": 0.10, "R99": 0.35}

def read_text(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            d = docxlib.Document(path)
            parts = [p.text for p in d.paragraphs]
            for tbl in d.tables:
                for r in tbl.rows: parts.append(" | ".join(c.text for c in r.cells))
            return "\n".join(parts)
        if ext == ".pdf":
            return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
        if ext in (".xlsx", ".xlsm"):
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            parts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    parts.append(" | ".join("" if c is None else str(c) for c in row))
            wb.close(); return "\n".join(parts)
        if ext in (".txt", ".csv"):
            return open(path, encoding="utf-8", errors="ignore").read()
    except Exception: pass
    return ""

def lot_amount(t, award):
    """Очікувана вартість ЛОТУ, до якого належить відхилення.

    У багатолотовій закупівлі і плата до АМКУ, і наша ціна рахуються від лоту,
    а не від усього тендера. Якщо лот не визначено — повертаємо суму тендера.
    """
    total = (t.get("value") or {}).get("amount") or 0
    if not PRICE_FROM_LOT:
        return total
    lot_id = award.get("lotID") or award.get("lotId")
    if not lot_id:
        bid_id = award.get("bid_id") or award.get("bidID")
        for b in (t.get("bids") or []):
            if b.get("id") == bid_id:
                for lv in (b.get("lotValues") or []):
                    if lv.get("relatedLot"):
                        lot_id = lv.get("relatedLot")
                        break
            if lot_id:
                break
    if lot_id:
        for lot in (t.get("lots") or []):
            if lot.get("id") == lot_id:
                a = (lot.get("value") or {}).get("amount")
                if a:
                    return a
    return total

VIOLATION_RE = re.compile(
    r"не\s+надав|не\s+надано|не\s+підтверд|не\s+відповіда|не\s+містить|не\s+завантаж|"
    r"не\s+усун|не\s+виправ|не\s+заповн|не\s+зазначен|не\s+долучен|не\s+підпис|"
    r"не\s+дотрим|не\s+враховано|не\s+передбач|не\s+заванта|відсутн|порушен|"
    r"суперечи|розбіжн|неповн|недостовірн|виявлен\w*\s+невідповідн", re.IGNORECASE)

BOILERPLATE_RE = re.compile(
    r"продовженн\w*\s+строку\s+розгляд|керуючись"
    r"|замовник\s+розміщу\w*\s+повідомленн|оприлюднює\s+повідомленн"
    r"|уповноважен\w*\s+особ\w*\s+прийня|розглянувши\s+тендерн"
    r"|на\s+підставі\s+викладеного"
    # цитата пункту 46 про порядок оприлюднення рішення — це опис процедури,
    # а не претензія до учасника (трапляється майже в кожному протоколі)
    r"|інформація\s+про\s+відхилення\s+тендерної\s+пропозиції"
    r"|оприлюднюється\s+в\s+електронній\s+системі"
    r"|автоматично\s+надсилається\s+учасник"
    r"|розгляд\s+(?:друг|перш|трет)\w*\s+питання\s+порядку\s+денного",
    re.IGNORECASE)

NORM_ONLY_RE = re.compile(
    r"^(?:відповідно\s+до|згідно\s+з|на\s+виконанн|у\s+зв.язку\s+із\s+тим)", re.IGNORECASE)

_ABBR_RE = re.compile(
    r"\b(п|пп|ст|абз|розд|гл|ч|ін|рр|р|тис|млн|грн|табл|мал|рис|дод|вул|обл)\.",
    re.IGNORECASE)

def _protect_dots(t):
    """Ховає крапки у скороченнях і числах, щоб не різати речення посеред «п. 5.2»."""
    t = _ABBR_RE.sub(lambda m: m.group(1) + "\x00", t)
    return re.sub(r"(\d)\.(?=\d)", "\\1\x00", t)

def split_claims(text, max_claims=25):
    """Ріже текст протоколу на окремі претензії."""
    t = clean_spaces(text)
    if not t:
        return []
    t = _protect_dots(t)
    # Ріжемо за кінцем речення, за нумерацією виду «2)» і за маркерами списку.
    # Нумерацію виду «2.» окремо не ловимо: інакше рвалося б «Додатку 3.» —
    # такі пункти й так відділяються крапкою попереднього речення.
    parts = re.split(r"(?:(?<=[.;])\s+)|(?:\s+(?=\d{1,2}\)\s))|(?:\s*[•‒–—]\s+)", t)
    claims, seen = [], set()
    for p in parts:
        s = clean_spaces(p).replace("\x00", ".")
        if len(s) < 25 or len(s) > 900:
            continue
        if BOILERPLATE_RE.search(s):
            continue
        if NORM_ONLY_RE.match(s) and not re.search(
                r"не\s+надав|не\s+надано|відсутн|не\s+відповіда|не\s+містить", s, re.I):
            continue
        if not VIOLATION_RE.search(s):
            continue
        k = s.lower()[:120]
        if k in seen:
            continue
        seen.add(k)
        claims.append(s)
        if len(claims) >= max_claims:
            break
    return claims

def classify_claims(claims, fallback_text):
    """Класифікує кожну претензію окремо. Повертає список (код, назва, текст)."""
    out = []
    for c in claims:
        code, label = classify(c)
        out.append((code, label, c))
    if not out:                       # нічого не вирізалось — класифікуємо весь блок
        code, label = classify(fallback_text)
        out.append((code, label, clean_spaces(fallback_text)[:600]))
    return out

def main_ground(claim_list):
    """Головна підстава = найчастіша; за нічиєї — з найвищою базовою перспективою."""
    cnt = Counter(c for c, _, _ in claim_list)
    top = max(cnt.values())
    best = [c for c, n in cnt.items() if n == top]
    best.sort(key=lambda c: -BASE_PROSPECT.get(c, 0.35))
    code = best[0]
    label = next(l for c, l, _ in claim_list if c == code)
    return code, label

def bid_docs_for(tender, bid_id):
    """Список документів пропозиції конкретного учасника."""
    for b in (tender.get("bids") or []):
        if bid_id and b.get("id") != bid_id:
            continue
        docs = b.get("documents") or []
        for lot in (b.get("lotValues") or []):
            docs += lot.get("documents") or []
        if docs or (bid_id and b.get("id") == bid_id):
            return docs, b
    bids = tender.get("bids") or []
    return ((bids[0].get("documents") or []), bids[0]) if bids else ([], {})

def make_session():
    s = requests.Session()
    s.headers.update({"Accept": "application/json,text/html",
                      "User-Agent": "day-monitor-and-analyzer/3.0"})
    return s

def get_json(session, url, params=None, tries=4):
    for a in range(tries):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 200: return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (a + 1)); continue
            return None
        except Exception: time.sleep(2 * (a + 1))
    return None

def get_bytes(session, url, tries=3):
    for a in range(tries):
        try:
            r = session.get(url, timeout=120)
            if r.status_code == 200: return r.content
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (a + 1)); continue
            return None
        except Exception: time.sleep(2 * (a + 1))
    return None

def resolve_ua(session, ua):
    try:
        r = session.get(CLARITY.format(ua=ua), timeout=60)
        if r.status_code != 200: return None
        for cand in dict.fromkeys(re.findall(r"\b[0-9a-f]{32}\b", r.text)):
            t = (get_json(session, f"{API}/{cand}") or {}).get("data")
            if t and t.get("tenderID") == ua: return t
    except Exception: pass
    return None

def download_protocol(session, award, folder):
    """Качає протокол розгляду/відхилення з документів award і віддає його текст.
    Саме там замовник викладає конкретику, якої немає в короткому description."""
    os.makedirs(folder, exist_ok=True)
    texts = []
    docs = award.get("documents") or []
    # Спершу пробуємо документи з «говорящою» назвою, потім — усі інші текстові.
    # Раніше бралися лише перші, і якщо замовник назвав файл інакше
    # («Обгрунтування.pdf», «Лист.docx», «б/н.pdf») — конкретика губилася.
    def rank(d):
        low = clean_spaces(d.get("title") or "").lower()
        return 0 if any(w in low for w in ("протокол", "рішенн", "відхил", "розгляд",
                                           "обґрунт", "обгрунт", "повідомл")) else 1
    for d in sorted(docs, key=rank):
        title = clean_spaces(d.get("title") or "")
        low = title.lower()
        fmt = (d.get("format") or "").lower()
        if low.endswith(".p7s") or "sign" in low or "pkcs7" in fmt:   # підписи
            continue
        if any(low.endswith(e) for e in (".yaml", ".json", ".xml", ".zip", ".rar")):
            continue
        if not any(x in fmt for x in ("pdf", "wordprocessing", "msword", "text")) \
                and not any(low.endswith(e) for e in (".pdf", ".docx", ".doc", ".txt")):
            continue
        url = d.get("url")
        if not url:
            continue
        safe = re.sub(r"[^\w\d.\-() ]", "_", title)[:90] or "protokol"
        if not os.path.splitext(safe)[1]:
            fmt = (d.get("format") or "").lower()
            safe += (".pdf" if "pdf" in fmt else ".docx" if "wordprocessing" in fmt
                     else ".bin")
        path = os.path.join(folder, safe)
        if not os.path.exists(path):
            raw = get_bytes(session, url)
            if not raw or len(raw) > MAX_DOC_MB * 1024 * 1024:
                continue
            try:
                open(path, "wb").write(raw)
            except Exception:
                continue
        t = read_text(path)
        if len(t) > 200:
            texts.append(t)
        elif rank(d) == 0:
            # протокол є, але тексту немає — майже напевно скан
            texts.append(f"[!] Файл «{title}» схоже є сканом: текст не витягується. "
                         f"Відкрийте вручну: {os.path.abspath(path)}")
        if len(texts) >= 3:
            break
    return "\n".join(texts)

def cpv_rule_for(t):
    best = None
    for it in t.get("items", []):
        code = str(((it.get("classification") or {}).get("id")) or "")
        for pref, minv in CPV_RULES:
            if code.startswith(pref) and (best is None or len(pref) > len(best[0])):
                best = (pref, minv)
    return best

def first_cpv(t):
    for it in t.get("items", []):
        c = (it.get("classification") or {}).get("id")
        if c: return str(c)
    return ""

def tenderer_by_bid(t, bid_id):
    for b in t.get("bids", []):
        if b.get("id") == bid_id and b.get("tenderers"): return b["tenderers"][0]
    return {}

def rejection_time(a):
    """Коли рішення про відхилення потрапило в систему.

    Поле «date» в award оновлюється не завжди й не всюди однаково. Для строку
    оскарження першоджерелом є початок періоду оскарження — момент, коли
    рішення оприлюднено. Тому дивимось на кілька полів і беремо найранішу
    достовірну позначку часу, а не одне «date».
    """
    cands = [a.get("date"), (a.get("complaintPeriod") or {}).get("startDate")]
    for d in (a.get("documents") or []):
        cands.append(d.get("datePublished"))
    times = [parse_dt(x) for x in cands]
    times = [x for x in times if x]
    return min(times) if times else None

def rejection_times(a):
    """Усі позначки часу цього рішення — для перевірки «чи потрапляє в день»."""
    out = [parse_dt(a.get("date")),
           parse_dt((a.get("complaintPeriod") or {}).get("startDate"))]
    for d in (a.get("documents") or []):
        out.append(parse_dt(d.get("datePublished")))
    return [x for x in out if x]

def all_rejections(t):
    """ВСІ відхилені пропозиції тендера — і після оцінки, і на прекваліфікації.

    Це головна вибірка. Одне відхилення в закупівлі — рідкість: замовник, який
    відхиляє за формальною підставою, зазвичай відхиляє кількох. Пропустити
    когось означає не просто втратити ліда, а дати неповну картину по справі.
    """
    out = []
    for src in ("awards", "qualifications"):
        for a in (t.get(src) or []):
            if a.get("status") == "unsuccessful":
                out.append((src, a))
    # у межах однієї закупівлі два рішення можуть стосуватися одного bid
    # (перевідхилення після скарги) — лишаємо обидва, але не дублікати об'єктів
    seen, uniq = set(), []
    for src, a in out:
        k = a.get("id") or f"{src}:{a.get('bid_id') or a.get('bidID')}:{a.get('date')}"
        if k in seen:
            continue
        seen.add(k)
        uniq.append((src, a))
    return uniq

def day_rejections(t, day_start, day_end, now_utc):
    """Відхилення цього тендера, що потрапляють у день. Список (src, award).

    Рішення вважається «сьогоднішнім», якщо в межі дня потрапляє БУДЬ-ЯКА з
    його позначок часу: дата рішення, початок періоду оскарження або дата
    публікації протоколу. Раніше дивились лише на «date» — і рішення, у якому
    це поле відставало, у вибірку не потрапляло.
    """
    if not cpv_rule_for(t): return []
    amount = (t.get("value") or {}).get("amount") or 0
    if amount < cpv_rule_for(t)[1]: return []
    out = []
    for src, a in all_rejections(t):
        if not any(day_start <= x < day_end for x in rejection_times(a)):
            continue
        if ONLY_OPEN_WINDOW:
            end = parse_dt((a.get("complaintPeriod") or {}).get("endDate"))
            if not end or end <= now_utc: continue
        out.append((src, a))
    return out

def scan_day(session, day_start, day_end, now_utc):
    """Повертає {uuid: tender} для тендерів, де в цей день були відхилення."""
    offset = str(int(day_start.timestamp()))
    cands, events, pages = [], 0, 0
    print("\nКРОК 1/3. Читаю стрічку змін Prozorro...")
    while pages < 400:
        j = get_json(session, API, {"limit": 1000, "offset": offset,
                                    "opt_fields": "status,procurementMethodType"})
        if not j or not j.get("data"): break
        rows = j["data"]; events += len(rows)
        for t in rows:
            if (t.get("procurementMethodType") in KEEP_TYPES
                    and t.get("status") in KEEP_STATUSES): cands.append(t["id"])
        nxt = (j.get("next_page") or {}).get("offset"); pages += 1
        if pages % 25 == 0:
            print(f"    сторінка {pages}: подій {events:,}, кандидатів {len(cands):,}")
        if not nxt or len(rows) < 1000: break
        offset = nxt
    cands = list(dict.fromkeys(cands))
    print(f"    подій: {events:,} • тендерів до перевірки: {len(cands):,}")

    print(f"\nКРОК 2/3. Перевіряю тендери ({WORKERS} потоків)...")
    keep, done = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(get_json, session, f"{API}/{u}"): u for u in cands}
        for fut in as_completed(futs):
            done += 1
            t = (fut.result() or {}).get("data")
            if t and day_rejections(t, day_start, day_end, now_utc):
                keep[t.get("id") or futs[fut]] = t
            if done % 500 == 0:
                print(f"    перевірено {done}/{len(cands)} • з відхиленнями: {len(keep)}")
    print(f"    перевірено {done} • закупівель з відхиленнями за день: {len(keep)}")
    return keep

_M = """Олександр Андрій Сергій Володимир Микола Іван Василь Віктор Юрій Петро
Олег Ігор Дмитро Максим Роман Тарас Богдан Анатолій Валерій Віталій Євген
Леонід Михайло Павло Ростислав Руслан Станіслав Степан Артем Артур Борис Вадим
Валентин В'ячеслав Вячеслав Гліб Григорій Данило Денис Едуард Ілля Йосип Кирило
Костянтин Лев Любомир Марко Мирослав Назар Остап Пилип Тимофій Федір Ярослав
Юліан Аркадій Геннадій Георгій Захар Матвій Мар'ян Марян Назарій Олексій Орест
Платон Антон Богуслав Броніслав Веніамін Владислав Всеволод Гнат Давид Демид
Зіновій Ігнат Йосиф Казимир Клим Корній Лаврентій Лука Макар Мирон Нестор
Никифор Опанас Панас Прохор Родіон Святослав Семен Сидір Тимур Трохим Устим
Хома Юхим Яків Ян Ярема Едвард Еміль Ераст Ефрем Аскольд Йван Валер'ян Северин
Тимофій Феодосій Філіп Христофор Юстин Адам Альберт Веніамін Гордій Данила
Даніїл Дан Еліас Ілларіон Іларіон Йона Йосія Клавдій Корнелій Кузьма Лазар
Леонтій Модест Наум Никодим Олелько Пантелеймон Порфирій Радислав Ратібор
Сава Самійло Самуїл Севастян Соломон Спиридон Тадей Терентій Тихон Фадей
Феліксий Фелікс Харитон Юрко Яромир Ярополк"""

_F = """Олена Наталія Наталя Ірина Тетяна Оксана Людмила Світлана Марія Галина
Валентина Ольга Анна Ганна Юлія Катерина Вікторія Надія Любов Лідія Ніна Алла
Раїса Зоя Віра Вероніка Дарія Дарина Діана Євгенія Жанна Зінаїда Інна Каріна
Карина Клавдія Христина Крістіна Лариса Леся Ліана Ліля Лілія Маргарита Марина
Мар'яна Маряна Мирослава Мілана Неля Нелля Олеся Поліна Роксолана Руслана
Соломія Софія Тамара Уляна Ульяна Ярослава Яна Аліна Альона Анастасія Ангеліна
Анжела Богдана Валерія Василина Віолетта Влада Владислава Емілія Ілона Інеса
Мілена Олександра Орися Павліна Сніжана Таїсія Яніна Агнеса Ада Аделіна Алевтина
Альбіна Амалія Антоніна Аеліта Богуслава Броніслава Ванда Варвара Віталіна
Галя Дана Даная Ділара Домініка Єва Єлизавета Злата Іванна Іларія Інга Іолана
Іраїда Ірма Кароліна Квітослава Клара Ксенія Лада Лана Лілія Ліна Любава
Людмила Магдалина Мальва Марта Марфа Меланія Мотря Нонна Одарка Оксанія Октябрина
Ореста Пелагея Регіна Рената Римма Роза Роксана Румія Саломея Сільвія Слава
Соня Стелла Стефанія Тереза Тея Устина Фаїна Феодосія Христя Юстина Ядвіга"""

MALE_NAMES = {w.strip().lower() for w in _M.split() if w.strip()}

FEMALE_NAMES = {w.strip().lower() for w in _F.split() if w.strip()}

ALL_NAMES = MALE_NAMES | FEMALE_NAMES

DIMIN = {"саша": "ч", "сашко": "ч", "льоша": "ч", "діма": "ч", "вова": "ч",
         "коля": "ч", "толя": "ч", "юра": "ч", "гриша": "ч", "міша": "ч",
         "паша": "ч", "сєва": "ч", "славік": "ч", "тарасик": "ч",
         "оля": "ж", "галя": "ж", "валя": "ж", "катя": "ж", "настя": "ж",
         "таня": "ж", "юля": "ж", "ліда": "ж", "люда": "ж", "света": "ж",
         "надя": "ж", "маша": "ж", "даша": "ж", "саня": "ч", "женя": "ч"}

NOT_PERSON = re.compile(
    r"тендерн|комітет|відділ|бухгалт|управлінн|департамент|секретар|приймальн|"
    r"канцеляр|уповноважен\w*\s+особ$|дирекц|адміністрац|служб|товариств|"
    r"підприємств|організац|компані|group|ltd|llc|отдел", re.IGNORECASE)

PATRONYMIC_RE = re.compile(
    r"^[А-ЯІЇЄҐа-яіїєґ'’\-]{3,}(ович|йович|евич|ьович|ич|івна|ївна|івна|ична|инічна|іївна)$",
    re.IGNORECASE)

FEM_PATR = re.compile(r"(івна|ївна|ична|инічна|іївна)$", re.IGNORECASE)

SURNAME_HINT = re.compile(
    r"(енко|ук|юк|чук|ський|цький|зький|ська|цька|зька|ов|ев|єв|ін|ина|іна|"
    r"ко|ак|як|ик|ець|чак|шин|няк|ий|ій|ая|ва|ло|ба|да|ра|ха|ша|ня)$",
    re.IGNORECASE)

_LAT2CYR_NAME = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "I": "І", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "i": "і", "o": "о", "p": "р", "x": "х",
    "y": "у"})

VOC_FIRST_OVERRIDE = {
    "олег": "Олегу", "ігор": "Ігорю", "лазар": "Лазарю", "федір": "Федоре",
    "сидір": "Сидоре", "яків": "Якове", "антін": "Антоне", "лев": "Леве",
    "марко": "Марку", "ілля": "Ілле", "лука": "Луко", "сава": "Саво",
    "хома": "Хомо", "кузьма": "Кузьмо", "микита": "Микито", "гнат": "Гнате",
    "юрко": "Юрку", "данило": "Даниле", "кирило": "Кириле", "михайло": "Михайле",
    "павло": "Павле", "дмитро": "Дмитре", "петро": "Петре", "самійло": "Самійле",
    "любов": "Любове", "нінель": "Нінеле", "мотря": "Мотре", "марфа": "Марфо",
    "леся": "Лесю", "орися": "Орисю", "христя": "Христе", "сара": "Саро",
}

VOC_SURNAME_OVERRIDE = {}          # напр. {"кравець": "Кравцю"}

def _cap(w):
    """Правильна велика літера з урахуванням дефіса та апострофа."""
    if not w:
        return w
    parts = re.split(r"([-'’])", w.lower())
    return "".join(p if p in "-'’" else (p[:1].upper() + p[1:]) for p in parts)

def voc_first_male(n):
    low = n.lower()
    if low in VOC_FIRST_OVERRIDE:
        return VOC_FIRST_OVERRIDE[low]
    if low.endswith("я"):                       # Ілля, Мусія
        return n[:-1] + "е"
    if low.endswith("а"):                       # Микола, Сава, Кузьма
        return n[:-1] + "о"
    if low.endswith("ко"):                      # Марко, Юрко, Івасько
        return n[:-1] + "у"
    if low.endswith("о"):                       # Петро, Дмитро, Павло
        return n[:-1] + "е"
    if low.endswith("й"):                       # Андрій, Сергій, Віталій
        return n[:-1] + "ю"
    if low.endswith("ь"):                       # Василь, Ігорь
        return n[:-1] + "ю"
    if low.endswith(("г", "ґ", "к", "х")):      # Олег, Марк, Кирик
        return n + "у"
    if low.endswith(("ж", "ч", "ш", "щ")):      # мішана група
        return n + "у"
    return n + "е"                              # Іван, Тарас, Богдан, Фізер

def voc_first_female(n):
    low = n.lower()
    if low in VOC_FIRST_OVERRIDE:
        return VOC_FIRST_OVERRIDE[low]
    if re.search(r"[аеиіоуяюєї]я$", low):       # Марія, Наталія, Софія, Надія
        return n[:-1] + "є"
    if low.endswith("я"):                       # Галя, Оля, Настя, Таня
        return n[:-1] + "ю"
    if low.endswith("а"):                       # Олена, Ірина, Ольга, Ганна
        return n[:-1] + "о"
    return n                                    # незмінне (Нінель, Кармен)

def voc_patronymic(p, female=None):
    low = p.lower()
    fem = FEM_PATR.search(low) if female is None else female
    if fem:
        if low.endswith("на"):                  # Іванівна -> Іванівно
            return p[:-1] + "о"
        return p
    if low.endswith("ич"):                      # Іванович -> Івановичу, Ілліч -> Іллічу
        return p + "у"
    return p

def voc_surname_male(s):
    low = s.lower()
    if low in VOC_SURNAME_OVERRIDE:
        return VOC_SURNAME_OVERRIDE[low]
    if low.endswith(("ий", "ій", "их", "ово", "аго", "ко́")):
        return s                                # прикметникові — не змінюються
    if low.endswith("ець"):                     # Кравець -> Кравцю
        return s[:-3] + "цю"
    if low.endswith("ко"):                      # Шевченко -> Шевченку, Бойко -> Бойку
        return s[:-1] + "у"
    if low.endswith("о"):
        # Діногло, Кайдо, Матео — здебільшого не українського кореня.
        # Відмінювати навмання не варто: «Діногле» читається як помилка.
        return s
    if low.endswith("а"):                       # Сорока -> Сороко, Кучма -> Кучмо
        return s[:-1] + "о"
    if low.endswith("я"):                       # Гмиря -> Гмире
        return s[:-1] + "е"
    if low.endswith(("і", "у", "ю", "е", "є", "ї")):
        return s                                # Півторадні, Леле — не змінюємо
    if low.endswith("ь"):                       # Коваль -> Ковалю
        return s[:-1] + "ю"
    if low.endswith("й"):                       # Соловей — залишаємо
        return s
    if low.endswith(("г", "ґ", "к", "х")):      # Мельник -> Мельнику, Ковальчук -> у
        return s + "у"
    if low.endswith(("ж", "ч", "ш", "щ", "ц")):  # Ткач -> Ткачу
        return s + "у"
    return s + "е"                              # Фізер -> Фізере, Бондар -> Бондаре

def voc_surname_female(s):
    """Жіночі прізвища в кличному, як правило, лишаються без змін."""
    return s

FOP_RE = re.compile(
    r"^\s*(?:ФОП|Ф\s*О\s*П|ФО-П|фізичн\w*\s+особ\w*[\s\-—]*підприєм\w*|"
    r"приватн\w*\s+підприєм\w*)\s*[«\"']?", re.IGNORECASE)

def is_fop(company_name):
    return bool(FOP_RE.match(company_name or ""))

def _clean_person(raw):
    s = str(raw or "")
    s = s.translate(_LAT2CYR_NAME)
    s = re.sub(r"[«»\"'`]", " ", s)
    s = FOP_RE.sub(" ", s)
    s = re.sub(r"\b(директор|керівник|головний|бухгалтер|менеджер|інженер|"
               r"уповноважен\w*|особа|представник|заступник|тел|моб|e-?mail)\b\.?",
               " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[^А-ЯІЇЄҐа-яіїєґ'’\-\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_pib(raw, company_name=""):
    """Розбирає рядок на прізвище / ім'я / по батькові + стать.

    Повертає dict: {'прізвище','імя','по_батькові','стать','повне','є_особа'}
    стать: 'ч' | 'ж' | '' (невідомо)
    """
    empty = {"прізвище": "", "імя": "", "по_батькові": "", "стать": "",
             "повне": "", "є_особа": False}
    s = _clean_person(raw)
    if not s and is_fop(company_name):
        s = _clean_person(company_name)
    if not s:
        return empty
    if NOT_PERSON.search(s):
        return empty

    toks = [t for t in s.split() if len(t) >= 2]
    if not (1 <= len(toks) <= 4):
        return empty
    toks = [_cap(t) for t in toks[:3]]

    patr = name = surn = ""
    rest = []
    for t in toks:
        if not patr and PATRONYMIC_RE.match(t):
            patr = t
        else:
            rest.append(t)

    # 1) ім'я — за словником
    for t in list(rest):
        if t.lower() in ALL_NAMES or t.lower() in DIMIN:
            name = t
            rest.remove(t)
            break
    # 2) якщо словник не спрацював — позиційні евристики
    if not name and rest:
        if len(rest) == 2:
            a, b = rest
            if SURNAME_HINT.search(a) and not SURNAME_HINT.search(b):
                surn, name = a, b
            elif SURNAME_HINT.search(b) and not SURNAME_HINT.search(a):
                name, surn = a, b
            else:
                surn, name = (a, b) if patr else (b, a)
            rest = []
        elif len(rest) == 1:
            name = rest.pop(0) if patr else ""
            if not name:
                surn = toks[0]
    if rest and not surn:
        surn = rest[0]

    # стать
    gender = ""
    if patr:
        gender = "ж" if FEM_PATR.search(patr) else "ч"
    elif name:
        ln = name.lower()
        if ln in MALE_NAMES:
            gender = "ч"
        elif ln in FEMALE_NAMES:
            gender = "ж"
        elif ln in DIMIN:
            gender = DIMIN[ln]
    if not gender and surn:
        gender = "ж" if re.search(r"(ська|цька|зька|ова|єва|іна|ина|а)$",
                                  surn.lower()) else "ч"

    ok = bool(name) and (bool(patr) or bool(surn) or name.lower() in ALL_NAMES)
    return {"прізвище": surn, "імя": name, "по_батькові": patr, "стать": gender,
            "повне": " ".join(x for x in (surn, name, patr) if x),
            "є_особа": ok}



def greeting(pib, fallback_company=""):
    """Звертання в кличному відмінку. «Шановний Іване Петровичу!»"""
    name, patr, surn = pib.get("імя"), pib.get("по_батькові"), pib.get("прізвище")
    fem = pib.get("стать") == "ж"
    hello = "Шановна" if fem else "Шановний"
    if not name:
        if surn:                               # знаємо лише прізвище
            vs = voc_surname_female(surn) if fem else voc_surname_male(surn)
            return f"{hello} {'пані' if fem else 'пане'} {vs}!"
        return GREETING_FALLBACK
    vn = voc_first_female(name) if fem else voc_first_male(name)
    if patr:                                   # повне ПІБ -> ім'я + по батькові
        return f"{hello} {vn} {voc_patronymic(patr, fem)}!"
    if surn and ZVERTANNYA_BEZ_PB == "імя_прізвище":
        vs = voc_surname_female(surn) if fem else voc_surname_male(surn)
        return f"{hello} {vn} {vs}!"
    # без по батькові безпечніше звертатися лише на ім'я: рідкісне прізвище
    # легко зіпсувати відмінюванням, а зіпсоване прізвище в першому ж рядку
    # холодного листа коштує дорожче, ніж трохи менша офіційність
    return f"{hello} {vn}!"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

def norm_phone(raw):
    """+380XXXXXXXXX; кілька номерів — через кому."""
    out = []
    for chunk in re.split(r"[,;/]| або ", str(raw or "")):
        d = re.sub(r"\D", "", chunk)
        if not d:
            continue
        if d.startswith("380") and len(d) == 12:
            out.append("+" + d)
        elif d.startswith("0") and len(d) == 10:
            out.append("+38" + d)
        elif len(d) == 9:
            out.append("+380" + d)
        elif len(d) >= 10:
            out.append("+" + d)
    return ", ".join(dict.fromkeys(out))

def norm_email(raw):
    found = EMAIL_RE.findall(str(raw or ""))
    return ", ".join(dict.fromkeys(x.lower() for x in found))

def participant_contacts(tender, award):
    """Максимально повні контакти відхиленого учасника."""
    sup = (award.get("suppliers") or [{}])[0]
    bid = {}
    bid_id = award.get("bid_id") or award.get("bidID")
    for b in (tender.get("bids") or []):
        if b.get("id") == bid_id:
            bid = b
            break
    tend = ((bid.get("tenderers") or [{}])[0]) if bid else {}

    def pick(field, *sources):
        for s in sources:
            v = (s or {}).get(field)
            if v:
                return v
        return ""

    cp_s = sup.get("contactPoint") or {}
    cp_t = tend.get("contactPoint") or {}
    name = pick("name", sup, tend)
    ident = pick("identifier", sup, tend) or {}
    addr = pick("address", sup, tend) or {}
    legal = (ident.get("legalName") or "") if isinstance(ident, dict) else ""

    person_raw = pick("name", cp_s, cp_t)
    pib = parse_pib(person_raw, name)
    if not pib["є_особа"] and is_fop(name):
        pib = parse_pib(name, name)

    return {
        "компанія": soft_clean(legal or name or ""),
        "ЄДРПОУ": (ident.get("id") or "") if isinstance(ident, dict) else "",
        "контактна_особа": pib["повне"] or (person_raw or "").strip(),
        "ПІБ": pib,
        "email": norm_email(pick("email", cp_s, cp_t)),
        "телефон": norm_phone(pick("telephone", cp_s, cp_t)),
        "адреса": ", ".join(x for x in [addr.get("postalCode"), addr.get("region"),
                                        addr.get("locality"),
                                        addr.get("streetAddress")] if x),
        "сайт": pick("url", cp_s, cp_t, sup, tend),
    }

CODE_HUMAN = {
    "R01": "невідповідність кваліфікаційним критеріям (досвід, працівники, "
           "матеріально-технічна база)",
    "R02": f"непідтвердження відсутності підстав, визначених {NORMA['пст17_р']}",
    "R03": "невідповідність пропозиції умовам технічної специфікації та іншим "
           "вимогам щодо предмета закупівлі",
    "R04": f"невиправлення виявлених замовником невідповідностей протягом 24 годин "
           f"({NORMA['п24год']})",
    "R05": "подання недостовірної інформації",
    "R06": "недоліки забезпечення тендерної пропозиції (банківської гарантії)",
    "R07": "аномально низьку ціну тендерної пропозиції",
    "R08": "порушення вимог щодо конфіденційності інформації",
    "R09": "відсутність або невідповідність ліцензії, дозволу чи сертифіката",
    "R10": "ненадання документа або формальну невідповідність вимогам "
           "тендерної документації",
    "R11": "подання документів іншою мовою",
    "R12": "сплив строку дії тендерної пропозиції",
    "R13": "відмову від підписання договору про закупівлю",
    "R14": "ненадання забезпечення виконання договору",
    "R99": "підставу, сформульовану замовником у довільній формі",
}

_PURE_NORM = re.compile(
    r"^(?:не\s+відповідає\s+умовам\s+технічної|не\s+відповідає\s+вимогам,\s*установлен"
    r"|згідно\s+з\s+(?:під)?пунктом|відповідно\s+до\s+(?:абзац|підпункт|пункт|част)"
    r"|замовник\s+відхиляє\s+тендерну\s+пропозицію\s+із\s+зазначенням"
    r"|пунктом\s+\d|тому,\s*пунктом)", re.IGNORECASE)

def claim_specificity(text):
    """Наскільки претензія конкретна (а не цитата норми). Більше — краще."""
    t = (text or "").strip()
    if not t:
        return -99
    score = 0
    if _PURE_NORM.match(t):
        score -= 6
    if re.search(r"«[^»]{3,}»", t):
        score += 3
    if re.search(r"(?:п\.|пункт\w*|додат\w*|розділ\w*|таблиц\w*)\s*№?\s*\d", t, re.I):
        score += 2
    if re.search(r"не\s+надав|не\s+підтверд|не\s+містить|відсутн|не\s+заванта", t, re.I):
        score += 2
    if re.search(r"довідк|сертифікат|ліценз|наказ|договор|акт\b|кошторис|відомість",
                 t, re.I):
        score += 2
    if re.search(r"\bучасник\w*\b", t, re.I):
        score += 1
    score += min(2, len(t) // 250)
    return score

def soft_clean(s):
    """Стискає пробіли, але зберігає «№», «—» та інші знаки (на відміну від NFKC)."""
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    return t.replace('"', "«", 1).replace('"', "»", 1) if t.count('"') == 2 else t



def dependency_status() -> dict:
    """Що встановлено, а що ні. Використовує ноутбук перед прогоном."""
    return {
        "requests": True,                       # без нього модуль не імпортувався б
        "pypdf (PDF-протоколи)": PdfReader is not None,
        "python-docx (DOCX)": docxlib is not None,
        "openpyxl (XLSX)": openpyxl is not None,
    }


# --- додано для службової картки: плата АМКУ і формат сум ---------------
# Ставки з ПКМУ № 292, від вартості ЛОТУ. Дослівно з оригіналу.
# Перевіряти при кожній зміні норм — див. NORMY_PEREVIRENO.
FEE_TD_RATE,  FEE_TD_MIN,  FEE_TD_MAX  = 0.003, 2_000, 85_000    # умови ТД
FEE_DEC_RATE, FEE_DEC_MIN, FEE_DEC_MAX = 0.006, 3_000, 170_000   # рішення замовника

def fee_amcu(amount, stage):
    """Плата за подання скарги. amount — очікувана вартість ЛОТУ."""
    if stage == "умови тендерної документації":
        return (int(min(FEE_TD_MAX, max(FEE_TD_MIN, amount * FEE_TD_RATE))),
                f"{FEE_TD_RATE:.1%}".replace(".", ","))
    return (int(min(FEE_DEC_MAX, max(FEE_DEC_MIN, amount * FEE_DEC_RATE))),
            f"{FEE_DEC_RATE:.1%}".replace(".", ","))

PLATA_STAVKA   = 0.003

PLATA_CAP_PDV  = 11_980.80      # те саме з ПДВ

PLATA_POVERT   = 2 / 3          # частка, яку повертає майданчик

def plata_za_podannya(v):
    """(повна плата з ПДВ, безповоротна частина) для пропозиції на суму v."""
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0
    povna = min(PLATA_CAP_PDV, v * PLATA_STAVKA * 1.2)
    return round(povna, 2), round(povna * (1 - PLATA_POVERT), 2)

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
             "серпня", "вересня", "жовтня", "листопада", "грудня"]

def ua_date(d):
    return f"{d.day} {UA_MONTHS[d.month - 1]} {d.year} р."

def money(x):
    """12345 -> «12 345». Нероздільний пробіл не ставимо: не всі поштові
    клієнти й CSV-імпорти його переживають."""
    try:
        return f"{int(round(float(x))):,}".replace(",", " ")
    except Exception:
        return str(x)

def plural_ua(n, one, few, many):
    """1 скарга · 2 скарги · 5 скарг — за останньою цифрою числа."""
    try:
        n = abs(int(round(float(n))))
    except Exception:
        return many
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many

def money_k(x):
    """11980.8 -> «11 980,80». З копійками — там, де сума береться з норми
    і клієнт може звірити її з квитанцією майданчика."""
    try:
        return f"{float(x):,.2f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(x)

def num_ua(x, znaky=1):
    """1606 -> «1 606»; 198.2 -> «198,2». Десятковий роздільник — кома."""
    try:
        v = float(x)
    except Exception:
        return str(x)
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}".replace(",", " ")
    return f"{v:,.{znaky}f}".replace(",", " ").replace(".", ",")
