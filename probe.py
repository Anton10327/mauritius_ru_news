"""Writer + proofreader on the road-safety article (posts nothing)."""
import bot
u = "https://lexpress.mu/s/" 
import json
seen = json.load(open(bot.STATE_DIR / "seen.json", encoding="utf-8"))
for uid in reversed(seen[-300:]):
    if "lexpress" in uid and ("dos-d-ane" in uid or "ralentisseur" in uid or "securite-routiere" in uid):
        e = bot.page_entry(uid); p = bot.build_post("L'Express", e, uid, [])
        print(p.get("title")); print(p.get("summary")); break
else:
    print("article not found")
