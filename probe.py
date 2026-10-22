"""Names-list approach on real recent articles (posts nothing)."""
import json, re, requests, bot
bot.SUMMARY_MODELS[:] = ["GigaChat-2-Max"]
bot.DUP_MODELS[:] = []
seen = json.load(open(bot.STATE_DIR / "seen.json", encoding="utf-8"))
urls = []
for uid in reversed(seen):
    if uid.startswith("wp:Le Mauricien:"):
        pid = uid.rsplit(":", 1)[1]
        try:
            d = requests.get(f"https://www.lemauricien.com/wp-json/wp/v2/posts/{pid}?_fields=link,title",
                             headers=bot.UA, timeout=20).json()
        except Exception:
            continue
        if "BOXE" in d.get("title", {}).get("rendered", "") or len([u for u in urls if "lemauricien" in u]) < 3:
            urls.append(d["link"])
    elif "inside.news/?p=" not in uid:
        urls.append(uid.replace("http://", "https://"))
    if len(urls) >= 8:
        break
src = lambda u: next((s[0] for s in bot.FEEDS if s[0].split()[0].lower().replace("'", "") in u.replace("-", "")), "Source")
for u in urls:
    e = bot.page_entry(u)
    if not e or not e["title"]:
        continue
    post = bot.build_post(src(u), e, u, [])
    print("\n=== ", e["title"][:100])
    print("names:", "; ".join(f"{n['orig']}{'→' + n['ru'] if n.get('ru') else ''} ({n.get('type')})"
                              for n in bot._llm_state.get("names") or []))
    print(post.get("title"), "\n", post.get("summary"))
print(f"\nLLM {bot._llm_state['calls']} calls, {bot._llm_state['tokens']} tokens")
