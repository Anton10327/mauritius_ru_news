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

SEEN_FILE = Path("seen.json")
STATE_FILE = Path("state.json")  # remembers which alerts were sent today
RECENT_FILE = Path("recent.json")  # recently posted headlines, for duplicate checks
RECENT_HOURS = 36                  # compare new articles with posts from this period
RECENT_MAX = 50
MAX_SEEN = 2000          # how many item IDs to remember
FIRST_RUN_POSTS = 3      # on the very first run, post only the latest N items
MAX_POSTS_PER_RUN = 20   # safety limit against flooding the channel

# Free LLM via Groq (OpenAI-compatible API). Get a free key at
# console.groq.com -> API Keys, and save it as the LLM_API_KEY secret.
# Models are tried in order until one works.
LLM_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_URL = os.environ.get("LLM_URL", "").strip() or \
    "https://api.groq.com/openai/v1/chat/completions"
LLM_MODELS = [m.strip() for m in (os.environ.get("LLM_MODELS", "").strip() or
              "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"
              ).split(",") if m.strip()]
ARTICLE_CHARS = 4000     # article text sent to the model (keeps within free limits)
MIN_TEXT_FOR_SUMMARY = 300  # less text than this -> no summary, title only

PROMPT = (
    "Ты редактор русскоязычного новостного канала о Маврикии. "
    "Ниже статья с маврикийского сайта на французском; в ней бывают цитаты "
    "на маврикийском креольском (например: «Li guet nou», «Nou pou res», "
    "«monn trouv lamor»). Верни строго JSON вида "
    '{"title": "...", "summary": "..."}. '
    "title — полный перевод заголовка на русский. Цитаты в кавычках тоже "
    "переводи на русский по смыслу, сохраняя кавычки; ни одного слова на "
    "французском или креольском в title остаться не должно. "
    "summary — пересказ своими словами на русском в 2–4 предложениях, "
    "до 600 символов: что произошло, где, кто, что дальше; ключевые цифры "
    "(число пострадавших, арестованных, суммы, даты) сохраняй. "
    "Только факты из статьи, без оценок и без выдумок; если в статье мало "
    "информации, пиши короче, но ничего не додумывай. "
    "Названия мест (Port-Louis, Sainte-Croix, Camp-Levieux) оставляй латиницей. "
    "Имена людей пиши по-русски в общепринятой транскрипции (Navin Ramgoolam — "
    "Навин Рамгулам). Рупии — «рупий»."
)

DUP_PROMPT = (
    "Ты помогаешь вести новостной канал и убираешь повторы. Дана новая новость "
    "и пронумерованный список заголовков, уже опубликованных в канале. "
    "Дубль — только если новая новость сообщает ТОТ ЖЕ факт, что уже был "
    "опубликован, и не добавляет ничего существенного: то же событие, то же "
    "решение, то же заявление того же человека, просто в пересказе другого СМИ. "
    "НЕ дубль, если есть хоть что-то новое: новое или изменённое решение "
    "(например, экзамены сначала перенесли, а теперь их проведут), новое "
    "событие в той же истории (было «арестовали», стало «предъявили "
    "обвинение»; было «созван кабинет», стало «премьер обратится к нации»), "
    "новые цифры, новое место, заявление другого человека. Общая тема — "
    "не повод считать дублем. Если сомневаешься — это НЕ дубль. "
    "Ответь строго JSON в таком порядке полей: "
    '{"reason": "кратко: что нового или что повторяется", '
    '"is_duplicate": true или false, "duplicate_of": номер или null}.'
)

# Extra feeds checked only in test mode (to try a source before adding it)
TEST_FEEDS = []

# --------------------------------------------------------------------------

translator = GoogleTranslator(source="auto", target="ru")
_llm_state = {"disabled": False, "model": None, "exhausted": set(), "tokens": 0, "calls": 0}


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return None  # None = first run


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


def article_text(url):
    """Download the article page; return (main text, og:image URL or None)."""
    try:
        r = requests.get(url, timeout=30, headers=UA)
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False,
                                   include_tables=False) or ""
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text))
        image = html.unescape(m.group(1)) if m else None
        return text.strip(), image
    except Exception as e:
        print(f"Could not read article {url}: {e}")
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


def _llm_post(model, messages, max_tokens):
    return requests.post(
        LLM_URL,
        headers={"Authorization": f"Bearer {LLM_KEY}",
                 "Content-Type": "application/json"},
        json={"model": model, "messages": messages,
              "temperature": 0.2, "max_tokens": max_tokens},
        timeout=90,
    )


def _parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def summarize(title, body):
    """Ask the LLM for a Russian title + summary. Returns (title, summary)
    or None if the model is unavailable (no key, daily limit, error)."""
    if _llm_state["disabled"] or not LLM_KEY:
        return None
    content = f"Заголовок: {title}\n\nТекст статьи:\n{body[:ARTICLE_CHARS] or '(текст недоступен)'}"
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": content}]
    models = [m for m in LLM_MODELS if m not in _llm_state["exhausted"]]
    if _llm_state["model"] in models:  # keep using the model that works
        models.remove(_llm_state["model"])
        models.insert(0, _llm_state["model"])
    for model in models:
        exhausted = False
        for attempt in range(2):
            try:
                r = _llm_call(model, messages)
            except Exception as e:
                print(f"LLM request failed: {e}")
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
        if r.status_code in (401, 403):
            print(f"LLM key rejected: {r.text[:200]}")
            _llm_state["disabled"] = True
            alert("key", "⚠️ Groq не принимает ключ LLM_API_KEY. Проверьте "
                  "секрет в настройках репозитория. Пока публикуются только "
                  "заголовки.")
            return None
        if r.status_code in (400, 404) and "model" in r.text.lower():
            print(f"Model {model} unavailable ({r.text[:150]}), trying next")
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
        return ru_title, ru_summary
    _llm_state["disabled"] = True
    if set(LLM_MODELS) <= _llm_state["exhausted"]:
        alert("limit",
              "⚠️ Закончился бесплатный дневной лимит нейросети (Groq) у всех "
              "моделей.\n\nДо сброса бот публикует только переведённые "
              "заголовки, без пересказов. Лимит восстанавливается сам, "
              "ничего делать не нужно.")
        return None
    alert("models",
          "⚠️ Ни одна модель из LLM_MODELS не ответила — похоже, Groq их "
          "переименовал или убрал.\n\nБот публикует только переведённые "
          "заголовки. Актуальные модели: console.groq.com/docs/models")
    return None


def alert(kind, text):
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


def find_duplicate(title, summary, recent_titles):
    """Number (1-based) of the recent post this news repeats, or None."""
    if not recent_titles or not LLM_KEY or _llm_state["disabled"]:
        return None
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(recent_titles, 1))
    msg = (f"Новая новость:\n{title}\n{summary}\n\n"
           f"Уже опубликованы:\n{listing}")
    free = [m for m in LLM_MODELS if m not in _llm_state["exhausted"]]
    if not free:
        return None
    model = _llm_state["model"] if _llm_state["model"] in free else free[0]
    try:
        r = _llm_call(model, [{"role": "system", "content": DUP_PROMPT},
                              {"role": "user", "content": msg}], 300)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", "0") or 0)
            if not 0 < wait <= 60:
                print(f"Duplicate check skipped, limit reached for {model}")
                _llm_state["exhausted"].add(model)
                return None
            time.sleep(wait + 1)
            r = _llm_call(model, [{"role": "system", "content": DUP_PROMPT},
                                  {"role": "user", "content": msg}], 300)
        if not r.ok:
            print(f"Duplicate check failed {r.status_code}: {r.text[:150]}")
            return None
        data = _parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        if data.get("is_duplicate") is not True:
            return None
        dup = data.get("duplicate_of")
        dup = int(dup) if dup not in (None, "", "null", False) else None
        if dup is not None and 1 <= dup <= len(recent_titles):
            print(f"  dup reason: {data.get('reason', '')}")
            return dup
    except Exception as e:
        print(f"Duplicate check failed: {e}")
    return None


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


def build_post(source, entry, link, recent_titles=()):
    """Russian title + summary (LLM) or translated headline (fallback)."""
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
    # Without real article text a "summary" would be invented: title only then
    result = summarize(orig_title, body) if len(body) >= MIN_TEXT_FOR_SUMMARY else None
    if result is None and body and len(body) < MIN_TEXT_FOR_SUMMARY:
        print(f"Too little text ({len(body)} chars), title only: {link}")
    dup = None
    if result:
        title, summary = result
        dup = find_duplicate(title, summary, recent_titles)
    else:
        title = translate(orig_title)
        summary = translate(feed_text[:450]) if feed_text and len(feed_text) > 40 else ""

    footer = f'🔗 <a href="{html.escape(link)}">{html.escape(source)}</a>'
    head = f"<b>{html.escape(title)}</b>"
    # Photo captions are capped at 1024 characters; leave room for markup
    room = 1000 - len(head) - len(footer) - 4
    if summary and summary != title and room > 50:
        if len(summary) > room:
            summary = summary[:room - 1].rsplit(" ", 1)[0] + "…"
        return f"{head}\n\n{html.escape(summary)}\n\n{footer}", title, page_image, dup
    return f"{head}\n\n{footer}", title, page_image, dup


def send(text, photo=None):
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    if photo:
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": CHANNEL_ID, "photo": photo,
                  "caption": text, "parse_mode": "HTML"},
            timeout=30,
        )
        if r.ok:
            return
        # Telegram couldn't fetch the picture itself: download and upload it
        try:
            img = requests.get(photo, timeout=30,
                               headers={"User-Agent": "Mozilla/5.0 news-bot"})
            if img.ok and img.headers.get("content-type", "").startswith("image"):
                r = requests.post(
                    f"{base}/sendPhoto",
                    data={"chat_id": CHANNEL_ID, "caption": text,
                          "parse_mode": "HTML"},
                    files={"photo": ("photo.jpg", img.content)},
                    timeout=60,
                )
                if r.ok:
                    return
        except Exception as e:
            print(f"Photo upload failed: {e}")
        print(f"Photo failed ({r.text[:200]}), sending text only")
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
        to_post = new_items[-MAX_POSTS_PER_RUN:]

    recent = load_recent()
    for source, e, uid, img, _ in to_post:
        link = e.get("link", "").replace("http://", "https://", 1)
        recent_titles = [x["title"] for x in recent]
        text, title, page_image, dup = build_post(source, e, link, recent_titles)

        if dup:
            print(f"Skipped duplicate ({source}): {title} == {recent_titles[dup - 1]}")
            if uid not in seen_set:
                seen.append(uid)
                seen_set.add(uid)
            continue

        try:
            send(text, photo=item_image(e, (img, None)) or page_image)
            print(f"Posted: {title}")
        except Exception as ex:
            print(f"Failed to post {link}: {ex}")
            continue
        if uid not in seen_set:
            seen.append(uid)
            seen_set.add(uid)
        recent.append({"t": time.time(), "title": title, "source": source})
        time.sleep(3)  # Telegram limit: ~20 messages/minute per channel

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
    print(f"Python {sys.version.split()[0]}; LLM_API_KEY set: {bool(LLM_KEY)}")
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
        text, _, page_image, dup = build_post(source, e, link,
                                              [x["title"] for x in load_recent()])
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
        for model in LLM_MODELS:
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


if __name__ == "__main__":
    import sys
    if "--compare" in sys.argv:
        compare_models()
    elif "--test" in sys.argv:
        self_test()
    else:
        main()
