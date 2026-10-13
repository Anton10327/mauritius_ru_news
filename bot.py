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

# Free LLM via GitHub Models (uses the built-in GITHUB_TOKEN in Actions).
# Low-tier models: ~150 free requests/day. Tried in order until one works.
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
LLM_URL = "https://models.github.ai/inference/chat/completions"
LLM_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"]
ARTICLE_CHARS = 6000     # how much article text to send (fits the 8k-token limit)

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
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return translator.translate(text[:4500])
    except Exception as e:
        print(f"Translation failed, keeping original: {e}")
        return text


def article_text(url):
    """Download the article page and pull out the main text."""
    try:
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 news-bot"})
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False,
                                   include_tables=False) or ""
        return text.strip()
    except Exception as e:
        print(f"Could not read article {url}: {e}")
        return ""


def summarize(title, body):
    """Ask GitHub Models for a Russian title + summary. Returns (title, summary)
    or None if the model is unavailable (no token, daily limit, error)."""
    if _llm_state["disabled"] or not GH_TOKEN:
        return None
    content = f"Заголовок: {title}\n\nТекст статьи:\n{body[:ARTICLE_CHARS] or '(текст недоступен)'}"
    models = [_llm_state["model"]] if _llm_state["model"] else LLM_MODELS
    for model in models:
        try:
            r = requests.post(
                LLM_URL,
                headers={"Authorization": f"Bearer {GH_TOKEN}",
                         "Content-Type": "application/json",
                         "Accept": "application/vnd.github+json"},
                json={"model": model,
                      "messages": [{"role": "system", "content": PROMPT},
                                   {"role": "user", "content": content}],
                      "temperature": 0.2,
                      "max_tokens": 600,
                      "response_format": {"type": "json_object"}},
                timeout=60,
            )
        except Exception as e:
            print(f"LLM request failed: {e}")
            return None
        if r.status_code == 429:
            print("GitHub Models limit reached - headlines only until it resets")
            _llm_state["disabled"] = True
            alert("limit",
                  "⚠️ Закончился бесплатный лимит GitHub Models.\n\n"
                  "До его сброса бот публикует в канал только переведённые "
                  "заголовки, без пересказов. Обычно лимит восстанавливается "
                  "в течение суток, ничего делать не нужно.")
            return None
        if r.status_code in (400, 404) and "model" in r.text.lower():
            print(f"Model {model} unavailable, trying next")
            continue
        if not r.ok:
            print(f"LLM error {r.status_code}: {r.text[:300]}")
            return None
        try:
            raw = r.json()["choices"][0]["message"]["content"]
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
            data = json.loads(raw)
            ru_title = (data.get("title") or "").strip()
            ru_summary = (data.get("summary") or "").strip()
        except Exception as e:
            print(f"Bad LLM answer: {e}")
            return None
        if not ru_title:
            return None
        _llm_state["model"] = model
        return ru_title, ru_summary
    _llm_state["disabled"] = True
    alert("models",
          "⚠️ Ни одна модель из LLM_MODELS не доступна в GitHub Models — "
          "похоже, их переименовали или убрали.\n\n"
          "Бот публикует только переведённые заголовки. Замените LLM_MODELS "
          "в bot.py на актуальную модель уровня Low с "
          "github.com/marketplace/models")
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
    result = summarize(orig_title, article_text(link))
    if result:
        title, summary = result
    else:
        title = translate(orig_title)
        summary = translate(_strip_html(entry.get("summary", "")))

    footer = f'🔗 <a href="{html.escape(link)}">{html.escape(source)}</a>'
    head = f"<b>{html.escape(title)}</b>"
    # Photo captions are capped at 1024 characters; leave room for markup
    room = 1000 - len(head) - len(footer) - 4
    if summary and summary != title and room > 50:
        if len(summary) > room:
            summary = summary[:room - 1].rsplit(" ", 1)[0] + "…"
        return f"{head}\n\n{html.escape(summary)}\n\n{footer}", title
    return f"{head}\n\n{footer}", title


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
        for e in feedparser.parse(xml).entries:
            uid = e.get("id") or e.get("link")
            if uid and uid not in seen_set:
                img, ts = extras.get(uid) or extras.get(e.get("link")) or (None, 0.0)
                if not ts and e.get("published_parsed"):
                    ts = time.mktime(e.published_parsed)
                new_items.append((source, e, uid, img, ts))

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
        text, title = build_post(source, e, link)

        try:
            send(text, photo=item_image(e, (img, None)))
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


if __name__ == "__main__":
    main()
