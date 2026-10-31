"""
Mauritius news -> Telegram channel (Russian).

Checks RSS feeds, opens each new article, asks a free GitHub Models
LLM for a short Russian summary and posts it to a Telegram channel
with the photo and a link to the source. If the model is unavailable
(daily limit, error), falls back to a Google-translated headline.
Already-sent items are remembered in seen.json.
"""

import html
import json
import os
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import trafilatura
from deep_translator import GoogleTranslator

# --- Settings -------------------------------------------------------------

# Add more RSS feeds here: ("Source name", "feed URL")
# (name, url, kind, link pattern). kind: "rss" (default), "wp" (WordPress
# REST API, for sites whose RSS is broken) or "html" (read links straight
# from the site's page; the pattern says which links are articles).
FEEDS = [
    ("Defimedia", "https://defimedia.info/rss.xml"),
    ("L'Express", "https://lexpress.mu/", "html", r"^https://lexpress\.mu/s/[a-z0-9-]+-\d+$"),
    ("Le Mauricien", "https://www.lemauricien.com/wp-json/wp/v2/posts?per_page=20&_fields=id,date,title,link", "wp"),
    ("Inside News", "https://inside.news/feed/"),
    ("Wazaa", "https://www.wazaa.mu/feed/"),
    ("Top FM", "https://topfm.mu/", "html", r"^https://topfm\.mu/actualites/[a-z0-9-]{15,}$"),
    # ("ION News", "https://ionnews.mu/feed/"),  # works, but headlines only
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
MU_TZ = "+04:00"  # Mauritius time, for sites that give dates without a zone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]   # from @BotFather
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # e.g. @my_mauritius_news
# Optional: your personal Telegram id for service alerts (from @userinfobot).
# You must press Start in your bot once, or Telegram won't let it write to you.
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()

# Bot state lives in a separate "state" branch, checked out into STATE_DIR
STATE_DIR = Path(os.environ.get("STATE_DIR", "."))
SEEN_FILE = STATE_DIR / "seen.json"
STATE_FILE = STATE_DIR / "state.json"  # remembers which alerts were sent today
RECENT_FILE = STATE_DIR / "recent.json"  # recently posted headlines, for duplicate checks
RECENT_HOURS = 48                  # remember posts from this period for duplicate checks
RECENT_MAX = 400
DUP_LATEST = 5                     # duplicate check sees the latest posts...
DUP_SIMILAR = 8                    # ...plus the most similar ones from RECENT_HOURS
CLASSIFY_ENABLED = False          # cheap first pass (Lite) for foreign news / features
DUP_COMPACT = True                 # shorter listing: only the 5 closest candidates (2-3x fewer tokens)
MAX_SEEN = 2000          # how many item IDs to remember
FIRST_RUN_POSTS = 3      # on the very first run, post only the latest N items
MAX_POSTS_PER_RUN = 4    # posts per check; the rest wait for the next check
MIN_POST_GAP = 60        # seconds between two posts in the channel

# Free LLM via Groq (OpenAI-compatible API). Get a free key at
# console.groq.com -> API Keys, and save it as the LLM_API_KEY secret.
# Models are tried in order until one works.
LLM_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_URL = os.environ.get("LLM_URL", "").strip() or \
    "https://api.groq.com/openai/v1/chat/completions"
LLM_MODELS = [m.strip() for m in (os.environ.get("LLM_MODELS", "").strip() or
              "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"
              ).split(",") if m.strip()]
# GigaChat (Sber) writes the summaries: best Russian in a side-by-side test.
# Free yearly pools: Ultra 50M tokens, Pro 40M. Daily caps keep the yearly pool
# from running out early; when a cap is reached the next model takes over.
GIGA_KEY = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
GIGA_URL = "https://api.giga.chat/v1/chat/completions"
GIGA_OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
# Paid GigaChat packages are spread evenly until this date (one year);
# the daily budget is recomputed every day from the remaining balance
GIGA_UNTIL = "2027-10-20"
GIGA_BALANCE_URL = "https://api.giga.chat/v1/balance"
# Writers in order: Ultra, then Max (its own package), then Pro
GIGA_DAILY_CAP = {"GigaChat-3-Ultra": 135_000, "GigaChat-2-Max": 68_000,
                  "GigaChat-3-Pro": 105_000}  # used only if the balance is unknown
SUMMARY_MODELS = ([m for m in GIGA_DAILY_CAP] if GIGA_KEY else []) + \
                 (LLM_MODELS if LLM_KEY else [])
# Proofreading pass. Only Qwen: in tests gpt-oss over-edited and changed meaning
PROOF_MODELS = (["GigaChat-3-Pro"] if GIGA_KEY else []) + ["qwen/qwen3.8-27b"]  # Pro won the proofreading test 5/5
PROOF_PROMPT = (
    "Ты корректор русскоязычного новостного канала. Исправь в заголовке и тексте "
    "только явные ошибки русского языка: орфографию, согласование родов, чисел и "
    "падежей (например, «третье допрос» → «третий допрос»), предлоги и управление "
    "(«в территорию» → «на территорию»), пунктуацию, неестественные кальки с "
    "французского («вторичный дом» → «второй дом»). Не меняй смысл, факты, цифры, даты, имена, названия и слова "
    "латиницей; ничего не добавляй и не убирай; не переписывай то, что и так "
    "правильно. Исключение: если имя человека, название места или организации "
    "написано кириллицей, а в оригинале (дан ниже) латиницей — замени его на "
    "латинское написание из оригинала («Правинд Жогно» → «Pravind Jugnauth», "
    "«четверо-Борнс» → «Quatre-Bornes», "
    "«Элоди Томас» → «Élodie Thomas»); слова «премьер-министр», «министр» и т. п. "
    "оставь по-русски. Цитата в кавычках на французском, английском или "
    "креольском — тоже ошибка: переведи её на русский по смыслу, сохранив "
    "кавычки («Who cares?» → «Кому какое дело?»). Английские и французские "
    "термины, оставленные в тексте (under warning, walk-out, Reserve, Policy), переведи "
    "на русский (under warning → в качестве подозреваемого). Названия "
    "государственных органов по-английски или по-французски переведи "
    "(в Attorney General's Office → в министерстве юстиции). "
    "Верни строго JSON: {\"title\": \"...\", \"summary\": \"...\", "
    "\"changes\": [\"было → стало\", ...]}. Если ошибок нет, верни текст как есть."
)

# Duplicate checks are a reasoning task: a different model order works better
# Qwen is kept for writing; repeats are judged by gpt-oss only
DUP_MODELS = (["GigaChat-3-Pro"] if GIGA_KEY else []) + ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
ARTICLE_CHARS = 2500     # article text sent to the model (keeps within free limits)
MIN_TEXT_FOR_SUMMARY = 80   # less text than this -> no summary, title only

PROMPT = (
    "Ты редактор русскоязычного новостного канала о Маврикии. "
    "Ниже статья с маврикийского сайта на французском; в ней бывают цитаты "
    "на маврикийском креольском (например: «Li guet nou», «Nou pou res», "
    "«monn trouv lamor»). Верни строго JSON вида "
    '{"mauritius": true или false, "news": true или false, '
    '"names": [{"orig": "...", "type": "...", "ru": "..."}], "title": "...", "summary": "..."}. '
    "names — СНАЧАЛА выпиши все имена собственные из статьи, которые войдут "
    "в заголовок или пересказ: orig — точно как в статье (с диакритикой, "
    "полное имя: «Chloé Gabriel», «Sainte-Croix»); type — person (человек), "
    "org (организация, партия, команда, компания, суд, полиция как ведомство), "
    "mu_place (место на Маврикии, Rodrigues, Agaléga), foreign (страна, город, "
    "остров или регион за пределами Маврикия). ru — только для foreign: "
    "название по-русски в именительном падеже, как в русских СМИ "
    "(Irlande → Ирландия, Congo → ДР Конго, Comores → Коморы, Kinshasa → "
    "Киншаса, Singapour → Сингапур). ru также для org, у которых есть обычное "
    "русское название: Assemblée nationale → Национальное собрание, Parti "
    "travailliste → Лейбористская партия, Banque de Maurice → Банк Маврикия, "
    "Cour suprême → Верховный суд, Cour intermédiaire → Промежуточный суд, "
    "Attorney General's Office → министерство юстиции, Prime "
    "Minister's Office → канцелярия премьер-министра, Ministry of … → "
    "министерство …; вообще все государственные органы и должности "
    "(министерства, ведомства, суды, полиция, парламент) пиши по-русски, "
    "латиницей — только компании, клубы, ассоциации, партии с аббревиатурой. "
    "Международные конвенции, договоры и законы тоже по-русски: Convention "
    "des Nations unies relative aux droits de l'enfant → Конвенция ООН о "
    "правах ребёнка, Civil Code → Гражданский кодекс. Спортивные и прочие "
    "события тоже по-русски: Championnats d'Afrique de cyclisme → чемпионат "
    "Африки по велоспорту, Jeux des îles → Игры островов Индийского океана. Затем в title и summary пиши person, "
    "остальные org и mu_place ТОЧНО как orig, латиницей, не склоняя и не переводя частично "
    "(никаких «Клёэ Gabriel» или «Матьё Souprayen» — только «Chloé Gabriel»); "
    "foreign — только по-русски, склоняя как обычные слова («в Ирландии», "
    "«на Коморах»). "
    "news — есть ли в статье новость, то есть недавнее событие или решение: "
    "происшествие, решение властей, заявление, суд, забастовка, цены, "
    "результат матча или скачек, открытие, мероприятие, назначение. "
    "news = false для материалов без события: портрет или история жизни "
    "частного человека (как он учился, сделал карьеру, открыл своё дело, "
    "«мечта», «путь к успеху»), лайфстайл, рецепты, советы, гороскопы, "
    "реклама и промо-статьи, ностальгия и воспоминания. Если поводом "
    "для портрета стала свежая новость о человеке (награда, назначение, "
    "происшествие), news = true. "
    "mauritius — решай по главному событию статьи, а не по отдельным фразам. "
    "true, только если: событие происходит на Маврикии (включая Rodrigues, "
    "Agaléga, Chagos); или его участники — маврикийцы, маврикийские власти, "
    "компании, организации, спортсмены (в том числе за рубежом); или другая "
    "страна либо организация приняла решение конкретно по Маврикию. "
    "false — во всех остальных случаях: мировая политика, войны и конфликты, "
    "экономика других стран, цены на нефть, знаменитости, зарубежный спорт, "
    "происшествия за границей без маврикийцев. Это false, даже если сайт "
    "добавил к зарубежной новости абзац о возможных последствиях для "
    "Маврикия («для Маврикия, зависящего от импорта, это риск», «может "
    "отразиться на ценах»): общие рассуждения о влиянии не делают новость "
    "маврикийской. "
    "title — полный перевод заголовка на русский. Цитаты в кавычках тоже "
    "переводи на русский по смыслу, сохраняя кавычки; ни одного слова на "
    "французском, английском или креольском в title остаться не должно "
    "(«Who cares?» → «Кому какое дело?»). "
    "summary — пересказ своими словами на русском в 2–4 предложениях, "
    "грамотным живым русским языком, как в хорошем новостном издании "
    "(проверь согласование слов в роде, числе и падеже), "
    "до 600 символов: что произошло, где, кто, что дальше; ключевые цифры "
    "(число пострадавших, арестованных, суммы, даты) сохраняй. "
    "Только факты из статьи, без оценок и без выдумок; должности и звания "
    "людей бери только из статьи, не добавляй свои (кто сейчас премьер-министр, "
    "министр или лидер оппозиции, ты можешь не знать). Если текст статьи "
    "короткий (объявление в 1–3 предложения), просто точно переведи его на "
    "русский, ничего не добавляя и не додумывая. "
    "Имена и фамилии людей, названия мест, организаций, партий и компаний "
    "оставляй латиницей, точно как в оригинале (Navin Ramgoolam, Kobita Jugnauth, "
    "Bruneau Laurette, Port-Louis, Sainte-Croix, Business Mauritius); названия "
    "городов и районов никогда не переводи по частям: Quatre-Bornes, Rose-Hill, "
    "Beau-Bassin, Vacoas, Curepipe, Grand-Baie, Mahébourg, Rivière-Noire, "
    "Pamplemousses, Flacq, Plaine-Verte, Roche-Bois, Pointe-aux-Sables. "
    "Исключение — зарубежная география: страны, их столицы и другие города за "
    "пределами Маврикия пиши по-русски, как принято в русских СМИ (Сингапур, "
    "Лондон, Париж, Лилль, Нью-Дели, Реюньон, Мадагаскар), а не латиницей. Про остров "
    "пиши «на Маврикии», «на Маврикий», «с Маврикия» (не «в Маврикии»); склонять их "
    "не нужно, при необходимости добавь русское слово: «премьер-министр Navin "
    "Ramgoolam», «в городе Curepipe». Рупии — «рупий». Словарик: couvre-feu — комендантский "
    "час; Assemblée nationale — Национальное собрание; DPP — генпрокурор (DPP); "
    "IPCC — комиссия по жалобам на полицию (IPCC); SMF — спецподразделение SMF; "
    "CWA — водоканал CWA; Premier ministre — премьер-министр; "
    "MSM — партия MSM (Militant Socialist Movement); MMM — партия MMM; "
    "Parti travailliste — Лейбористская партия; Attorney General — "
    "министр юстиции (это не прокурор: прокурор — DPP); "
    "rogue and vagabond — бродяжничество с подозрительными целями; "
    "provisional charge — предварительное обвинение; "
    "walk-out — демонстративный уход из зала («Я не ухожу из зала, я "
    "приостанавливаю заседание»); PNQ — срочный вопрос оппозиции (PNQ); "
    "Speaker — спикер; "
    "résidence secondaire — второй дом (не «вторичный дом»); campement — "
    "дом на побережье; bungalow — бунгало; "
    "interrogé/questioned under warning — допрошен в качестве подозреваемого; "
    "remand, reconduit en cellule — оставлен под стражей; "
    "bail, caution — залог; Casernes centrales — штаб полиции (Casernes centrales); "
    "taking part in a riot — участие в беспорядках. Английские юридические "
    "термины (названия статей и обвинений) переводи на русский по смыслу, "
    "а не оставляй по-английски. "
    "Маврикийский креольский: «mari» перед прилагательным или глаголом — "
    "«очень, сильно» («mo mari kontan» — «я очень рад»), а не «муж»; «mo» — "
    "«я, мой», «to» — «ты», «li» — «он/она», «nou» — «мы», «zot» — «они»; "
    "«inn» и «monn» — прошедшее время («monn trouv» — «я увидел»); «pou» — "
    "будущее время; «lame» — «рука», «lamor» — «смерть». Пол говорящего "
    "бери из статьи (дядя — мужчина: «я рад», не «я рада»). Если смысл цитаты "
    "неясен, передай его осторожно, без выдуманных подробностей."
)

DUP_PROMPT = (
    "Ты редактор новостного канала и не пускаешь в канал повторы. Дана новая "
    "статья (заголовок и начало текста, обычно на французском) и пронумерованный "
    "список того, что уже опубликовано в канале (заголовок — краткое содержание). "
    "Реши, есть ли в новой статье хотя бы один НОВЫЙ ВАЖНЫЙ факт, которого нет "
    "в опубликованном: новое событие, новое или изменённое решение властей, "
    "новые пострадавшие или задержанные, новые важные цифры или сроки, "
    "заявление человека, которого ещё не цитировали. "
    "Если всё главное в статье уже есть в опубликованном (тот же указ, та же речь, "
    "то же событие в пересказе другого СМИ или повторная статья того же СМИ, "
    "прямой эфир того, о чём уже написали, материал печатного номера или обзор, "
    "пересказывающий вчерашние события) — это дубль, даже если статья новее. "
    "Если есть новый важный факт — не дубль, даже если тема та же. "
    "Но дополнительные подробности, цитаты, мнения и комментарии к уже "
    "опубликованной истории без нового события — это дубль; особенно если это "
    "та же история от того же СМИ в тот же день (например, годовщина дела и "
    "комментарий адвоката к ней). "
    "Одно решение или объявление — один пост: если уже опубликовано, что "
    "принято решение (школьный календарь, график подачи воды, указ, бюджет, "
    "тарифы), то другая статья о том же решении — дубль, даже если в ней "
    "точнее даты, расписаны детали, названы другие цифры из того же документа "
    "(«Календарь 2027: начало 11 января» после «Календарь 2027: даты триместров» "
    "— дубль). Не дубль — только если решение изменилось или отменено. "
    "Плановые судебные шаги по делу, о заседании которого уже написали, — тоже "
    "дубль: тот же обвиняемый снова в суде, оставлен под стражей или в камере, "
    "содержание под стражей продлено, дело отложено до новой даты. Не дубль — "
    "если появились новые обвиняемые или задержанные, освобождение под залог, "
    "новое или снятое обвинение, приговор, признание вины. "
    "Итог соревнования (победа, медаль, счёт финала) — не дубль, если раньше "
    "писали только о выходе в финал или о ходе турнира. "
    "Ответь строго JSON в таком порядке полей: "
    '{"new_facts": "какие новые важные факты есть, или пусто", '
    '"is_duplicate": true или false, "duplicate_of": номер самого похожего или null}.'
)

# Extra feeds checked only in test mode (to try a source before adding it)
TEST_FEEDS = []

# --------------------------------------------------------------------------

translator = GoogleTranslator(source="auto", target="ru")
_llm_state = {"disabled": False, "model": None, "exhausted": set(), "tokens": 0, "calls": 0,
              "names": []}


def load_seen():
    seen = None  # None = first run
    if SEEN_FILE.exists():
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    # Safety net while moving state out of main: merge any old copy there
    old = Path("seen.json")
    if old.exists() and old.resolve() != SEEN_FILE.resolve():
        known = set(seen or [])
        seen = (seen or []) + [u for u in json.loads(old.read_text(encoding="utf-8"))
                               if u not in known]
    return seen


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(seen[-MAX_SEEN:], ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def translate(text):
    """Google Translate, then MyMemory as a fallback; original text if both fail."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return translator.translate(text[:4500])
    except Exception as e:
        print(f"Google Translate failed: {type(e).__name__}")
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": text[:480], "langpair": "fr|ru"},
                         timeout=30)
        data = r.json()
        out = (data.get("responseData") or {}).get("translatedText") or ""
        if r.ok and out and "MYMEMORY WARNING" not in out.upper():
            return html.unescape(out)
        print(f"MyMemory failed: {str(data)[:200]}")
    except Exception as e:
        print(f"MyMemory failed: {e}")
    return text


_fetch = {"ok": True}


def article_text(url):
    """Download the article page; return (main text, og:image URL or None).
    _fetch["ok"] tells whether the page could be downloaded at all."""
    _fetch["ok"] = True
    try:
        r = None
        for attempt in range(2):
            try:
                r = requests.get(url, timeout=30, headers=UA)
                if r.status_code < 500:
                    break
            except requests.RequestException:
                if attempt:
                    raise
            time.sleep(5)
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False,
                                   include_tables=False) or ""
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text))
        image = html.unescape(m.group(1)) if m else None
        return text.strip(), image
    except Exception as e:
        print(f"Could not read article {url}: {e}")
        _fetch["ok"] = False
        return "", None


def _llm_call(model, messages, max_tokens=700):
    r = _llm_post(model, messages, max_tokens)
    try:
        if r.ok:
            _llm_state["tokens"] += r.json().get("usage", {}).get("total_tokens", 0)
            _llm_state["calls"] += 1
    except Exception:
        pass
    return r


_giga = {"token": None, "exp": 0, "verify": None, "dead": False}


def _giga_verify():
    """CA bundle with the Russian Ministry of Digital Development root (Sber's
    servers use it); falls back to the standard bundle."""
    if _giga["verify"]:
        return _giga["verify"]
    import certifi, tempfile
    pem = open(certifi.where(), "rb").read()
    for u in ["https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
              "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"]:
        try:
            r = requests.get(u, timeout=30)
            if r.ok and b"BEGIN CERTIFICATE" in r.content:
                pem += b"\n" + r.content
        except Exception as e:
            print(f"GigaChat certificate download failed: {e}")
    f = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
    f.write(pem)
    f.close()
    _giga["verify"] = f.name
    return f.name


def _giga_token(force=False):
    if _giga["token"] and not force and time.time() < _giga["exp"] - 60:
        return _giga["token"]
    import uuid
    r = requests.post(GIGA_OAUTH, timeout=30, verify=_giga_verify(),
                      headers={"Authorization": f"Basic {GIGA_KEY}",
                               "RqUID": str(uuid.uuid4()),
                               "Content-Type": "application/x-www-form-urlencoded",
                               "Accept": "application/json"},
                      data={"scope": "GIGACHAT_API_PERS"})
    r.raise_for_status()
    j = r.json()
    _giga["token"] = j["access_token"]
    _giga["exp"] = j.get("expires_at", 0) / 1000 or time.time() + 1500
    return _giga["token"]


def _giga_post(model, messages, max_tokens):
    payload = {"model": model, "messages": messages,
               "temperature": 0.2, "max_tokens": max_tokens}
    for attempt in range(2):
        r = requests.post(GIGA_URL, json=payload, timeout=120, verify=_giga_verify(),
                          headers={"Authorization": f"Bearer {_giga_token(force=attempt > 0)}",
                                   "Accept": "application/json"})
        if r.status_code != 401:
            break
    if r.ok:  # count against the daily cap
        used = r.json().get("usage", {}).get("total_tokens", 0)
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))
        g = _state_get("giga_used", {})
        if g.get("date") != today:
            g = {"date": today}
        g[model] = g.get(model, 0) + used
        _state_set("giga_used", g)
    return r


def _giga_cap(model):
    """Today's token budget for a model: what is left in its paid package,
    spread evenly over the days until GIGA_UNTIL. The balance is asked once
    a day; if that fails, the fixed GIGA_DAILY_CAP is used."""
    pool = ("GigaChat-Ultra" if "Ultra" in model else "GigaChat-Pro" if "Pro" in model
            else "GigaChat-Max" if "Max" in model else "GigaChat")  # Lite: GigaChat-2, 3-Lightning
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))
    bal = _state_get("giga_balance", {})
    if bal.get("date") != today and not os.environ.get("BOT_TEST"):
        try:
            r = requests.get(GIGA_BALANCE_URL, timeout=30, verify=_giga_verify(),
                             headers={"Authorization": f"Bearer {_giga_token()}"})
            r.raise_for_status()
            bal = {"date": today, **{b["usage"]: b["value"] for b in r.json().get("balance", [])}}
            _state_set("giga_balance", bal)
            days = _giga_days_left()
            print("GigaChat balance: " + ", ".join(f"{k} {v:,}" for k, v in bal.items() if k != "date")
                  + f"; {days} days to go")
            if days in (30, 7, 1):
                alert("giga_end", f"⏳ Пакеты GigaChat рассчитаны до {GIGA_UNTIL}, осталось {days} дн. "
                      "После этого бот перейдёт на бесплатные модели Groq (качество "
                      "пересказов ниже). Можно купить новый пакет на developers.sber.ru.")
        except Exception as e:
            print(f"GigaChat balance check failed: {e}")
    if pool and bal.get(pool) is not None:
        return int(bal[pool] / _giga_days_left())
    return GIGA_DAILY_CAP.get(model, 0)


def _giga_days_left():
    import datetime
    end = datetime.date.fromisoformat(GIGA_UNTIL)
    now = datetime.datetime.fromtimestamp(time.time() + 4 * 3600, datetime.timezone.utc).date()
    return max(1, (end - now).days)


def _giga_over_cap(model):
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))
    g = _state_get("giga_used", {})
    return g.get("date") == today and g.get(model, 0) >= _giga_cap(model)


def _llm_post(model, messages, max_tokens):
    if model.startswith("GigaChat"):
        return _giga_post(model, messages, max_tokens)
    payload = {"model": model, "messages": messages,
               "temperature": 0.2, "max_tokens": max_tokens}
    if "gpt-oss" in model:
        payload["reasoning_effort"] = "low"  # keep hidden reasoning short
    return requests.post(
        LLM_URL,
        headers={"Authorization": f"Bearer {LLM_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )


def _parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = text.replace("\u0301", "").replace("\u0300", "")  # stress marks: Бере́нже
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


# Cheap first pass: is it Mauritian news at all? Done by a model from the big
# Lite package, so foreign news and features don't cost Ultra tokens.
CLASSIFY_MODELS = ["GigaChat-3-Lightning"]
_RULES = PROMPT[PROMPT.index("news — есть ли"):PROMPT.index("title — полный перевод")]
CLASSIFY_PROMPT = (
    "Ты редактор русскоязычного новостного канала о Маврикии. Ниже начало "
    "статьи с маврикийского сайта. Реши, подходит ли она для канала. Верни "
    'строго JSON: {"mauritius": true или false, "news": true или false}. ' + _RULES)


def classify(title, body):
    """(mauritius, news) from the cheap model, or None if it is unavailable."""
    if not GIGA_KEY:
        return None
    msgs = [{"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\nНачало статьи:\n{body[:1200]}"}]
    for model in CLASSIFY_MODELS:
        if _giga["dead"] or _giga_over_cap(model) or model in _llm_state["exhausted"]:
            continue
        try:
            r = _llm_call(model, msgs, 60)
            if not r.ok:
                continue
            d = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        except Exception as e:
            print(f"  classify failed ({model}): {e}")
            continue
        no = lambda v: v is False or str(v).lower() == "false"
        if "mauritius" in d and "news" in d:
            return (not no(d["mauritius"]), not no(d["news"]))
    return None


def summarize(title, body):
    """Ask the LLM for a Russian title + summary. Returns (title, summary)
    or None if the model is unavailable (no key, daily limit, error)."""
    if _llm_state["disabled"] or not SUMMARY_MODELS:
        return None
    _llm_state["names"] = []  # this article's names only
    content = f"Заголовок: {title}\n\nТекст статьи:\n{body[:ARTICLE_CHARS] or '(текст недоступен)'}"
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": content}]
    models = [m for m in SUMMARY_MODELS if m not in _llm_state["exhausted"]]
    unavailable = []  # models the provider says do not exist
    for model in models:
        if model.startswith("GigaChat") and (_giga["dead"] or _giga_over_cap(model)):
            _llm_state["exhausted"].add(model)
            continue
        exhausted = False
        for attempt in range(2):
            try:
                r = _llm_call(model, messages, 1200)  # names list + text
            except Exception as e:
                print(f"LLM request failed ({model}): {type(e).__name__} {str(e)[:200]}")
                if model.startswith("GigaChat"):  # e.g. certificate or network problem
                    _giga["dead"] = True
                    exhausted = True
                    break
                return None
            if r.status_code == 429:
                wait = float(r.headers.get("retry-after", "0") or 0)
                if "per minute" in r.text and not wait:
                    wait = 30
                if attempt == 0 and 0 < wait <= 60:
                    print(f"LLM per-minute limit, waiting {wait:.0f}s")
                    time.sleep(wait + 1)
                    continue
                print(f"LLM limit reached for {model}: {r.text[:200]}")
                _llm_state["exhausted"].add(model)
                if _llm_state["model"] == model:
                    _llm_state["model"] = None
                exhausted = True
            break
        if exhausted:
            continue
        if model.startswith("GigaChat") and r.status_code in (401, 402, 403):
            # 401/403: key problem, 402: yearly token pool used up
            print(f"GigaChat {r.status_code}: {r.text[:200]}")
            _llm_state["exhausted"].add(model)
            if r.status_code != 402:
                _giga["dead"] = True
                alert("giga", "⚠️ GigaChat не принимает ключ GIGACHAT_AUTH_KEY. "
                      "Пересказы пишет запасная модель (Groq). Проверьте ключ в "
                      "секретах репозитория.")
            continue
        if r.status_code in (401, 403):
            print(f"LLM key rejected: {r.text[:200]}")
            _llm_state["disabled"] = True
            alert("key", "⚠️ Groq не принимает ключ LLM_API_KEY. Проверьте "
                  "секрет в настройках репозитория. Пока публикуются только "
                  "заголовки.")
            return None
        if r.status_code in (400, 404) and "model" in r.text.lower():
            print(f"Model {model} unavailable ({r.text[:150]}), trying next")
            unavailable.append(model)
            continue
        if not r.ok:
            print(f"LLM error {r.status_code}: {r.text[:300]}")
            return None
        try:
            data = _parse_json(r.json()["choices"][0]["message"]["content"])
            ru_title = (data.get("title") or "").strip()
            ru_summary = (data.get("summary") or "").strip()
        except Exception as e:
            print(f"Bad LLM answer from {model}: {e}; raw: {r.text[:300]}")
            continue
        if not ru_title:
            continue
        _llm_state["model"] = model
        if model != SUMMARY_MODELS[0]:
            print(f"  written by {model}")
        no = lambda v: v is False or str(v).lower() == "false"
        # True, or why the article is skipped: "foreign" / "feature"
        local = "foreign" if no(data.get("mauritius")) else \
            "feature" if no(data.get("news")) else True
        names = data.get("names") if isinstance(data.get("names"), list) else []
        _llm_state["names"] = [n for n in names if isinstance(n, dict) and n.get("orig")]
        return ru_title, ru_summary, local
    _llm_state["disabled"] = True
    if set(SUMMARY_MODELS) <= _llm_state["exhausted"]:
        alert("limit",
              "⚠️ Закончился дневной лимит у всех моделей (GigaChat и Groq).\n\nДо сброса бот публикует только переведённые "
              "заголовки, без пересказов. Лимит восстанавливается сам, "
              "ничего делать не нужно.")
        return None
    if not unavailable:
        # models answered, just not usefully (empty or broken answer): this
        # article gets a translated headline, nothing to warn the owner about
        print("No usable answer from the models for this article")
        _llm_state["disabled"] = False
        return None
    alert("models",
          "⚠️ Ни одна модель из LLM_MODELS не ответила — похоже, Groq их "
          "переименовал или убрал.\n\nБот публикует только переведённые "
          "заголовки. Актуальные модели: console.groq.com/docs/models")
    return None


def _state_get(key, default=None):
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def _state_set(key, value):
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    state[key] = value
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def alert(kind, text):
    if os.environ.get("BOT_TEST"):  # test and probe runs never message the owner
        print(f"(alert suppressed in test run: {kind})")
        return
    """Private message to the owner, at most once per day for each kind."""
    if not ADMIN_ID:
        return
    today = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get(kind) == today:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_ID, "text": text},
            timeout=30,
        )
        if not r.ok:
            print(f"Alert not delivered ({r.text[:200]}). "
                  "Did you press Start in the bot?")
            return
    except Exception as e:
        print(f"Alert not delivered: {e}")
        return
    state[kind] = today
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def _norm_title(t):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", (t or "").lower())  # [LIVE], (vidéo)
    words = re.findall(r"[a-zà-ÿ0-9]+", t)
    stop = {"le", "la", "les", "de", "des", "du", "un", "une", "et", "à", "a",
            "au", "aux", "en", "pour", "sur", "dans", "par", "ce", "cette", "ses",
            "son", "sa", "est", "qui", "que", "d", "l", "s", "n", "the", "of"}
    return {w for w in words if w not in stop and len(w) > 1}


def same_title(a, b):
    """Near-identical original headlines (e.g. two sites, same agency text)."""
    x, y = _norm_title(a), _norm_title(b)
    if len(x) < 3 or len(y) < 3:
        return False
    # Different numbers, days or dates mean a different event
    # (e.g. Wednesday's curfew vs Thursday's curfew)
    days = {"lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
            "demain", "aujourd", "hier", "soir", "matin", "nuit", "nouvelle", "nouveau"}
    if {w for w in x if w.isdigit() or w in days} != {w for w in y if w.isdigit() or w in days}:
        return False
    common = len(x & y)
    # same words, or one headline is the other plus a few extra words
    return common / len(x | y) >= 0.6 or (common >= 5 and common / min(len(x), len(y)) >= 0.7)


def find_duplicate(orig_title, lead, recent):
    """Index (1-based) of the recent post this article repeats, or None."""
    if not recent:
        return None
    for i, x in enumerate(recent, 1):
        if x.get("orig") and same_title(orig_title, x["orig"]) and x.get("t", 0) > time.time() - 36 * 3600:
            print(f"  same headline as #{i}: {x['orig'][:80]}")
            return i
    if not LLM_KEY or _llm_state["disabled"]:
        return None
    # Show the model the latest posts plus the most similar older ones
    probe = _norm_title(orig_title) | _norm_title(lead[:400])
    def score(x):
        w = _norm_title(x.get("orig", ""))
        return len(w & probe) / (len(w) + 2) if w else 0
    # No significant word in common with anything recent: not a repeat, and
    # no need to spend tokens asking the model
    if max((len(_norm_title(x.get("orig", "")) & probe) for x in recent), default=0) < 2:
        return None
    if DUP_COMPACT:
        # Only real candidates: posts sharing 2+ significant words, the 5 closest;
        # the 3 closest in full, the others by their first sentence
        common = lambda x: len(_norm_title(x.get("orig", "")) & probe)
        cand = sorted((i for i in range(len(recent)) if common(recent[i]) >= 2),
                      key=lambda i: (-common(recent[i]), -score(recent[i])))[:5]
        full = set(cand[:3])
        first = lambda t: (re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0])[:200]
        when = lambda x: time.strftime("%d.%m %H:%M", time.gmtime(x.get("t", 0) + 4 * 3600))
        listing = "\n".join(f"{i + 1}. [{when(recent[i])}] {recent[i].get('orig') or recent[i]['title']}"
                             f" — {recent[i].get('summary', '')[:600] if i in full else first(recent[i].get('summary', ''))}"
                             for i in sorted(cand))
        return _ask_duplicate(orig_title, lead, recent, listing)
    idx = list(range(len(recent)))
    latest = idx[-DUP_LATEST:]
    similar = sorted((i for i in idx[:-DUP_LATEST] if score(recent[i]) > 0),
                     key=lambda i: -score(recent[i]))[:DUP_SIMILAR]
    shown = sorted(set(latest) | set(similar))
    # Similar posts are shown in full (that is where repeats hide); the
    # latest unrelated ones only briefly, to save tokens
    full = set(similar) | {i for i in latest if score(recent[i]) >= 0.3}
    when = lambda x: time.strftime("%d.%m %H:%M", time.gmtime(x.get("t", 0) + 4 * 3600))
    listing = "\n".join(f"{i + 1}. [{when(recent[i])}] {recent[i].get('orig') or recent[i]['title']}"
                         f" — {recent[i].get('summary', '')[:600 if i in full else 90]}"
                         for i in shown)
    return _ask_duplicate(orig_title, lead, recent, listing)


def _ask_duplicate(orig_title, lead, recent, listing):
    msg = (f"Новая статья:\n{orig_title}\n{lead[:700]}\n\n"
           f"Уже опубликовано:\n{listing}")
    free = [m for m in DUP_MODELS if m not in _llm_state["exhausted"]
            and not (m.startswith("GigaChat") and (_giga["dead"] or _giga_over_cap(m)))]
    messages = [{"role": "system", "content": DUP_PROMPT},
                {"role": "user", "content": msg}]
    r = None
    for model in free:
        try:
            r = _llm_call(model, messages, 600)
        except Exception as ex:
            print(f"Duplicate check {model} failed: {ex}")
            r = None
            continue
        if r.status_code == 429 and not model.startswith("GigaChat"):
            wait = float(r.headers.get("retry-after", "0") or 0)
            if "per minute" in r.text and not wait:
                wait = 30
            if not 0 < wait <= 60:
                print(f"Duplicate check: limit reached for {model}")
                _llm_state["exhausted"].add(model)
                continue
            time.sleep(wait + 1)
            r = _llm_call(model, messages, 600)
        if r.ok:
            break
        print(f"Duplicate check {model} failed {r.status_code}: {r.text[:150]}")
        if model.startswith("GigaChat") and r.status_code in (402, 429):
            _llm_state["exhausted"].add(model)
    if r is None or not r.ok:
        return None
    try:
        data = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        if data.get("is_duplicate") is not True:
            print(f"  dup check: new ({str(data.get('new_facts', ''))[:150]})")
            return None
        dup = data.get("duplicate_of")
        try:
            dup = int(dup)
        except (TypeError, ValueError):
            dup = 1
        dup = dup if 1 <= dup <= len(recent) else 1
        print(f"  dup: nothing new ({data.get('new_facts', '')[:150]})")
        return dup
    except Exception as e:
        print(f"Duplicate check failed: {e}")
    return None


def _safe_edit(old, new, original="", removable=()):
    """Accept a proofread version only if it kept the numbers, kept every Latin
    word, added only Latin words found in the original article, kept the size."""
    if not new or not isinstance(new, str):
        return False
    digits = lambda t: sorted(re.findall(r"\d+", t))
    latin = lambda t: re.findall(r"[A-Za-zÀ-ÿ](?:[A-Za-zÀ-ÿ'’.-]*[A-Za-zÀ-ÿ])?", t)
    if digits(old) != digits(new):
        return False
    # words of a foreign quote («Who cares?») may be translated away
    quoted = set(latin(" ".join(re.findall(r"«([^»]*)»|\"([^\"]*)\"", old) and
                                [q for pair in re.findall(r"«([^»]*)»|\"([^\"]*)\"", old)
                                 for q in pair])))
    gone = quoted | set(latin(" ".join(removable)))  # foreign places go Russian
    old_l, new_l = [w for w in latin(old) if w not in gone], latin(new)
    allowed = set(w.lower() for w in old_l + latin(original))
    if any(w not in new_l for w in old_l) or any(w.lower() not in allowed for w in new_l):
        return False
    return 0.8 <= len(new) / max(len(old), 1) <= 1.2


_FIXES = [
    (r"\b([Вв]) Маврики(и|й)\b", lambda m: ("Н" if m.group(1) == "В" else "н") + "а Маврики" + m.group(2)),
    (r"\b([Ии])з Маврикия\b", lambda m: ("С" if m.group(1) == "И" else "с") + " Маврикия"),
    (r'"([^"\n]{1,200})"', r"«\1»"),  # Russian quotes
    # a police term the models keep leaving in English
    (r"\s*(?:«|\")?under warning(?:»|\")?", " в качестве подозреваемого"),
    (r"\b([Вв]) Родригес[еа]?\b", lambda m: ("Н" if m.group(1) == "В" else "н") + "а Rodrigues"),
    (r"\bРодригес(?:а|е|у|ом)?\b", "Rodrigues"),  # «жителя Родригеса» -> Rodrigues
]


def fix_russian(text):
    """Deterministic fixes the models keep getting wrong."""
    for pattern, repl in _FIXES:
        text = re.sub(pattern, repl, text or "")
    return text


_LATIN = re.compile(r"[A-Za-zÀ-ÿ](?:[A-Za-zÀ-ÿ'’.-]*[A-Za-zÀ-ÿ])?")

_CYR2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "j", "z", "i", "i", "k", "l", "m",
                     "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sh",
                     "", "i", "", "e", "iu", "ia"]))
# Russian case endings a name may get ("Риши" -> "Риши", "Кандосе" -> "Кандос")
_RU_ENDINGS = ("ами", "ями", "ого", "его", "ому", "ему", "ой", "ей", "ом", "ем",
               "ах", "ях", "ам", "ям", "ии", "ию", "ия", "а", "я", "у", "ю", "е", "ы")


# Capitalised French words that are not names (titles, institutions, countries,
# days, months): their Russian equivalents must stay in Russian
_NOT_NAMES_FR = """premier premiere ministre ministere president presidente docteur monsieur
madame mademoiselle maitre monseigneur mgr pere soeur saint sainte police gouvernement
commissaire juge cour tribunal parlement assemblee nationale etat republique ile maurice
mauricien mauricienne france inde chine afrique europe angleterre australie madagascar
reunion seychelles rodrigues lundi mardi mercredi jeudi vendredi samedi dimanche janvier
fevrier mars avril mai juin juillet aout septembre octobre novembre decembre nord sud est
ouest centre hopital ecole college universite banque bureau conseil comite commission
association federation union syndicat parti eglise mosquee temple video photos live
affaire selon apres avant depuis pendant pension tour commentaire commentaires interview
operation university national nationale diabete diabetes glucometre glucometres blog papa
maman video videos photo exclusif urgent dossier rapport budget diable groupe hindi renvoi france inde
francais francaise francaises anglais anglaise indien indienne indiens chinois chinoise
malgache malgaches reunionnais reunionnaise seychellois seychelloise britannique
americain americaine africain africaine sud-africain europeen europeenne allemand allemande
italien italienne russe russes pakistanais bangladais nepalais sri-lankais australien
australienne mauriciens mauriciennes rodriguais comorien comorienne kenyan japonais
espagnol espagnole portugais belge suisse canadien canadienne emirati saoudien
policier policiers policiere indian program programme aeroport incidents incident comment
rating express impots administration expert african commissioner office officer
chief minister court committee commissions
singapour singapore london londres paris lille lyon marseille bordeaux toulouse nice
nantes montpellier strasbourg valence narbonne rennes delhi new-delhi mumbai bombay
chennai calcutta kolkata bangalore pekin beijing shanghai dubai doha abu riyad
johannesburg durban pretoria nairobi antananarivo tananarive saint-denis moroni
maputo canberra melbourne tokyo moscou washington bruxelles
geneve rome berlin madrid lisbonne constitution senat loi code penal etat-major
cabinet conseil-des-ministres opposition majorite convention conventions traite accord
charte protocole declaration nations unies onu championnat championnats coupe jeux tournoi
festival salon semaine journee mondial mondiaux olympiques""".split()


def _sound(word):
    """Rough pronunciation key, comparable between French and Russian spellings."""
    import unicodedata
    w = word.lower().replace("ç", "s").replace("ье", "ie")
    w = re.sub(r"c(?=[eiyéèê])", "s", w)  # French soft c: Valence, Cité
    w = re.sub(r"g(?=[eiyéèê])", "j", w)  # and soft g: Roger ~ Роже
    w = re.sub(r"ch(?=[lr])", "k", w)  # Chloé, Christophe: к
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    w = "".join(_CYR2LAT.get(c, c) for c in w)
    w = re.sub(r"ch(?=[lr])", "k", w)  # Члоэ (a misspelt Chloé) ~ kl
    w = w.replace("ch", "sh")  # French ch = Russian ш (Sèche ~ Сеш)
    for a, b in (("ou", "u"), ("ph", "f"), ("qu", "k"), ("ck", "k"), ("c", "k"),
                 ("q", "k"), ("w", "v"), ("y", "i"), ("x", "ks"), ("dj", "j"),
                 ("zh", "j"), ("sh", "ch"), ("tch", "ch"), ("h", ""), ("ee", "i"),
                 ("oo", "u"), ("ai", "e"), ("ei", "e"), ("au", "o"), ("eau", "o")):
        w = w.replace(a, b)
    w = w.replace("z", "s")
    w = re.sub(r"[^a-z]", "", w)
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


def _sound_latin(word):
    """Like _sound, but French final consonants are silent (Jugnauth ~ Jugno)."""
    w = _sound(word)
    return re.sub(r"(?<=[aeiou])[tdsx]$", "", w) or w


def _latin_keys(word):
    """Ways a French name may sound in Russian spelling:
    Perrier ~ Перье, Marie ~ Мари, Valence ~ Валанс."""
    keys = {_sound(word), _sound_latin(word)}
    if re.search(r"(ey|y)$", word.lower()):  # English names: Sydney ~ Сидни
        keys.add(_sound(re.sub(r"(ey|y)$", "i", word.lower())))
    for k in list(keys):
        k2 = re.sub(r"ier$", "ie", re.sub(r"(?<=.{3})(er|ez)$", "e", k))
        keys |= {k2, re.sub(r"en(?=[^aeiou]|$)", "an", k2)}
    for k in list(keys):
        if len(k) > 4 and k.endswith("e"):
            keys.add(k[:-1])
            # Laurette ~ Лоре(т): the consonant before a silent e may be dropped too
            keys.add(re.sub(r"(?<=[aeiou])[tdsx]$", "", k[:-1]))
    return keys


_NOT_NAMES = set()
_VOWELS = set("aeiou")
# u~a (Hurreeram ~ Харрирам), a~e (ram ~ рэм), e~i, o~u
_VOWEL_SWAPS = [{"u", "a"}, {"a", "e"}, {"e", "i"}, {"o", "u"}]


def _fr(word, oi=True):
    """French reading of one word: oi = уа, silent final consonant and e."""
    w = re.sub(r"e?aux?$", "o", word.lower())  # aux, eau, eaux = о
    w = re.sub(r"eu[xs]?$", "e", w).replace("eu", "e")  # Levieux ~ Левьё, Fleur ~ Флёр
    w = re.sub(r"(?<=[aeiouy])[xs]$", "", w)  # silent final x/s
    if oi:
        w = re.sub(r"oi[xst]?$", "ua", w).replace("oi", "ua")
    k = re.sub(r"(?<=[aeiou])[tdsx]$", "", _sound(w))
    k = re.sub(r"(?<=.{3})(er|ez)$", "e", k)
    return re.sub(r"(?<=..)e$", "", k)


def _align_names(text, original):
    """A name the model left half in Latin ("Роже Agathe", "Louis Майк Dambel"):
    the Latin words anchor it to the full name in the article, so the Cyrillic
    words between them are replaced by the article's words in that place."""
    from difflib import SequenceMatcher
    runs = []
    for m in re.finditer(r"[A-ZÀ-Ý][\w'’-]*(?:\s+[A-ZÀ-Ý][\w'’-]*)+", original):
        # only name words: not "L'activiste", "Le", "Me", "Ministre"...
        run = [w for w in m.group(0).split()
               if not re.match(r"[LDldQq][u]?[’']", w) and not _not_name(w)
               and w not in ("Le", "La", "Les", "Me", "Mme", "Mr", "M.", "Dr")]
        if len(run) >= 2:
            runs.append(run)
    tok = r"[A-ZÀ-ÝА-ЯЁ][\w'’-]*"

    def sub(m):
        words = m.group(0).split()
        latin = [w for w in words if re.match(r"[A-Za-zÀ-ÿ]", w)]
        if not latin or len(latin) == len(words):
            return m.group(0)
        for run in runs:
            if not all(w in run for w in latin):
                continue
            # split both into segments around the Latin anchors
            out, i = [], 0
            ok = True
            for w in words:
                if w in latin:
                    j = run.index(w, i) if w in run[i:] else -1
                    if j < 0:
                        ok = False
                        break
                    gap = run[i:j]
                    if gap and out and isinstance(out[-1], list):
                        cyr = out.pop()
                        r = SequenceMatcher(None, _sound("".join(cyr)),
                                            _sound("".join(gap))).ratio()
                        if r < (0.3 if len(cyr) == len(gap) == 1 else 0.5):
                            ok = False
                            break
                        out.extend(gap)
                    elif gap or (out and isinstance(out[-1], list) and len(out) > 1):
                        ok = False
                        break
                    out.append(w)
                    i = j + 1
                else:
                    if out and isinstance(out[-1], list):
                        out[-1].append(w)
                    else:
                        out.append([w])
            if ok and out and isinstance(out[-1], list):  # Cyrillic words at the end
                cyr = out.pop()
                sim = lambda g: SequenceMatcher(None, _sound("".join(cyr)), _sound("".join(g))).ratio()
                gaps = [run[i:i + n] for n in {len(cyr), len(cyr) + 1, len(run) - i} if run[i:i + n]]
                gap = max(gaps, key=sim) if gaps else []
                if not gap or sim(gap) < (0.3 if len(cyr) == len(gap) == 1 else 0.5):
                    out.extend(cyr)  # not part of the name: keep these words
                else:
                    out.extend(gap)
                    if len(cyr) == 1:
                        learned[cyr[0]] = " ".join(gap)
            # a sentence-initial word is usually not a name part ("Активиста
            # Bruneau"): it needs a close match ("Франсуа Mark" ~ François Mark)
            starts = re.search(r"(^|[.!?:«\"—]\s*)$", text[:m.start()]) is not None
            if ok and out and isinstance(out[0], list):  # Cyrillic words first
                cyr = out.pop(0)
                first = run.index(out[0]) if out and out[0] in run else -1
                gap = run[max(0, first - len(cyr)):first] if first > 0 else []
                if not gap or SequenceMatcher(None, _sound("".join(cyr)),
                                              _sound("".join(gap))).ratio() < (0.75 if starts else 0.5):
                    out = cyr + out  # "Маврикия Pravind ...": not part of the name
                else:
                    out = gap + out
            if ok and all(isinstance(x, str) for x in out):
                return " ".join(out)
        return m.group(0)
    learned = {}
    text = re.sub(rf"{tok}(?:\s+{tok})+", sub, text)
    # the same surname elsewhere in the text, alone or declined ("Джагнова")
    for cyr, lat in learned.items():
        stem = cyr
        for e in _RU_ENDINGS:
            if cyr.lower().endswith(e) and len(cyr) - len(e) >= 3:
                stem = cyr[:-len(e)]
                break
        text = re.sub(rf"\b{re.escape(stem)}(?:{'|'.join(_RU_ENDINGS)})?\b", lat, text)
    return text


_LINKS = {"d", "de", "du", "des", "la", "le", "les", "l", "aux", "en", "sur", "or"}


def _latinize_compounds(text, original, lenient=False):
    """Hyphenated Cyrillic place names -> the Latin original:
    "Пудр-Дор-Виллаж" -> "Poudre d'Or Village", "Роуз-Хилл" -> "Rose-Hill"."""
    from difflib import SequenceMatcher
    toks = list(re.finditer(r"[A-Za-zÀ-ÿ]+", original))
    spans = {}

    def put(key, span):
        # prefer the article's own spelling over a CAPITALS headline turned
        # into Title case: "Flic-en-Flac" over "Flic-En-Flac", "Sorèze" over "Soreze"
        old = spans.get(key)
        natural = lambda x: (not x.isascii()) + any(w[0].islower() for w in re.split(r"[\s'’-]", x) if w)
        if old is None or natural(span) > natural(old):
            spans[key] = span
    for i, t in enumerate(toks):
        followed = original[t.end():t.end() + 1] == "-"  # Sainte-Croix is a name
        if not t.group(0)[0].isupper() or (_not_name(t.group(0)) and not followed):
            continue
        for j in range(i + 1, min(i + 5, len(toks))):
            last = toks[j].group(0)
            gap = original[toks[j - 1].end():toks[j].start()]
            if not re.fullmatch(r"\s?[-'’]?\s?", gap) or gap == "":
                break
            if not last[0].isupper() and last.lower() not in _LINKS:
                break
            if last[0].isupper():
                words = [toks[k].group(0) for k in range(i, j + 1)]
                span = original[t.start():toks[j].end()]
                put(_sound("".join(words)), span)
                put(_sound("".join(words[:-1])) + _sound_latin(last), span)
                # silent final e in each part: Rose-Hill ~ Роуз-Хилл
                put("".join(re.sub(r"(?<=..)e$", "", _sound(w)) for w in words), span)
                put("".join(_fr(w) for w in words), span)  # Sainte-Croix ~ Сент-Круа
                put("".join(_fr(w, oi=False) for w in words), span)  # Pointe read as Поинт

    def sub(m):
        word = m.group(0)
        stems = [word] + [word[:-len(e)] for e in _RU_ENDINGS if word.lower().endswith(e)]
        best, score = None, 0
        for st in stems:
            key = _sound(st)
            for k, span in spans.items():
                if k[:1] != key[:1] or abs(len(k) - len(key)) > 3:
                    continue
                r = SequenceMatcher(None, key, k).ratio()
                if r > score:
                    best, score = span, r
        return best if score >= (0.72 if lenient else 0.8) else word
    def sub_mixed(m):  # only words with some Cyrillic in them
        return sub(m) if re.search(r"[А-Яа-яЁё]", m.group(0)) else m.group(0)
    return re.sub(r"\b[A-ZÀ-ÝА-ЯЁ][\wÀ-ÿ’']*(?:-[\wÀ-ÿ’']+)+", sub_mixed, text)


def _decline(ru, prep):
    """Russian place name after a preposition: в Ирландии, из Ирландии, на Коморах."""
    low = prep.lower()
    if " " in ru or re.search(r"[оиуэюе]$", ru):  # ДР Конго, Сочи: not declined
        return ru
    loc = low in ("в", "во", "на", "о", "об")
    gen = low in ("из", "с", "со", "до", "для", "от", "у", "около", "возле", "из-за")
    if low in ("через", "про", "сквозь"):  # accusative: через Индию
        return (ru[:-2] + "ию" if ru.endswith("ия") else ru[:-1] + "у" if ru.endswith("а")
                else ru[:-1] + "ю" if ru.endswith("я") else ru)
    if not (loc or gen):
        return ru
    if ru.endswith("ия"):
        return ru[:-2] + "ии"
    if ru.endswith("а"):
        return ru[:-1] + ("е" if loc else ("и" if ru[-2:-1] in "гкхжшчщ" else "ы"))
    if ru.endswith("я"):
        return ru[:-1] + ("е" if loc else "и")
    if ru.endswith("ы") or ru.endswith("и"):
        return ru[:-1] + ("ах" if loc else "")
    if ru.endswith("ь"):
        return ru[:-1] + ("и" if loc else "я")
    return ru + ("е" if loc else "а")


_COUNTRIES = None


def _countries():
    """Country names in French and English -> Russian, from the CLDR data in
    Babel (complete and maintained, not a hand-made list)."""
    global _COUNTRIES
    if _COUNTRIES is None:
        _COUNTRIES = {}
        try:
            from babel import Locale
            ru = Locale("ru").territories
            fix = {"CD": "ДР Конго", "CG": "Республика Конго", "US": "США",
                   "GB": "Великобритания", "RE": "Реюньон", "MU": "Маврикий",
                   "SC": "Сейшелы", "AE": "ОАЭ", "KR": "Южная Корея"}
            for lang in ("fr", "en"):
                for code, name in Locale(lang).territories.items():
                    if len(code) == 2 and code.isalpha() and code in ru:
                        name = re.sub(r"^(La |Le |Les |L’|L')", "", name)
                        _COUNTRIES[name.lower()] = fix.get(code, ru[code])
            _COUNTRIES.update({"congo": "Конго", "rdc": "ДР Конго", "usa": "США",
                               "uk": "Великобритания", "émirats arabes unis": "ОАЭ"})
        except Exception as e:
            print(f"Country names unavailable: {e}")
    return _COUNTRIES


def _with_countries(text, names):
    """Add any country written in Latin in the text to the names list as a
    foreign place, so it gets its Russian name."""
    known = {n.get("orig", "").lower() for n in names}
    # a country word inside a person's name (Jean France) is not a country
    for n in names:
        if n.get("type") == "person":
            known |= {w.lower() for w in n.get("orig", "").split()}
    text = re.sub(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]+\s(?=[A-ZÀ-Ý])|(?<=[A-Za-zÀ-ÿ]\s)[A-ZÀ-Ý][\wÀ-ÿ'’-]+", " ", text or "")
    extra = []
    for w in set(re.findall(r"[A-ZÀ-Ý][\wÀ-ÿ'’-]+(?:\s[A-ZÀ-Ý][\wÀ-ÿ'’-]+)?", text or "")):
        for cand in (w, w.split()[0]):
            ru = _countries().get(cand.lower())
            if ru and cand.lower() not in known and ru != "Маврикий":
                extra.append({"orig": cand, "type": "foreign", "ru": ru})
                known.add(cand.lower())
    return list(names) + extra


def apply_names(text, names, source_text=""):
    """Make the text follow the names list the model wrote first:
    foreign places in Russian, people/organisations/Mauritian places in Latin."""
    if not text:
        return text
    # some models write non-breaking hyphens and spaces (Poudre‑d’Or, 20 %)
    text = text.replace("\u2011", "-").replace("\u2010", "-").replace("\u00a0", " ").replace("\u202f", " ")
    names = _with_countries(text, names)
    latin_names = [n["orig"].strip() for n in names
                   if n.get("type") != "foreign" and not n.get("ru")
                   and re.search(r"[A-Za-zÀ-ÿ]", n["orig"])]
    for n in names:
        ru, orig = (n.get("ru") or "").strip(), n["orig"].strip()
        if n.get("type") != "foreign" or not ru or not re.search(r"[A-Za-zÀ-ÿ]", orig):
            continue
        # a Latin foreign name left in the text -> the Russian one, declined
        def put(m, ru=ru):
            prev = m.group(1) or ""
            after = m.string[m.end():m.end() + 30]
            if re.match(r"[A-Za-zÀ-ÿ]", prev[-1:] or "x") and prev[:1].isupper() or \
                    re.match(r"\s[A-ZÀ-Ý][a-zà-ÿ]", after):
                return m.group(0)  # part of a Latin name: Jean France, Air Seychelles
            name = ru[3:] if ru.startswith("ДР ") and prev.lower().startswith("республик") else ru
            if prev == "в" and re.match(r"(Фр|Вр|Вл|Вс)", name):
                prev = "во"  # во Франции
            return (prev + " " if prev else "") + (_decline(name, prev) if prev else name)
        text = re.sub(rf"(?:\b(\w+(?:-\w+)?)\s+)?(?<![\w-]){re.escape(orig)}(?![\w-])", put, text)
    # Organisation names are full of ordinary words (Constitutional Review
    # Commission, Championnats d'Afrique): matching them word by word turns
    # «Конституции» into «Constitutional». They are only matched whole, as a
    # hyphenated name (Eco-Sud ~ Эко-Суд); single words come from people and places.
    # (the same for multi-word places: Musée de l'esclavage, Jardin de Pamplemousses)
    orgs = [n["orig"].strip() for n in names if not n.get("ru") and (
        n.get("type") == "org" or (n.get("type") == "mu_place" and " " in n["orig"].strip()))]
    latin_names = [n for n in latin_names if n not in orgs]
    if orgs:
        text = _latinize_compounds(text, ".\n".join(orgs), lenient=True)
    if latin_names:
        # Cyrillic spellings of the listed names -> Latin; only real names are
        # candidates now, so matching can be lenient
        text = latinize_names(text, ".\n".join(latin_names), lenient=True)
    elif source_text:
        text = latinize_names(text, source_text)
    return text


def latinize_names(text, original, lenient=False):
    """Replace Cyrillic spellings of names from the original article with the
    Latin original ("Риши" -> "Rishi", "Кандосе" -> "Candos")."""
    if not text or not original:
        return text
    # Le Mauricien writes headlines in CAPITALS ("SOREZE : ..."): match as Title case
    original = re.sub(r"\b[A-ZÀ-Ý]{3,}(?:-[A-ZÀ-Ý]+)*\b",
                      lambda m: "-".join(p.capitalize() for p in m.group(0).split("-")), original)
    # A headline in CAPITALS copied from Le Mauricien ("ФЛИК-АН-ФЛАК"): compare
    # in Title case so it can be matched (short abbreviations like МВД stay)
    caps = {}

    def title(m):
        t = "-".join(p.capitalize() for p in m.group(0).split("-"))
        caps[t] = m.group(0)
        return t
    # only names: hyphenated (ФЛИК-АН-ФЛАК) or a headline place before a colon (СОРЕЗ:)
    text = re.sub(r"\b[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})+\b|\b[А-ЯЁ]{4,}\b(?=\s*:)", title, text)
    text = _align_names(_latinize_compounds(text, original, lenient), original)
    from difflib import SequenceMatcher
    def at_start(src, pos):  # first word of a line or sentence (or after a colon)
        return re.search(r"(^|[.!?:…«\"—–-]\s*|\n\s*)$", src[max(0, pos - 3):pos]) is not None \
            or pos == 0
    names, mid = {}, {}  # mid: the word was capitalised mid-sentence, so surely a name
    for m in re.finditer(r"\b[A-ZÀ-Ý][a-zà-ÿ]{2,}(?:-[A-ZÀ-Ý][a-zà-ÿ]+)*\b", original):
        w = m.group(0)
        if _not_name(w):
            continue
        for k in _latin_keys(w):
            if k not in names or (names[k].isascii() and not w.isascii()):
                names[k] = w  # prefer the accented spelling: Sorèze over SOREZE
            nxt = re.match(r"\s+([A-ZÀ-Ý][a-zà-ÿ]+)", original[m.end():])
            in_name = (bool(nxt) and not _not_name(nxt.group(1))  # "Gautam Singh"
                       or (re.match(r"\s*:", original[m.end():]) is not None  # "Vacoas : ..."
                           and _plain(w) in _MU_PLACES))  # but not "Tourisme : ..."
            mid[k] = mid.get(k, False) or in_name or not at_start(original, m.start())

    def best(word, initial):
        # A word seen only at the start of a sentence ("Examens reportés") may be
        # an ordinary word: use it only for a capitalised word mid-sentence
        pool = {k: v for k, v in names.items() if mid[k] or not initial or lenient}
        cands = [word] + [word[:-len(e)] for e in _RU_ENDINGS
                          if word.lower().endswith(e) and len(word) - len(e) >= 3]
        found, score = None, 0
        for c in cands:
            key = _sound(c)
            if len(key) < 3:
                continue
            for k, latin in pool.items():
                # same first sound, and the second matches too unless both are
                # vowels (Джагнот ~ Jugnauth), so "Франция" never meets "Fenty"
                same = lambda a, b: a == b or (a in _VOWELS and b in _VOWELS)
                if not same(k[0], key[0]) or abs(len(k) - len(key)) > 2 or \
                        not same(k[1:2], key[1:2]):
                    continue
                # Short words need an exact match: "После" ~ "Paul" scores 0.86
                if min(len(key), len(k)) <= 4 and key != k and not (
                        lenient and SequenceMatcher(None, key, k).ratio() >= 0.7):
                    continue
                r = SequenceMatcher(None, key, k).ratio()
                # Vowels differ, consonants identical: English spelling of an
                # Indian name (Hurreeram ~ Харрирэм)
                sk = lambda x: re.sub(r"[aeiou]", "", x)
                if not initial and len(key) >= 6 and len(sk(key)) >= 3 and sk(key) == sk(k) \
                        and len(key) == len(k) and all(  # vowels differ only in
                            x == y or {x, y} in _VOWEL_SWAPS  # English-style ways
                            for x, y in zip(key, k)):
                    r = max(r, 0.9)
                if r > score:
                    found, score = latin, r
        # Longer words tolerate more spelling difference
        need = 0.82 if len(_sound(word)) >= 6 else 0.88
        if initial:  # a sentence-initial word is usually an ordinary word
            need = max(need, 0.9)
        if lenient:  # candidates are only the article's real names
            need -= 0.12 if len(_sound(word)) >= 6 else 0.16
        return found if score >= need else None

    def sub(m):
        word = m.group(0)
        if re.match(r"Маврик", word):
            return word  # the country itself is always in Russian
        latin = best(word, at_start(text, m.start()))
        return latin if latin else word
    # Capitalised Cyrillic words (not at the start of a sentence: those are
    # usually ordinary words, but names are still checked)
    # (not a part of a hyphenated name: "Бай-дю-Томбо" is handled as a whole)
    text = re.sub(r"(?<!-)\b[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)*\b(?!-)", sub, text)
    # a name may have become half-Latin only now ("Ричард Сюне" -> "Richard
    # Сюне"): align once more
    text = _align_names(text, original)
    for t, c in caps.items():  # no Latin match: keep the headline as it was
        text = text.replace(t, c)
    return text


_NOT_NAMES.update(_sound(w) for w in _NOT_NAMES_FR)


# Mauritian towns and villages: a headline starting "Vacoas :" names a place,
# while "Tourisme :", "Éducation :" are just rubrics
_MU_PLACES = set("""port-louis curepipe vacoas phoenix vacoas-phoenix quatre-bornes rose-hill
beau-bassin beau-bassin-rose-hill moka mahebourg flacq centre-de-flacq goodlands triolet
grand-baie pamplemousses riviere-du-rempart riviere-noire tamarin flic-en-flac bambous
souillac rose-belle plaisance chemin-grenier surinam bel-air bel-ombre le-morne
trou-aux-cerises trou-aux-biches pereybere cap-malheureux albion coromandel petite-riviere
camp-levieux cite-la-cure roche-bois sainte-croix plaine-verte vallee-pitot tranquebar
grande-riviere grand-river grand-gaube poste-de-flacq belle-mare trou-d'eau-douce
saint-pierre quartier-militaire montagne-blanche nouvelle-france new-grove l'escalier
rodrigues agalega chagos ebene reduit floreal candos midlands mare-aux-vacoas lalmatie
solitude la-louise sainte-croix soreze glen-park cassis stanley camp-de-masque camp-diable chamarel case-noyale
beau-champ olivia laventure mapou piton terre-rouge baie-du-tombeau arsenal""".split())


def _plain(word):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", word.lower())
                   if not unicodedata.combining(c))


def _not_name(word):
    """A capitalised French word that is not a name (Français, Ministre...)."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", word.lower())
                    if not unicodedata.combining(c))
    return plain in _NOT_NAMES_PLAIN or _sound(word) in _NOT_NAMES


_NOT_NAMES_PLAIN = set(_NOT_NAMES_FR)


def fix_caps_title(title, original_title):
    """Le Mauricien writes some headlines in CAPITALS; the model copies that."""
    words = re.findall(r"[А-ЯЁA-ZÀ-Ý][А-ЯЁA-ZÀ-Ýа-яёa-zà-ÿ-]*", title)
    caps = [w for w in words if w.isupper() and len(w) > 2]
    if len(caps) < 3:
        return title
    known = {w.lower(): w for w in re.findall(r"\b[A-ZÀ-Ý][a-zà-ÿ]+\b", original_title or "")}

    def fix(m):
        w = m.group(0)
        if re.search(r"[А-ЯЁ]", w):
            return w.lower()
        if w.lower() in ("de", "du", "des", "la", "le", "les", "et", "au", "aux", "en", "sur", "pour"):
            return w.lower()
        if len(w) <= 4:          # acronyms: SAMU, MSM, PTR
            return w
        return known.get(w.lower(), w.capitalize())
    out = re.sub(r"\b[А-ЯЁA-ZÀ-Ý]{2,}\b", fix, title)
    return out[:1].upper() + out[1:]


def _name_fixes(changes, original):
    """From the proofreader's «было → стало» list, keep only Cyrillic names
    replaced by a Latin spelling that really appears in the original article."""
    orig = original.lower()
    fixes = []
    for c in changes:
        if not isinstance(c, str):
            continue
        parts = re.split(r"\s*(?:→|->)\s*", c.strip().strip("«»\"'"), maxsplit=1)
        if len(parts) != 2:
            continue
        old, new = (x.strip().strip("«»\"'") for x in parts)
        if (not old or not new or _LATIN.search(old) or re.search(r"\d", old + new)
                or re.search(r"[А-Яа-яЁё]", new)):
            continue
        words = _LATIN.findall(new)
        if words and all(w.lower() in orig for w in words):
            fixes.append((old, new))
    return sorted(fixes, key=lambda f: -len(f[0]))  # «Элоди Томас» before «Элоди»


def _apply_fixes(text, fixes):
    for old, new in fixes:
        text = re.sub(rf"(?<![А-Яа-яЁё]){re.escape(old)}(?![А-Яа-яЁё])", new, text)
    return text


LEFTOVER_PROMPT = (
    "Ты редактор русскоязычного новостного канала. В заголовке или тексте "
    "остались иностранные слова, которые не являются именами людей, названиями "
    "мест или компаний: они перечислены ниже. Переведи на русский именно их, "
    "по смыслу и согласовав с предложением (Pesticides → Пестициды, "
    "Agricultural Services → сельскохозяйственная служба, Cour du Nord de "
    "Port-Louis → Северный суд Port-Louis, walk-out → "
    "демонстративный уход). Всё остальное, включая имена латиницей, оставь "
    "без изменений. Верни строго JSON: {\"title\": \"...\", \"summary\": \"...\"}."
)


def _latin_words(text):
    return re.findall(r"[A-Za-zÀ-ÿ](?:[A-Za-zÀ-ÿ'’-]*[A-Za-zÀ-ÿ])?", text or "")


_MIXED = re.compile(r"\b(?=[\wÀ-ÿ'’-]*[A-Za-zÀ-ÿ])(?=[\wÀ-ÿ'’-]*[А-Яа-яЁё])[\wÀ-ÿ'’-]+")


def fix_mixed_words(text, names):
    """A word half in Latin, half in Cyrillic ("Bérengerнже"): keep the listed
    name it starts with, drop the rest."""
    tokens = sorted({w for n in names if n.get("type") != "foreign"
                     for w in _latin_words(n.get("orig", ""))}, key=len, reverse=True)

    def sub(m):
        w = m.group(0)
        lat = re.match(r"[A-Za-zÀ-ÿ'’-]+", w)
        if lat:
            for t in tokens:
                if lat.group(0).lower().startswith(t.lower()[:max(4, len(t) - 2)]):
                    return t
        return w
    return _MIXED.sub(sub, text or "")


# A name starting with one of these is a state body: it is written in Russian
# (суд, министерство, полиция...), even if the model listed it as a Latin name
_STATE_BODY = re.compile(r"^(cour|tribunal|court|minist\w*|assembl\w*|parlement|parliament|"
                         r"police|commission|commissioner|conseil|council|municipalit\w*|"
                         r"district|bureau|office|h[oô]pital|hospital|prison|département|"
                         r"department|direction|service|agence|agency|autorité|authority|"
                         r"cabinet|gouvernement|government|senat|sénat)\b", re.I)


def _state_body(n):
    return n.get("type") in ("org", "mu_place") and bool(_STATE_BODY.match(n.get("orig", "").strip()))


def foreign_leftovers(title, summary, names, original=""):
    """Latin words that are not part of a listed name or an abbreviation."""
    names = [n for n in names if not _state_body(n)]  # Cour du Nord... must be translated
    text = f"{title} {summary}"
    allowed = {"dr", "me", "mme", "st", "ste", "mr", "sir"}
    # proper names the model did not list: capitalised in the middle of a
    # sentence in the article (but not just a word of an organisation's name)
    whole_only = lambda n: n.get("type") == "org" or (n.get("type") == "mu_place" and " " in n.get("orig", "").strip())
    org_words = {w.lower() for n in names if whole_only(n)
                 for w in _latin_words(n.get("orig", ""))}
    for m in re.finditer(r"(?<=[a-zà-ÿ,;]\s)([A-ZÀ-Ý][\w'’-]+)", original or ""):
        w = m.group(1)
        if w.lower() not in org_words and not _not_name(w) and w.lower() not in _countries():
            allowed |= {x.lower() for x in _latin_words(w)}
    for n in names:
        if n.get("type") == "foreign" or n.get("ru"):
            continue
        if whole_only(n):  # an organisation or multi-word place counts only as a whole
            # name, or as 2+ of its words in a row ("Silver Bank" of "Silver Bank Ltd")
            ws = n.get("orig", "").split()
            parts = sorted({" ".join(ws[i:j]) for i in range(len(ws))
                            for j in range(i + 2, len(ws) + 1)} | {n.get("orig", "")}, key=len, reverse=True)
            for part in parts:
                if part.strip():
                    text = re.sub(re.escape(part), " ", text, flags=re.I)
        else:  # people and places: a surname alone is fine too
            allowed |= {w.lower() for w in _latin_words(n.get("orig", ""))}
    left = [w for w in _MIXED.findall(text)]  # half-Latin words always need fixing
    for w in _latin_words(_MIXED.sub(" ", text)):
        if w.lower() in allowed or (w.isupper() and len(w) <= 6) or re.fullmatch(r"[A-Z]{1,3}\d*", w):
            continue
        if w not in left:
            left.append(w)
    return left


def fix_leftovers(title, summary, names, original=""):
    """Systematic last check: any foreign word outside the names list is
    sent back to the proofreading model to be translated."""
    if not names:
        return title, summary
    left = foreign_leftovers(title, summary, names, original)
    if not left:
        return title, summary
    print(f"  foreign words left: {', '.join(left)}")
    free = [m for m in PROOF_MODELS if m not in _llm_state["exhausted"]
            and not (m.startswith("GigaChat") and (_giga["dead"] or _giga_over_cap(m)))
            and (m.startswith("GigaChat") or (LLM_KEY and not _llm_state["disabled"]))]
    msg = (f"Заголовок: {title}\nТекст: {summary}\n\nИностранные слова, которые нужно "
           f"перевести: {', '.join(left)}")
    for model in free:
        try:
            r = _llm_call(model, [{"role": "system", "content": LEFTOVER_PROMPT},
                                  {"role": "user", "content": msg}], 700)
            if not r.ok:
                continue
            d = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        except Exception as e:
            print(f"  leftover fix failed ({model}): {e}")
            continue
        new_t, new_s = (d.get("title") or "").strip(), (d.get("summary") or "").strip()
        ok_t = new_t and _safe_edit(title, new_t, original, left)
        ok_s = not summary or (new_s and _safe_edit(summary, new_s, original, left))
        out_t = new_t if ok_t else title
        out_s = new_s if (ok_s and summary) else summary
        still = foreign_leftovers(out_t, out_s, names, original)
        print(f"  leftovers fixed by {model}; still left: {', '.join(still) or 'none'}")
        return out_t, out_s
    return title, summary


def proofread(title, summary, original="", names=()):
    """Second pass that fixes Russian language errors. Returns (title, summary)."""
    free = [m for m in PROOF_MODELS if m not in _llm_state["exhausted"]
            and not (m.startswith("GigaChat") and (_giga["dead"] or _giga_over_cap(m)))
            and (m.startswith("GigaChat") or (LLM_KEY and not _llm_state["disabled"]))]
    if not free:
        return title, summary
    listing = "\n".join(f"{n['orig']} → {n['ru']}" if n.get("ru") else n["orig"] for n in names)
    foreign = [n["orig"] for n in names if n.get("ru")]  # these may leave the text
    messages = [{"role": "system", "content": PROOF_PROMPT},
                {"role": "user", "content": f"Заголовок: {title}\nТекст: {summary}\n\n"
                                            + (f"Обязательные написания (латиницей — точно так; "
                                               f"зарубежные места — по-русски, склоняя):\n{listing}\n\n"
                                               if listing else "")
                                            + f"Оригинал (для написания имён):\n{original[:1200]}"}]
    d = None
    for model in free:
        try:
            r = _llm_call(model, messages, 700)
            if r.status_code == 429 and not model.startswith("GigaChat"):
                wait = float(r.headers.get("retry-after", "0") or 0)
                if "per minute" in r.text and not wait:
                    wait = 30
                if not 0 < wait <= 60:
                    _llm_state["exhausted"].add(model)
                    print(f"Proofreading: {model} daily limit")
                    continue
                time.sleep(wait + 1)
                r = _llm_call(model, messages, 700)
            if not r.ok:
                print(f"Proofreading {model} failed {r.status_code}: {r.text[:120]}")
                if model.startswith("GigaChat") and r.status_code in (402, 429):
                    _llm_state["exhausted"].add(model)
                continue
            d = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
            if model != PROOF_MODELS[0]:
                print(f"  proofread by {model}")
            break
        except Exception as e:
            print(f"Proofreading {model} failed: {e}")
    if d is None:
        return title, summary
    new_t, new_s = (d.get("title") or "").strip(), (d.get("summary") or "").strip()
    # An edit that turned a Latin name into Cyrillic (Paul Bérenger -> Поль Беренже)
    # is rejected as it is; repairing it produced half-and-half words
    # ("Bérengerнже"). Only stray non-breaking hyphens/spaces are normalised.
    norm = lambda t: t.replace("\u2011", "-").replace("\u00a0", " ").replace("\u202f", " ")
    new_t, new_s = norm(new_t), norm(new_s)
    out_t = new_t if _safe_edit(title, new_t, original, foreign) else title
    out_s = new_s if (not summary or _safe_edit(summary, new_s, original, foreign)) else summary
    # A rejected whole-text edit can still carry safe name fixes
    # («Элоди Томас» → «Élodie Thomas»): apply those one by one
    if out_t == title or out_s == summary:
        fixes = _name_fixes(d.get("changes") or [], original)
        if out_t == title:
            out_t = _apply_fixes(title, fixes)
        if out_s == summary:
            out_s = _apply_fixes(summary, fixes)
    if not summary:
        out_s = summary
    if (out_t, out_s) != (title, summary):
        print(f"  proofread: {'; '.join(d.get('changes') or [])[:300]}")
    elif (new_t and new_t != title) or (new_s and new_s != summary):
        print("  proofread: edit rejected (changed numbers, names or length)")
    return out_t, out_s


def load_recent():
    """Headlines posted in the last RECENT_HOURS, oldest first."""
    try:
        items = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    cutoff = time.time() - RECENT_HOURS * 3600
    return [x for x in items if x.get("t", 0) >= cutoff][-RECENT_MAX:]


def save_recent(items):
    RECENT_FILE.write_text(json.dumps(items[-RECENT_MAX:], ensure_ascii=False,
                                      indent=0), encoding="utf-8")


def build_post(source, entry, link, recent=(), force=False):
    """Check for repeats, then Russian title + summary (LLM) or translated
    headline (fallback). Returns a dict; "dup" is set for a repeat."""
    orig_title = entry.get("title", "").strip()
    body, page_image = article_text(link)
    # Some sites block article pages; fall back to the text inside the feed
    feed_html = ""
    if entry.get("content"):
        feed_html = entry["content"][0].get("value", "")
    feed_html = feed_html or entry.get("summary", "")
    feed_text = re.sub(r"(The post|L[’']article) .{0,300}(appeared first on|est apparu en premier sur).*$",
                       "", _strip_html(feed_html), flags=re.S).strip()
    if len(feed_text) > len(body):
        body = feed_text
    if not page_image:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', feed_html)
        page_image = html.unescape(m.group(1)) if m else None
    post = {"orig": orig_title, "image": page_image, "dup": None,
            "title": orig_title, "summary": "", "text": ""}
    # Site temporarily down and no text in the feed: try again next check
    if not _fetch["ok"] and len(body) < MIN_TEXT_FOR_SUMMARY and not force:
        post["retry"] = True
        return post

    # 1) repeat? (checked first, so no tokens are spent on summarizing repeats)
    lead = re.sub(r"^(Publicité\s*)+", "", body).strip()
    post["dup"] = find_duplicate(orig_title, lead, list(recent))
    if post["dup"]:
        return post

    # 2) summary. Without real article text it would be invented: title only
    if len(body) >= MIN_TEXT_FOR_SUMMARY and CLASSIFY_ENABLED:
        c = classify(orig_title, body)
        if c and not (c[0] and c[1]):
            post["foreign"] = True
            print(f"  {'not about Mauritius' if not c[0] else 'not news (portrait/lifestyle)'}"
                  f" (first pass): {orig_title[:100]}")
            return post
    result = summarize(orig_title, body) if len(body) >= MIN_TEXT_FOR_SUMMARY else None
    if result is None and body and len(body) < MIN_TEXT_FOR_SUMMARY:
        print(f"Too little text ({len(body)} chars), title only: {link}")
    if result:
        title, summary, local = result
        names = _llm_state.get("names") or []
        if local is not True:
            post["foreign"] = True  # skipped, same handling as foreign news
            print(f"  {'not about Mauritius' if local == 'foreign' else 'not news (portrait/lifestyle)'}: "
                  f"{orig_title[:100]}")
            return post
    else:
        names = []
        title = translate(orig_title)
        summary = translate(feed_text[:450]) if feed_text and len(feed_text) > 40 else ""
    title, summary = proofread(title, summary, f"{orig_title}\n{body[:1500]}", names)
    title, summary = fix_russian(title), fix_russian(summary)
    source_text = f"{orig_title}\n{body}"
    title = fix_caps_title(apply_names(title, names, source_text), orig_title)
    summary = apply_names(summary, names, source_text)
    title, summary = fix_mixed_words(title, names), fix_mixed_words(summary, names)
    title, summary = fix_leftovers(title, summary, names, source_text)
    post["title"], post["summary"] = title, summary

    footer = f'🔗 <a href="{html.escape(link)}">{html.escape(source)}</a>'
    head = f"<b>{html.escape(title)}</b>"
    # Photo captions are capped at 1024 characters; leave room for markup
    room = 1000 - len(head) - len(footer) - 4
    if summary and summary != title and room > 50:
        if len(summary) > room:
            summary = summary[:room - 1].rsplit(" ", 1)[0] + "…"
        post["text"] = f"{head}\n\n{html.escape(summary)}\n\n{footer}"
    else:
        post["text"] = f"{head}\n\n{footer}"
    return post


def _looks_like_image(data):
    """True for JPEG/PNG/GIF/WebP bytes, whatever content-type the site sent."""
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:6] in (b"GIF87a", b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _download_image(photo, referer=None):
    """Picture bytes, or None (the reason is printed to the log)."""
    headers = dict(UA)
    headers["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
    if referer:
        headers["Referer"] = referer
    try:
        img = requests.get(photo, timeout=30, headers=headers)
    except Exception as e:
        print(f"Photo download failed: {type(e).__name__} {e}")
        return None
    ctype = img.headers.get("content-type", "")
    if not img.ok:
        print(f"Photo download failed: HTTP {img.status_code} for {photo}")
        return None
    if not (ctype.startswith("image") or _looks_like_image(img.content)):
        print(f"Photo download failed: got {ctype or 'unknown type'}, "
              f"{len(img.content)} bytes, not a picture: {photo}")
        return None
    return img.content


def send(text, photo=None, referer=None):
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    if photo:
        # spaces and accented letters in file names break Telegram's fetch
        photo = requests.utils.requote_uri(photo.strip())
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": CHANNEL_ID, "photo": photo,
                  "caption": text, "parse_mode": "HTML"},
            timeout=30,
        )
        if r.ok:
            return
        print(f"Telegram couldn't fetch the photo ({r.text[:150]}), uploading it")
        # Telegram couldn't fetch the picture itself: download and upload it
        data = _download_image(photo, referer)
        if data:
            try:
                r = requests.post(
                    f"{base}/sendPhoto",
                    data={"chat_id": CHANNEL_ID, "caption": text,
                          "parse_mode": "HTML"},
                    files={"photo": ("photo.jpg", data)},
                    timeout=60,
                )
                if r.ok:
                    return
                print(f"Photo upload rejected: {r.text[:200]}")
            except Exception as e:
                print(f"Photo upload failed: {e}")
        print(f"Photo failed: {photo} — sending text only")
    r = requests.post(
        f"{base}/sendMessage",
        data={"chat_id": CHANNEL_ID, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=30,
    )
    r.raise_for_status()


def raw_items(xml):
    """Map guid/link -> (image, timestamp) straight from the raw XML.
    Needed because Defimedia uses a plain <image> tag and French weekday
    names in dates ("mar, 13 Oct 2026"), which feedparser can't read."""
    out = {}
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
            return html.unescape(m.group(1).strip()) if m else None
        ts = 0.0
        date = tag("pubDate")
        if date:
            try:
                ts = parsedate_to_datetime(date.split(",", 1)[-1].strip()).timestamp()
            except Exception:
                pass
        info = (tag("image"), ts)
        for key in (tag("guid"), tag("link")):
            if key:
                out[key] = info
    return out


def item_image(entry, extra):
    for key in ("media_content", "media_thumbnail"):
        val = entry.get(key)
        if isinstance(val, list) and val and val[0].get("url"):
            return val[0]["url"]
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
            return link.get("href")
    return extra[0]


def _meta(page, key):
    m = (re.search(rf'<meta[^>]+(?:property|name)=["\']{key}["\'][^>]+content=["\']([^"\']*)', page)
         or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{key}["\']', page))
    return html.unescape(m.group(1)).strip() if m else ""


def _parse_ts(value):
    """Timestamp from ISO-ish dates like '2026-10-13 19:00:00' (Mauritius time)."""
    from datetime import datetime
    v = (value or "").strip().replace(" ", "T")
    if not v:
        return 0.0
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", v):
        v += MU_TZ
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def page_entry(url):
    """Build a feed-like entry from an article page (title, date, picture)."""
    try:
        page = requests.get(url, timeout=30, headers=UA).text
    except Exception as e:
        print(f"Could not open {url}: {e}")
        return None
    title = _meta(page, "og:title")
    if not title:
        m = re.search(r"<title>(.*?)</title>", page, re.S)
        title = html.unescape(m.group(1)).split("|")[0].strip() if m else ""
    ts = _parse_ts(_meta(page, "article:published_time"))
    return {"title": title, "link": url, "id": url, "summary": "",
            "_image": _meta(page, "og:image") or None, "_ts": ts or time.time()}


def collect(source, url, kind, pattern, seen_set):
    """Unseen items of one source as (entry, uid, image, timestamp), and all uids."""
    out, uids = [], []
    r = requests.get(url, timeout=30, headers=UA)
    if kind == "wp":
        for p in r.json():
            link = p.get("link", "")
            uid = f"wp:{source}:{p.get('id')}"
            uids.append(uid)
            if uid not in seen_set:
                e = {"title": html.unescape(re.sub(r"<[^>]+>", "", p.get("title", {}).get("rendered", ""))),
                     "link": link, "id": uid, "summary": ""}
                out.append((e, uid, None, _parse_ts(p.get("date"))))
        return out, uids
    if kind == "html":
        links = []
        for m in re.finditer(r'href=["\']([^"\'#?]+)', r.text):
            u = urljoin(url, html.unescape(m.group(1))).rstrip("/")
            if re.match(pattern, u) and u not in links:
                links.append(u)
        uids = links
        new = [u for u in links if u not in seen_set]
        # pages are opened later, only for links that will really be used
        for i, u in enumerate(new):
            out.append((None, u, None, -i))  # page order as a provisional date
        return out, uids
    extras = raw_items(r.text)
    for e in feedparser.parse(r.text).entries:
        uid = e.get("id") or e.get("link")
        uids.append(uid)
        if uid and uid not in seen_set:
            img, ts = extras.get(uid) or extras.get(e.get("link")) or (None, 0.0)
            if not ts and e.get("published_parsed"):
                ts = time.mktime(e.published_parsed)
            out.append((e, uid, img, ts))
    return out, uids


def newest_entry(src):
    """Newest item of a source as (entry, link, picture) - for test modes."""
    source, url = src[0], src[1]
    kind = src[2] if len(src) > 2 else "rss"
    items, _ = collect(source, url, kind, src[3] if len(src) > 3 else None, set())
    if not items:
        return None, None, None
    if kind == "html":
        e = page_entry(items[0][1])
        return e, items[0][1], e and e["_image"]
    e, uid, img, ts = max(items, key=lambda x: x[3])
    return e, e.get("link", "").replace("http://", "https://", 1), img


def main():
    seen = load_seen()
    first_run = seen is None
    seen = seen or []
    seen_set = set(seen)

    new_items = []
    for src in FEEDS:
        source, url = src[0], src[1]
        kind = src[2] if len(src) > 2 else "rss"
        pattern = src[3] if len(src) > 3 else None
        try:
            items, uids = collect(source, url, kind, pattern, seen_set)
        except Exception as ex:
            print(f"Could not read {source}: {type(ex).__name__} {ex}")
            continue
        # A source we have never seen before (just added to FEEDS): remember
        # all its current items and post only the newest one, not the backlog.
        fresh = not first_run and uids and not any(u in seen_set for u in uids)
        if fresh:
            if kind == "html":
                items = items[:1]  # top of the site's page
            else:
                items.sort(key=lambda x: x[3])
                items = items[-1:]
            for u in uids:
                if u not in seen_set:
                    seen.append(u)
                    seen_set.add(u)
            print(f"New source {source}: skipped backlog, posting newest only")
        if kind == "html":
            items = items[:MAX_POSTS_PER_RUN]
            opened = []
            for e, uid, img, ts in items:
                e = page_entry(uid)
                if e and e["title"]:
                    opened.append((e, uid, e["_image"], e["_ts"]))
                elif uid not in seen_set:
                    seen.append(uid)
                    seen_set.add(uid)
            items = opened
        for e, uid, img, ts in items:
            new_items.append((source, e, uid, img, ts))
        if fresh:  # already marked seen above; let the newest one through
            for _, _, uid, _, _ in new_items[-len(items):] if items else []:
                seen_set.discard(uid)

    # oldest first, so the channel reads in order
    new_items.sort(key=lambda x: x[4])

    if first_run:
        to_post = new_items[-FIRST_RUN_POSTS:]
        for _, _, uid, _, _ in new_items:
            seen.append(uid)
            seen_set.add(uid)
    else:
        to_post = new_items[:MAX_POSTS_PER_RUN]  # oldest first, rest next time

    recent = load_recent()
    for source, e, uid, img, _ in to_post:
        link = e.get("link", "").replace("http://", "https://", 1)
        retries = _state_get("retry", {})
        post = build_post(source, e, link, recent, force=retries.get(uid, 0) >= 2)
        if post.get("retry"):
            retries[uid] = retries.get(uid, 0) + 1
            _state_set("retry", {k: v for k, v in retries.items() if k not in seen_set})
            print(f"Site not responding, will retry next check ({retries[uid]}/3): {link}")
            continue
        title = post["title"]

        if post.get("foreign"):
            print(f"Skipped foreign news ({source}): {post['orig'][:100]}")
            if uid not in seen_set:
                seen.append(uid)
                seen_set.add(uid)
            continue
        if post["dup"]:
            print(f"Skipped duplicate ({source}): {post['orig'][:90]} == "
                  f"{recent[post['dup'] - 1]['title'][:90]}")
            if uid not in seen_set:
                seen.append(uid)
                seen_set.add(uid)
            continue

        # Keep at least MIN_POST_GAP between posts (also across checks/runs)
        last = max((x.get("t", 0) for x in recent), default=0)
        wait = last + MIN_POST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            photo = item_image(e, (img, None)) or post["image"]
            if photo:  # some sites give a relative picture path
                photo = urljoin(link, html.unescape(photo))
            send(post["text"], photo=photo, referer=link)
            print(f"Posted ({source}): {title}")
        except Exception as ex:
            print(f"Failed to post {link}: {ex}")
            continue
        if uid not in seen_set:
            seen.append(uid)
            seen_set.add(uid)
        recent.append({"t": time.time(), "title": title, "source": source,
                       "orig": post["orig"], "summary": post["summary"][:600]})
        recent = recent[-RECENT_MAX:]
        save_seen(seen)      # save after every post, so a cut-off run can't repost
        save_recent(recent)

    save_seen(seen)
    save_recent(recent)
    if _llm_state["calls"]:
        print(f"LLM usage this run: {_llm_state['calls']} requests, "
              f"{_llm_state['tokens']} tokens")


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def self_test():
    """Check translation and summary on the latest article without posting."""
    import sys
    print(f"Python {sys.version.split()[0]}; LLM_API_KEY set: {bool(LLM_KEY)}; "
          f"GIGACHAT_AUTH_KEY set: {bool(GIGA_KEY)}; summary models: {SUMMARY_MODELS}")
    print("== Translation (Google, then MyMemory)")
    print(translate("Le service de bus est perturbé ce mardi matin"))
    print("== LLM:", LLM_URL)
    for model in LLM_MODELS:
        try:
            r = _llm_call(model, [{"role": "user", "content": "Reply with: ok"}], 200)
            print(model, r.status_code, r.text[:300])
        except Exception as e:
            print(model, "FAIL:", type(e).__name__, e)
    try:
        seen = set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        seen = set()
    for src in FEEDS + TEST_FEEDS:
        source = src[0]
        print(f"== {source}: {src[1]}")
        try:
            e, link, img = newest_entry(src)
        except Exception as ex:
            print("FAIL:", type(ex).__name__, ex)
            continue
        if not e:
            print("no items")
            continue
        body, page_image = article_text(link)
        print("Newest:", e.get("title"), "|", link)
        print(f"Article text: {len(body)} chars | photo: {img or page_image}")
        post = build_post(source, e, link, load_recent())
        text, dup = post["text"], post["dup"]
        print("Duplicate of recent post #:", dup)
        print(f"LLM usage so far: {_llm_state['calls']} requests, {_llm_state['tokens']} tokens")
        print("-- Post that would be sent:")
        print(text)


def compare_models():
    """Run the newest article of each feed through every model, print results."""
    for src in FEEDS:
        source = src[0]
        e, link, _ = newest_entry(src)
        if not e:
            continue
        body, _ = article_text(link)
        if len(body) < MIN_TEXT_FOR_SUMMARY and e.get("content"):
            body = _strip_html(e["content"][0].get("value", ""))
        print(f"\n######## {source}: {e.get('title')}\n{link}")
        content = f"Заголовок: {e.get('title')}\n\nТекст статьи:\n{body[:ARTICLE_CHARS]}"
        for model in SUMMARY_MODELS:
            t0 = time.time()
            try:
                r = _llm_call(model, [{"role": "system", "content": PROMPT},
                                      {"role": "user", "content": content}])
                if r.status_code == 429:
                    time.sleep(min(float(r.headers.get("retry-after", "20") or 20), 60) + 1)
                    r = _llm_call(model, [{"role": "system", "content": PROMPT},
                                          {"role": "user", "content": content}])
                data = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
                print(f"\n--- {model} ({time.time() - t0:.1f}s)")
                print("Заголовок:", data.get("title"))
                print("Пересказ:", data.get("summary"))
            except Exception as ex:
                print(f"\n--- {model}: FAIL {ex} {getattr(r, 'text', '')[:200]}")


def dup_eval():
    """Judge duplicates with each model on real recent articles (post nothing)."""
    pool = []
    for src in FEEDS:
        kind = src[2] if len(src) > 2 else "rss"
        try:
            items, _ = collect(src[0], src[1], kind, src[3] if len(src) > 3 else None, set())
        except Exception as ex:
            print("skip", src[0], ex)
            continue
        if kind == "html":
            got = []
            for _, uid, _, _ in items[:8]:
                e = page_entry(uid)
                if e:
                    got.append((e, uid, None, e["_ts"]))
            items = got
        for e, uid, img, ts in sorted(items, key=lambda x: -x[3])[:8]:
            pool.append((ts, src[0], e))
    pool.sort(key=lambda x: x[0])
    pool = pool[-40:]
    published = [{"title": e.get("title", ""), "orig": e.get("title", ""), "summary": ""}
                 for _, _, e in pool[:25]]
    print("Already published:")
    for i, x in enumerate(published, 1):
        print(f"  {i}. {x['orig'][:100]}")
    for ts, source, e in pool[25:]:
        link = e.get("link", "").replace("http://", "https://", 1)
        body, _ = article_text(link)
        lead = re.sub(r"^(Publicité\s*)+", "", body).strip()
        print(f"\n## {source}: {e.get('title', '')[:110]}")
        for m in [x for x in DUP_MODELS if x != "openai/gpt-oss-20b"]:
            DUP_MODELS_SAVED = list(DUP_MODELS)
            DUP_MODELS[:] = [m]
            _llm_state["exhausted"].discard(m)
            d = find_duplicate(e.get("title", ""), lead, published)
            DUP_MODELS[:] = DUP_MODELS_SAVED
            print(f"   {m:22s} -> {'DUP of #%d: %s' % (d, published[d-1]['orig'][:70]) if d else 'new'}")


if __name__ == "__main__":
    import sys
    if "--dupeval" in sys.argv:
        dup_eval()
    elif "--compare" in sys.argv:
        compare_models()
    elif "--test" in sys.argv:
        self_test()
    else:
        main()
