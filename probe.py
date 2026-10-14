"""Proofreading experiment on recent posts (not used by the bot, posts nothing)."""
import json, time, requests
import bot

EDIT_PROMPT = (
    "Ты корректор русскоязычного новостного канала. Исправь в тексте только "
    "ошибки: орфографию, грамматику (согласование родов, чисел и падежей, "
    "управление), пунктуацию, неестественные кальки с французского и явно "
    "неудачные слова. Не меняй смысл, факты, цифры, даты, имена и названия; "
    "не добавляй и не убирай информацию; не переписывай то, что и так верно. "
    "Верни строго JSON: {\"title\": \"...\", \"summary\": \"...\", "
    "\"changes\": [\"было → стало\", ...]}."
)

posts = json.load(open("recent.json"))[-12:]
posts.append({"title": "Дело о чемоданах с 114 миллионами рупий: Кобита Жогно прошла третье допрос в FCC",
              "summary": "14 октября Кобита Жогно явилась в Финансовую комиссию по расследованию преступлений (FCC) в Реде для третьего допроса по делу о чемоданах."})

print("== Reachability from GitHub")
for name, url in [("GigaChat OAuth", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"),
                  ("GigaChat API", "https://gigachat.devices.sberbank.ru/api/v1/models"),
                  ("YandexGPT", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")]:
    try:
        r = requests.post(url, timeout=20, verify=False)
        print(f"  {name}: HTTP {r.status_code} {r.text[:80]!r}")
    except Exception as e:
        print(f"  {name}: {type(e).__name__} {str(e)[:120]}")

for model in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]:
    print(f"\n######## Editor: {model}")
    for p in posts:
        text = f"Заголовок: {p['title']}\nТекст: {p.get('summary', '')}"
        for attempt in range(3):
            r = bot._llm_call(model, [{"role": "system", "content": EDIT_PROMPT},
                                      {"role": "user", "content": text}], 900)
            if r.status_code != 429:
                break
            time.sleep(20)
        try:
            d = bot._parse_json(r.json()["choices"][0]["message"]["content"]) or {}
        except Exception as e:
            print("  FAIL", r.status_code, r.text[:150]); continue
        ch = d.get("changes") or []
        print(f"\n- {p['title'][:90]}")
        print("   changes:", "; ".join(ch) if ch else "—")
        if d.get("title") != p["title"]:
            print("   new title:", d.get("title"))

print("\n######## LanguageTool (ru-RU)")
for p in posts:
    text = p["title"] + ". " + p.get("summary", "")
    try:
        r = requests.post("https://api.languagetool.org/v2/check",
                          data={"text": text, "language": "ru-RU"}, timeout=30)
        m = r.json().get("matches", [])
        found = [f"«{text[x['offset']:x['offset']+x['length']]}» → {[v['value'] for v in x['replacements'][:2]]} ({x['rule']['id']})" for x in m]
        print(f"- {p['title'][:70]}\n   {'; '.join(found) if found else '—'}")
    except Exception as e:
        print("  FAIL", e)
    time.sleep(3.5)
print(f"\nLLM usage: {bot._llm_state['calls']} requests, {bot._llm_state['tokens']} tokens")
