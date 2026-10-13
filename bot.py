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

import feedparser
import requests
import trafilatura
from deep_translator import GoogleTranslator

# --- Settings -------------------------------------------------------------

# Add more RSS feeds here: ("Source name", "feed URL")
FEEDS = [
    ("Defimedia", "https://defimedia.info/rss.xml"),
    ("ION News", "https://ionnews.mu/feed/"),
]

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]   # from @BotFather
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # e.g. @my_mauritius_news
# Optional: your personal Telegram id for service alerts (from @userinfobot).
# You must press Start in your bot once, or Telegram won't let it write to you.
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "").strip()

SEEN_FILE = Path("seen.json")
STATE_FILE = Path("state.json")  # remembers which alerts were sent today
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
              "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"
              ).split(",") if m.strip()]
ARTICLE_CHARS = 4000     # article text sent to the model (keeps within free limits)
MIN_TEXT_FOR_SUMMARY = 300  # less text than this -> no summary, title only

PROMPT = (
    "Ты редактор русскоязычного новостного канала о Маврикии. "
    "Ниже статья с маврикийского сайта (обычно на французском, "
    "цитаты бывают на креольском). Верни строго JSON вида "
    '{"title": "...", "summary": "..."}. '
    "title — точный перевод заголовка на русский. "
    "summary — пересказ своими словами на русском в 2–4 предложениях, "
    "до 600 символов: что произошло, где, кто, что дальше. "
    "Только факты из статьи, без оценок и без выдумок. "
    "Местные названия (Port-Louis, Curepipe) и имена пиши как в оригинале "
    "латиницей; рупии — «рупий»."
)

# --------------------------------------------------------------------------

translator = GoogleTranslator(source="auto", target="ru")
_llm_state = {"disabled": False, "model": None}


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
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 news-bot"})
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


def _llm_call(model, messages, max_tokens=1500):
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
    models = [_llm_state["model"]] if _llm_state["model"] else LLM_MODELS
    for model in models:
        for attempt in range(2):
            try:
                r = _llm_call(model, messages)
            except Exception as e:
                print(f"LLM request failed: {e}")
                return None
            if r.status_code == 429:
                wait = float(r.headers.get("retry-after", "0") or 0)
                if attempt == 0 and 0 < wait <= 60:
                    print(f"LLM per-minute limit, waiting {wait:.0f}s")
                    time.sleep(wait + 1)
                    continue
                print(f"LLM limit reached: {r.text[:200]}")
                _llm_state["disabled"] = True
                alert("limit",
                      "⚠️ Закончился бесплатный лимит нейросети (Groq).\n\n"
                      "До его сброса бот публикует в канал только переведённые "
                      "заголовки, без пересказов. Обычно лимит восстанавливается "
                      "в течение суток, ничего делать не нужно.")
                return None
            break
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


def build_post(source, entry, link):
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
    if result:
        title, summary = result
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
        return f"{head}\n\n{html.escape(summary)}\n\n{footer}", title, page_image
    return f"{head}\n\n{footer}", title, page_image


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


def main():
    seen = load_seen()
    first_run = seen is None
    seen = seen or []
    seen_set = set(seen)

    new_items = []
    for source, url in FEEDS:
        try:
            xml = requests.get(url, timeout=30,
                               headers={"User-Agent": "Mozilla/5.0 news-bot"}).text
        except Exception as ex:
            print(f"Could not fetch {url}: {ex}")
            continue
        extras = raw_items(xml)
        entries = feedparser.parse(xml).entries
        uids = [e.get("id") or e.get("link") for e in entries]
        # A feed we have never seen before (just added to FEEDS): remember all
        # its current items and post only the newest one, not the whole backlog.
        fresh_feed = not first_run and entries and not any(u in seen_set for u in uids)
        feed_items = []
        for e, uid in zip(entries, uids):
            if uid and uid not in seen_set:
                img, ts = extras.get(uid) or extras.get(e.get("link")) or (None, 0.0)
                if not ts and e.get("published_parsed"):
                    ts = time.mktime(e.published_parsed)
                feed_items.append((source, e, uid, img, ts))
        if fresh_feed:
            feed_items.sort(key=lambda x: x[4])
            for item in feed_items[:-1]:
                seen.append(item[2])
                seen_set.add(item[2])
            feed_items = feed_items[-1:]
            print(f"New feed {source}: skipped backlog, posting newest only")
        new_items.extend(feed_items)

    # oldest first, so the channel reads in order
    new_items.sort(key=lambda x: x[4])

    if first_run:
        to_post = new_items[-FIRST_RUN_POSTS:]
        for _, _, uid, _, _ in new_items:
            seen.append(uid)
            seen_set.add(uid)
    else:
        to_post = new_items[-MAX_POSTS_PER_RUN:]

    for source, e, uid, img, _ in to_post:
        link = e.get("link", "").replace("http://", "https://", 1)
        text, title, page_image = build_post(source, e, link)

        try:
            send(text, photo=item_image(e, (img, None)) or page_image)
            print(f"Posted: {title}")
        except Exception as ex:
            print(f"Failed to post {link}: {ex}")
            continue
        if uid not in seen_set:
            seen.append(uid)
            seen_set.add(uid)
        time.sleep(3)  # Telegram limit: ~20 messages/minute per channel

    save_seen(seen)


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
    for source, url in FEEDS:
        print(f"== {source}: {url}")
        xml = requests.get(url, timeout=30,
                           headers={"User-Agent": "Mozilla/5.0 news-bot"}).text
        entries = feedparser.parse(xml).entries
        print(f"Feed items: {len(entries)}; newest 4:")
        for x in entries[:4]:
            uid = x.get("id") or x.get("link")
            print(" ", "SEEN" if uid in seen else "NEW ", uid, "|", x.get("title", "")[:70])
        if not entries:
            continue
        e = entries[0]
        link = e.get("link", "").replace("http://", "https://", 1)
        img = raw_items(xml).get(e.get("id") or link, (None, 0))[0]
        text, _, page_image = build_post(source, e, link)
        print("Photo:", item_image(e, (img, None)) or page_image)
        print("-- Post that would be sent:")
        print(text)

if __name__ == "__main__":
    import sys
    self_test() if "--test" in sys.argv else main()
