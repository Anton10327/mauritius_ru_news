"""Run the bot's proofreading pass on recent posts (not used by the bot, posts nothing)."""
import json
import bot
posts = json.load(open("recent.json"))[-15:]
posts.append({"title": "Дело о чемоданах с 114 миллионами рупий: Кобита Жогно прошла третье допрос в FCC",
              "summary": "14 октября Кобита Жогно явилась в Финансовую комиссию по расследованию преступлений (FCC) в Реде для третьего допроса по делу о чемоданах."})
for p in posts:
    print(f"\n- {p['title'][:100]}")
    t, s = bot.proofread(p["title"], p.get("summary", ""))
    if t != p["title"]:
        print("   NEW TITLE:", t)
    if s != p.get("summary", ""):
        print("   NEW TEXT: ", s[:200])
print(f"\nLLM usage: {bot._llm_state['calls']} requests, {bot._llm_state['tokens']} tokens")
