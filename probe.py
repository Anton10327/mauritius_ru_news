"""Print the article about the Sainte-Croix bail hearing (posts nothing)."""
import json, re, bot
seen = json.load(open(bot.STATE_DIR / "seen.json", encoding="utf-8"))
for uid in [u for u in seen if "defimedia" in u][-60:]:
    body, _ = bot.article_text(uid.replace("http://", "https://"))
    if "Toorbuth" in body:
        print(uid); print(body[:2500]); break
