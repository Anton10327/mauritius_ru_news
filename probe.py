"""Full post pipeline on fresh articles with GigaChat (posts nothing)."""
import bot
n = 0
for src in bot.FEEDS:
    if n >= 3: break
    e, link, img = bot.newest_entry(src)
    if not e: continue
    post = bot.build_post(src[0], e, link, [])
    if post.get("foreign") or not post["text"]: continue
    n += 1
    print(f"\n## {src[0]} — written by: {bot._llm_state['model']}\n{post['text']}")
print("\nGigaChat used today:", bot._state_get("giga_used"))
