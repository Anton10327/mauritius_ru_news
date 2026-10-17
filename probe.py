"""Side-by-side: Qwen vs GigaChat models on the same newest articles (posts nothing)."""
import os, uuid, time, requests, certifi, tempfile, bot

KEY = os.environ["GIGACHAT_AUTH_KEY"].strip()
b = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
b.write(open(certifi.where(), "rb").read())
for u in ["https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
          "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"]:
    b.write(b"\n" + requests.get(u, timeout=30).content)
b.close(); V = b.name
tok = requests.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                    headers={"Authorization": f"Basic {KEY}", "RqUID": str(uuid.uuid4()),
                             "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                    data={"scope": "GIGACHAT_API_PERS"}, timeout=30, verify=V).json()["access_token"]
used = {}
def giga(model, messages):
    r = requests.post("https://api.giga.chat/v1/chat/completions", verify=V, timeout=120,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
                      json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 700})
    j = r.json(); used[model] = used.get(model, 0) + j.get("usage", {}).get("total_tokens", 0)
    return j["choices"][0]["message"]["content"]

MODELS = ["qwen/qwen3.8-27b", "GigaChat-2", "GigaChat-3-Lightning", "GigaChat-2-Pro", "GigaChat-3-Pro", "GigaChat-2-Max", "GigaChat-3-Ultra"]
done = 0
for src in bot.FEEDS:
    if done >= 4: break
    try:
        e, link, _ = bot.newest_entry(src)
    except Exception as ex:
        print("skip", src[0], ex); continue
    if not e: continue
    body, _ = bot.article_text(link)
    if len(body) < 400: continue
    done += 1
    print(f"\n######## {src[0]}: {e.get('title')}\n{link}")
    msgs = [{"role": "system", "content": bot.PROMPT},
            {"role": "user", "content": f"Заголовок: {e.get('title')}\n\nТекст статьи:\n{body[:bot.ARTICLE_CHARS]}"}]
    for m in MODELS:
        t0 = time.time()
        try:
            if m.startswith("GigaChat"):
                raw = giga(m, msgs)
            else:
                r = bot._llm_call(m, msgs)
                if r.status_code == 429: time.sleep(30); r = bot._llm_call(m, msgs)
                raw = r.json()["choices"][0]["message"]["content"]
            d = bot._parse_json(raw) or {}
            print(f"\n--- {m} ({time.time()-t0:.1f}s)\nЗаголовок: {d.get('title')}\nПересказ: {d.get('summary')}")
        except Exception as ex:
            print(f"\n--- {m}: FAIL {type(ex).__name__} {str(ex)[:150]}")
print("\nGigaChat tokens used:", used)
